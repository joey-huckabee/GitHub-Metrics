"""Tests for closed-issue collection and scoring."""

from __future__ import annotations

import logging
from typing import Any, cast

import pytest

from github_metrics.analysis.closed_issues import (
    CLOSED_ISSUE_BANDS,
    MAX_CLOSED_ISSUE_WEIGHT,
    MIN_CLOSED_ISSUE_WEIGHT,
    describe_bands,
    score_closed_issues,
)
from github_metrics.client import GitHubClient
from github_metrics.collect.closed_issues import get_closed_issues
from github_metrics.errors import GraphQLQueryError, RepositoryNotFoundError


class _StubClient:
    """Returns one canned GraphQL payload, and records what it was asked."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.queries: list[tuple[str, dict[str, Any]]] = []

    def graphql(
        self, query: str, variables: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Mimic `GitHubClient.graphql`."""
        self.queries.append((query, variables))
        return {}, self.payload


def counts_payload(*, closed: int = 0, open_: int = 0, enabled: bool = True) -> dict[str, Any]:
    """A successful repository response."""
    return {
        "data": {
            "repository": {
                "hasIssuesEnabled": enabled,
                "closedIssues": {"totalCount": closed},
                "openIssues": {"totalCount": open_},
            }
        }
    }


def collect(stub: _StubClient, owner: str = "pypa", repoid: str = "virtualenv") -> Any:
    """Run collection against a stub client."""
    return get_closed_issues(cast(GitHubClient, stub), owner, repoid)


# ---------------------------------------------------------------------------
# Scoring bands
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-SCR-001")
@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (0, 0.1),
        (19, 0.1),
        (20, 0.2),
        (49, 0.2),
        (50, 0.3),
        (99, 0.3),
        (100, 0.4),
        (149, 0.4),
        (150, 0.6),
        (299, 0.6),
        (300, 0.8),
        (399, 0.8),
        (400, 0.9),
        (499, 0.9),
        (500, 1.0),
        (501, 1.0),
        (3770, 1.0),
    ],
)
def test_every_band_boundary_scores_as_documented(count: int, expected: float) -> None:
    assert score_closed_issues(count) == expected


@pytest.mark.requirement("L3-SCR-001")
def test_exactly_five_hundred_scores_rather_than_falling_through() -> None:
    # The original chain ended `< 500 -> 0.9` and `> 500 -> 1.0`, so 500 itself
    # matched no branch and returned the initial 0. This is that hole.
    assert score_closed_issues(500) == MAX_CLOSED_ISSUE_WEIGHT


@pytest.mark.requirement("L3-SCR-001")
def test_no_count_is_left_unmapped() -> None:
    # A band that matches nothing returns a plausible number rather than an
    # error, so sweeping the whole domain is what makes that class of defect
    # impossible to reintroduce quietly.
    top = CLOSED_ISSUE_BANDS[-1][0]
    weights = {score_closed_issues(count) for count in range(0, top + 50)}

    assert weights == {0.1, 0.2, 0.3, 0.4, 0.6, 0.8, 0.9, 1.0}
    assert all(
        MIN_CLOSED_ISSUE_WEIGHT <= score_closed_issues(count) <= MAX_CLOSED_ISSUE_WEIGHT
        for count in range(0, top + 50)
    )


@pytest.mark.requirement("L3-SCR-001")
def test_the_score_never_decreases_as_the_count_rises() -> None:
    scores = [score_closed_issues(count) for count in range(0, 600)]

    pairs = zip(scores[:-1], scores[1:], strict=True)
    assert all(earlier <= later for earlier, later in pairs)


@pytest.mark.requirement("L3-SCR-001")
def test_bands_are_ordered_and_have_no_gaps() -> None:
    bounds = [bound for bound, _ in CLOSED_ISSUE_BANDS]
    weights = [weight for _, weight in CLOSED_ISSUE_BANDS]

    assert bounds == sorted(bounds)
    assert len(set(bounds)) == len(bounds)
    assert weights == sorted(weights)


@pytest.mark.requirement("L3-SCR-002")
def test_a_negative_count_is_reported_and_treated_as_zero(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="github_metrics.analysis.closed_issues"):
        weight = score_closed_issues(-5)

    # A count cannot be negative; scoring it silently would hide the caller's bug.
    assert weight == MIN_CLOSED_ISSUE_WEIGHT
    assert "negative" in caplog.text


@pytest.mark.requirement("L3-SCR-002")
def test_scoring_logs_the_band_it_chose(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger="github_metrics.analysis.closed_issues"):
        score_closed_issues(250)

    assert "250" in caplog.text
    assert "0.6" in caplog.text


@pytest.mark.requirement("L3-SCR-001")
def test_the_band_table_renders_for_diagnostics() -> None:
    rendered = describe_bands()

    assert "<20" in rendered
    assert ">=500" in rendered


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-MET-001")
def test_counts_are_read_from_the_response() -> None:
    stub = _StubClient(counts_payload(closed=3770, open_=691))

    counts = collect(stub, "cline", "cline")

    assert counts.closed == 3770
    assert counts.open == 691
    assert counts.total == 4461
    assert counts.has_issues is True


