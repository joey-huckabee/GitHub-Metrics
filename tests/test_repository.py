"""Tests for :mod:`github_metrics.collect.repository`."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, cast

import pytest

from github_metrics.client import GitHubClient
from github_metrics.collect.repository import RepoMetaData, get_repository
from github_metrics.errors import GraphQLQueryError, RepositoryNotFoundError

LOGGER_NAME = "github_metrics.collect.repository"


class _StubClient:
    """Returns a canned GraphQL payload and records the query."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.queries: list[tuple[str, dict[str, Any]]] = []

    def graphql(
        self, query: str, variables: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Mimic `GitHubClient.graphql`."""
        self.queries.append((query, variables))
        return {}, self.payload


OVERRIDABLE: dict[str, tuple[str, ...]] = {
    "owner_login": ("owner", "login"),
    "owner_type": ("owner", "__typename"),
    "stars": ("stargazerCount",),
    "forks": ("forkCount",),
    "closed": ("closedIssues", "totalCount"),
    "open_": ("openIssues", "totalCount"),
    "issues_enabled": ("hasIssuesEnabled",),
    "releases": ("releases", "totalCount"),
    "tags": ("tags", "totalCount"),
}
"""Where each keyword of `payload` lands in the response."""


def payload(**overrides: Any) -> dict[str, Any]:
    """A successful repository response, with the named fields replaced.

    Taking the fields as a mapping rather than as nine parameters keeps the
    call sites unchanged while leaving the argument-count limit in place for
    the code that limit is meant to police.
    """
    repository: dict[str, Any] = {
        "owner": {"login": "cline", "__typename": "Organization"},
        "stargazerCount": 64_574,
        "forkCount": 6_900,
        "createdAt": "2024-07-06T07:28:10Z",
        "updatedAt": "2026-07-12T12:00:00Z",
        "pushedAt": "2026-07-12T11:00:00Z",
        "hasIssuesEnabled": True,
        "closedIssues": {"totalCount": 3_770},
        "openIssues": {"totalCount": 691},
        "releases": {"totalCount": 398},
        "tags": {"totalCount": 717},
    }

    for name, value in overrides.items():
        if name not in OVERRIDABLE:
            raise TypeError(f"payload() got an unexpected keyword argument {name!r}")
        *path, leaf = OVERRIDABLE[name]
        target = repository
        for key in path:
            target = target[key]
        target[leaf] = value

    return {"data": {"repository": repository}}


def collect(stub: _StubClient, owner: str = "cline", repoid: str = "cline") -> RepoMetaData:
    """Run collection against a stub client."""
    return get_repository(cast(GitHubClient, stub), owner, repoid)


# ---------------------------------------------------------------------------
# One query, everything
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-MET-012")
def test_every_scored_value_comes_from_one_query() -> None:
    stub = _StubClient(payload())

    metadata = collect(stub)

    assert len(stub.queries) == 1
    assert metadata.stars == 64_574
    assert metadata.forks == 6_900
    assert metadata.closed_issues == 3_770
    assert metadata.open_issues == 691
    assert metadata.releases == 398
    assert metadata.tags == 717
    assert metadata.issues_enabled is True
    assert metadata.timestamps.created_at == datetime(2024, 7, 6, 7, 28, 10, tzinfo=timezone.utc)


@pytest.mark.requirement("L3-MET-012")
def test_the_query_asks_for_totals_only() -> None:
    stub = _StubClient(payload())

    collect(stub)

    query, variables = stub.queries[0]
    # `nodes` anywhere would make the cost scale with the repository's history
    # instead of staying at one point.
    assert "nodes" not in query
    assert query.count("totalCount") == 4
    assert variables == {"owner": "cline", "name": "cline"}


@pytest.mark.requirement("L3-MET-012")
def test_distinct_versions_is_derived_the_same_way_as_elsewhere() -> None:
    assert collect(_StubClient(payload(releases=398, tags=717))).distinct_versions == 717
    assert collect(_StubClient(payload(releases=0, tags=943))).distinct_versions == 943


# ---------------------------------------------------------------------------
# organization
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-MET-013")
def test_an_organisation_owned_repository_reports_its_organisation() -> None:
    metadata = collect(_StubClient(payload(owner_login="urllib3", owner_type="Organization")))

    assert metadata.is_organization is True
    assert metadata.organization == "urllib3"


@pytest.mark.requirement("L3-MET-013")
def test_a_personally_owned_repository_reports_no_organisation() -> None:
    # Empty is the answer, not a gap: it is the only place a row records that
    # the repository belongs to a person.
    metadata = collect(
        _StubClient(payload(owner_login="torvalds", owner_type="User")), "torvalds", "linux"
    )

    assert metadata.is_organization is False
    assert metadata.organization == ""


@pytest.mark.requirement("L3-MET-013")
def test_a_personally_owned_repository_is_noted(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        collect(_StubClient(payload(owner_type="User")))

    assert "organization column is empty" in caplog.text


@pytest.mark.requirement("L3-MET-013")
def test_an_unknown_owner_type_is_not_treated_as_an_organisation() -> None:
    # Defensive: only the exact string GitHub documents counts.
    metadata = collect(_StubClient(payload(owner_type="")))

    assert metadata.organization == ""


# ---------------------------------------------------------------------------
# Transferred repositories
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-MET-014")
def test_a_transferred_repository_keeps_both_owners() -> None:
    """The inventory said `tiangolo/fastapi`; GitHub says `fastapi/fastapi`.

    GitHub follows the redirect silently, so the entry still resolves. Both
    values are kept: `owner` is what someone has to edit to fix the list,
    `resolved_owner` is where the repository actually lives.
    """
    metadata = collect(
        _StubClient(payload(owner_login="fastapi", owner_type="Organization")),
        "tiangolo",
        "fastapi",
    )

    assert metadata.owner == "tiangolo"
    assert metadata.resolved_owner == "fastapi"
    assert metadata.organization == "fastapi"
    assert metadata.was_transferred is True


@pytest.mark.requirement("L3-MET-014")
def test_a_transfer_is_reported(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        collect(
            _StubClient(payload(owner_login="fastapi")),
            "tiangolo",
            "fastapi",
        )

    assert "now lives at fastapi/fastapi" in caplog.text


@pytest.mark.requirement("L3-MET-014")
def test_a_case_difference_is_not_a_transfer() -> None:
    # GitHub account names are case-insensitive, so PyPA and pypa are the same
    # owner and the row should not claim the repository moved.
    metadata = collect(_StubClient(payload(owner_login="PyPA")), "pypa", "virtualenv")

    assert metadata.was_transferred is False


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-MET-012")
def test_a_null_repository_fails_rather_than_raising_a_key_error() -> None:
    with pytest.raises(RepositoryNotFoundError):
        collect(_StubClient({"data": {"repository": None}}))


@pytest.mark.requirement("L3-MET-012")
def test_an_unparseable_timestamp_fails_loudly() -> None:
    broken = payload()
    broken["data"]["repository"]["createdAt"] = "not-a-date"

    with pytest.raises(GraphQLQueryError, match="could not parse"):
        collect(_StubClient(broken))


@pytest.mark.requirement("L3-MET-012")
def test_a_repository_never_pushed_to_is_handled() -> None:
    never = payload()
    never["data"]["repository"]["pushedAt"] = None

    assert collect(_StubClient(never)).timestamps.pushed_at is None


@pytest.mark.requirement("L3-MET-013")
def test_a_disabled_tracker_is_still_reported_here(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        collect(_StubClient(payload(issues_enabled=False, closed=0)))

    assert "issue tracker disabled" in caplog.text
