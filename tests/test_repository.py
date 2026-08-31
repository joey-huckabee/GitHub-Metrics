"""Tests for :mod:`github_metrics.collect.repository`."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, cast

import pytest

from github_metrics.client import GitHubClient
from github_metrics.collect.repository import RepoMetaData, get_repository
from github_metrics.errors import (
    GraphQLQueryError,
    RepositoryMovedError,
    RepositoryNotFoundError,
)

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
    "name": ("name",),
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
        "name": "cline",
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
    # The name is verified from the same query, so verification is free.
    assert any(line.strip() == "name" for line in query.splitlines())
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
    metadata = collect(
        _StubClient(payload(owner_login="urllib3", owner_type="Organization", name="urllib3")),
        "urllib3",
        "urllib3",
    )

    assert metadata.is_organization is True
    assert metadata.organization == "urllib3"


@pytest.mark.requirement("L3-MET-013")
def test_a_personally_owned_repository_reports_no_organisation() -> None:
    # Empty is the answer, not a gap: it is the only place a row records that
    # the repository belongs to a person.
    metadata = collect(
        _StubClient(payload(owner_login="torvalds", owner_type="User", name="linux")),
        "torvalds",
        "linux",
    )

    assert metadata.is_organization is False
    assert metadata.organization == ""


@pytest.mark.requirement("L3-MET-013")
def test_a_personally_owned_repository_is_noted(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        collect(_StubClient(payload(owner_type="User")))

    assert "organization column is empty" in caplog.text


@pytest.mark.requirement("L3-MET-013")
def test_an_unknown_owner_type_is_not_treated_as_an_organisation() -> None:
    # Defensive: only the exact string GitHub documents counts.
    metadata = collect(_StubClient(payload(owner_type="")))

    assert metadata.organization == ""


# ---------------------------------------------------------------------------
# Renamed and transferred repositories
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-MET-015")
def test_the_name_is_taken_from_the_api_not_from_the_inventory() -> None:
    """The column is verified rather than echoed.

    A repository name can be stale in the inventory and still work, because
    GitHub redirects a rename exactly as it redirects a transfer.
    """
    metadata = collect(
        _StubClient(payload(name="virtualenv", owner_login="pypa")), "pypa", "virtualenv"
    )

    assert metadata.resolved_name == "virtualenv"
    assert metadata.was_renamed is False


@pytest.mark.requirement("L3-MET-016")
def test_a_renamed_repository_is_refused() -> None:
    """The reference resolves and is still wrong.

    Every number would have been correct, collected against a repository the
    inventory no longer names, with nothing in the output to say so.
    """
    with pytest.raises(RepositoryMovedError, match="renamed to pypa/pyproject-hooks"):
        collect(_StubClient(payload(name="pyproject-hooks", owner_login="pypa")), "pypa", "pep517")


@pytest.mark.requirement("L3-MET-016")
def test_the_new_location_is_reported_so_it_can_be_pasted_in(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with (
        caplog.at_level(logging.WARNING, logger=LOGGER_NAME),
        pytest.raises(RepositoryMovedError),
    ):
        collect(_StubClient(payload(name="pyproject-hooks", owner_login="pypa")), "pypa", "pep517")

    assert "update the inventory to pypa/pyproject-hooks" in caplog.text
    assert "No data collected" in caplog.text


@pytest.mark.requirement("L3-MET-015")
def test_a_case_difference_is_not_a_rename() -> None:
    metadata = collect(
        _StubClient(payload(name="CPython", owner_login="python")), "python", "cpython"
    )

    assert metadata.was_renamed is False
    assert metadata.resolved_name == "CPython"


@pytest.mark.requirement("L3-MET-015")
def test_a_missing_name_falls_back_to_the_inventory() -> None:
    # Defensive: the column has to have an answer either way, since it is what
    # says which repository the row is about.
    broken = payload()
    broken["data"]["repository"]["name"] = None

    assert collect(_StubClient(broken), "cline", "cline").resolved_name == "cline"


@pytest.mark.requirement("L3-MET-014")
def test_a_transferred_repository_is_refused() -> None:
    """The inventory said `tiangolo/fastapi`; GitHub says `fastapi/fastapi`."""
    with pytest.raises(RepositoryMovedError, match="moved to fastapi/fastapi"):
        collect(
            _StubClient(payload(owner_login="fastapi", owner_type="Organization", name="fastapi")),
            "tiangolo",
            "fastapi",
        )


@pytest.mark.requirement("L3-MET-014")
def test_a_transfer_names_where_it_went(caplog: pytest.LogCaptureFixture) -> None:
    with (
        caplog.at_level(logging.WARNING, logger=LOGGER_NAME),
        pytest.raises(RepositoryMovedError),
    ):
        collect(_StubClient(payload(owner_login="fastapi", name="fastapi")), "tiangolo", "fastapi")

    assert "update the inventory to fastapi/fastapi" in caplog.text


@pytest.mark.requirement("L3-MET-014")
def test_a_case_difference_is_not_a_transfer() -> None:
    # GitHub account names are case-insensitive, so PyPA and pypa are the same
    # owner. Refusing the row over a spelling would reject a working reference.
    metadata = collect(
        _StubClient(payload(owner_login="PyPA", name="virtualenv")), "pypa", "virtualenv"
    )

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


# ---------------------------------------------------------------------------
# Log volume
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-LOG-002")
def test_an_unremarkable_repository_is_collected_in_silence(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One line per repository is four hundred lines on a real inventory.

    Nothing here is wrong, so nothing here is worth an operator's attention at
    the default level. The detail is still available; it is just a level down.
    """
    with caplog.at_level(logging.INFO, logger="github_metrics"):
        collect(_StubClient(payload()))

    assert caplog.records == []


@pytest.mark.requirement("L3-LOG-002")
def test_the_detail_is_still_there_at_debug(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        collect(_StubClient(payload()))

    assert "64574 stars" in caplog.text


@pytest.mark.requirement("L3-LOG-002")
def test_something_worth_doubting_is_still_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The rule quietens narration, not diagnosis.
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        collect(_StubClient(payload(issues_enabled=False, closed=0)))

    assert caplog.records


@pytest.mark.requirement("L3-MET-013")
def test_a_disabled_tracker_is_still_reported_here(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        collect(_StubClient(payload(issues_enabled=False, closed=0)))

    assert "issue tracker disabled" in caplog.text