@pytest.mark.requirement("L3-MET-001")
def test_the_query_asks_only_for_issues_never_pull_requests() -> None:
    stub = _StubClient(counts_payload(closed=1))

    collect(stub)

    query, variables = stub.queries[0]
    # Pull requests are a different connection in the schema. Asking for
    # `issues` is what excludes them; the REST issues endpoint cannot.
    assert "issues(states: CLOSED)" in query
    assert "pullRequests" not in query
    assert variables == {"owner": "pypa", "name": "virtualenv"}


@pytest.mark.requirement("L3-MET-002")
def test_only_counts_are_requested_so_the_cost_stays_at_one_point() -> None:
    stub = _StubClient(counts_payload())

    collect(stub)

    query, _ = stub.queries[0]
    # Requesting nodes would page through every issue and cost points in
    # proportion to the repository's history.
    assert "totalCount" in query
    assert "nodes" not in query
    assert len(stub.queries) == 1


@pytest.mark.requirement("L3-MET-002")
def test_a_repository_with_no_issues_reports_zero_not_one() -> None:
    # PyGithub's REST totalCount now returns 1 for every repository, because
    # GitHub dropped rel="last" from the issues endpoint. Zero must be zero.
    stub = _StubClient(counts_payload(closed=0, open_=0))

    counts = collect(stub)

    assert counts.closed == 0
    assert counts.total == 0
    assert counts.has_issues is False


@pytest.mark.requirement("L3-MET-004")
def test_a_disabled_issue_tracker_is_flagged_rather_than_scored_as_inactivity(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stub = _StubClient(counts_payload(closed=0, enabled=False))

    with caplog.at_level(logging.WARNING, logger="github_metrics.collect.closed_issues"):
        counts = collect(stub)

    # Zero here is a configuration fact, not a maintenance fact.
    assert counts.issues_enabled is False
    assert "issue tracker disabled" in caplog.text


@pytest.mark.requirement("L3-MET-004")
def test_an_enabled_but_empty_tracker_is_noted_without_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stub = _StubClient(counts_payload(closed=0, open_=0, enabled=True))

    with caplog.at_level(logging.INFO, logger="github_metrics.collect.closed_issues"):
        collect(stub)

    assert "no issues at all" in caplog.text
    assert "disabled" not in caplog.text


@pytest.mark.requirement("L3-MET-004")
def test_collection_logs_the_counts_it_found(caplog: pytest.LogCaptureFixture) -> None:
    stub = _StubClient(counts_payload(closed=3770, open_=691))

    with caplog.at_level(logging.INFO, logger="github_metrics.collect.closed_issues"):
        collect(stub, "cline", "cline")

    assert "cline/cline" in caplog.text
    assert "3770" in caplog.text
    assert "pull requests excluded" in caplog.text


# ---------------------------------------------------------------------------
# GraphQL failure handling
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-MET-003")
def test_a_not_found_error_is_classified_rather_than_generic() -> None:
    stub = _StubClient(
        {
            "data": {"repository": None},
            "errors": [{"type": "NOT_FOUND", "message": "Could not resolve to a Repository"}],
        }
    )

    with pytest.raises(RepositoryNotFoundError) as caught:
        collect(stub, "ghost", "missing")

    # A deleted, renamed or private repository is an expected outcome of a
    # valid reference, so it gets its own code.
    assert "GM-COL-001" in str(caught.value)


@pytest.mark.requirement("L3-MET-003")
def test_any_other_graphql_error_is_reported_with_its_message() -> None:
    stub = _StubClient({"errors": [{"message": "Field 'bogus' doesn't exist"}]})

    with pytest.raises(GraphQLQueryError) as caught:
        collect(stub)

    assert "GM-COL-002" in str(caught.value)
    assert "bogus" in str(caught.value)


@pytest.mark.requirement("L3-MET-003")
def test_errors_are_detected_even_though_graphql_answers_http_200() -> None:
    # GraphQL reports failure in the body, not the status line. A caller that
    # only checks the status sees success and then reads a null repository.
    stub = _StubClient({"data": {"repository": None}, "errors": [{"message": "rate limited"}]})

    with pytest.raises(GraphQLQueryError):
        collect(stub)


@pytest.mark.requirement("L3-MET-003")
def test_a_response_with_no_data_object_fails_loudly() -> None:
    stub = _StubClient({})

    with pytest.raises(GraphQLQueryError, match="no data object"):
        collect(stub)


@pytest.mark.requirement("L3-MET-003")
def test_a_null_repository_without_an_error_still_fails() -> None:
    # Should not happen, but a KeyError here would read as our bug rather than
    # as a malformed response.
    stub = _StubClient({"data": {"repository": None}})

    with pytest.raises(RepositoryNotFoundError, match="no repository and no error"):
        collect(stub)
