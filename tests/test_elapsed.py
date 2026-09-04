"""Tests for elapsed-time inputs and the timestamps they come from."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, cast

import pytest

from github_metrics.analysis.elapsed import age_days, last_update_hours
from github_metrics.client import GitHubClient
from github_metrics.collect.timestamps import RepositoryTimestamps, get_timestamps
from github_metrics.errors import GraphQLQueryError, RepositoryNotFoundError
from github_metrics.model import ScanIdentifier

ELAPSED_LOGGER = "github_metrics.analysis.elapsed"
COLLECT_LOGGER = "github_metrics.collect.timestamps"

# The reference row's own values.
REFERENCE_CREATED = datetime(2024, 7, 6, 7, 28, 10, tzinfo=timezone.utc)
REFERENCE_SCAN = datetime(2026, 7, 12, 20, 33, 7, 254804, tzinfo=timezone.utc)
REFERENCE_AGE_DAYS = 736.5466017006597


class _StubClient:
    """Returns a canned GraphQL payload."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.queries: list[tuple[str, dict[str, Any]]] = []

    def graphql(
        self, query: str, variables: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Mimic `GitHubClient.graphql`."""
        self.queries.append((query, variables))
        return {}, self.payload


def payload(
    created: str = "2024-07-06T07:28:10Z",
    updated: str = "2026-07-12T12:00:00Z",
    pushed: str | None = "2026-07-12T11:00:00Z",
) -> dict[str, Any]:
    """A successful repository response."""
    return {
        "data": {"repository": {"createdAt": created, "updatedAt": updated, "pushedAt": pushed}}
    }


def collect(stub: _StubClient) -> RepositoryTimestamps:
    """Run collection against a stub client."""
    return get_timestamps(cast(GitHubClient, stub), "cline", "cline")


# ---------------------------------------------------------------------------
# The anchor
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-MET-008")
def test_age_is_measured_from_the_scan_date_not_from_now() -> None:
    # Two calls a moment apart must agree, because the anchor is fixed.
    first = age_days(REFERENCE_CREATED, REFERENCE_SCAN)
    second = age_days(REFERENCE_CREATED, REFERENCE_SCAN)

    assert first == second
    assert first == pytest.approx(736.545107115787, abs=1e-9)


@pytest.mark.requirement("L3-MET-008")
def test_the_anchor_change_is_visible_against_the_reference_row() -> None:
    """The reference row was measured after its own scan_date.

    Its `age_days` implies a "now" 129 seconds later than the `scan_date`
    printed in the same row. Anchoring to `scan_date` therefore produces a
    slightly smaller number, and this records by how much rather than leaving
    a later reader to wonder why archived values do not reproduce.
    """
    anchored = age_days(REFERENCE_CREATED, REFERENCE_SCAN)

    drift_seconds = (REFERENCE_AGE_DAYS - anchored) * 86400
    assert drift_seconds == pytest.approx(129.1, abs=0.5)
    assert anchored < REFERENCE_AGE_DAYS


@pytest.mark.requirement("L3-MET-008")
def test_every_row_in_a_run_shares_one_anchor() -> None:
    # The property that makes rows comparable: one instant for the whole run,
    # so a repository's age does not depend on where it sat in the file.
    scan = ScanIdentifier()
    created = scan.scan_date - timedelta(days=100)

    early_row = age_days(created, scan.scan_date)
    late_row = age_days(created, scan.scan_date)

    assert early_row == late_row == pytest.approx(100.0, abs=1e-9)


@pytest.mark.requirement("L3-MET-008")
def test_full_precision_is_kept() -> None:
    # These are measurements; rounding at the boundary discards resolution the
    # caller may want. The reference row carries sixteen significant figures.
    value = age_days(REFERENCE_CREATED, REFERENCE_SCAN)

    assert len(repr(value).split(".")[1]) > 6


# ---------------------------------------------------------------------------
# last_update_hours
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-MET-009")
def test_hours_are_measured_from_updated_at() -> None:
    scan = datetime(2026, 7, 12, 20, 0, 0, tzinfo=timezone.utc)
    updated = scan - timedelta(hours=8, minutes=6, seconds=6)

    assert last_update_hours(updated, scan) == pytest.approx(8.10166, abs=1e-4)


@pytest.mark.requirement("L3-MET-009")
def test_a_freshly_updated_repository_reports_near_zero() -> None:
    scan = datetime(2026, 7, 12, 20, 0, 0, tzinfo=timezone.utc)

    assert last_update_hours(scan, scan) == 0.0


# ---------------------------------------------------------------------------
# Out-of-order timestamps
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-MET-010")
def test_a_timestamp_after_the_scan_is_reported_and_clamped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Reachable on a long run when a repository is updated mid-scan, and from
    # clock skew. Elapsed time is zero, never negative.
    scan = datetime(2026, 7, 12, 20, 0, 0, tzinfo=timezone.utc)
    updated = scan + timedelta(minutes=5)

    with caplog.at_level(logging.WARNING, logger=ELAPSED_LOGGER):
        hours = last_update_hours(updated, scan)

    assert hours == 0.0
    assert "is negative" in caplog.text
    assert "300.0 seconds" in caplog.text


@pytest.mark.requirement("L3-MET-010")
@pytest.mark.parametrize(
    ("moment", "scan"),
    [
        # Naive on purpose, once on each side: these are the values the
        # code under test exists to refuse, so DTZ001 is right about them
        # and wrong about the intent. Suppressed here rather than for the
        # whole file, so an *accidental* naive datetime elsewhere in the
        # suite still fails.
        (datetime(2024, 1, 1), datetime(2026, 1, 1, tzinfo=timezone.utc)),  # noqa: DTZ001
        (datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 1)),  # noqa: DTZ001
    ],
)
def test_a_naive_timestamp_is_refused_with_a_reason(moment: datetime, scan: datetime) -> None:
    # Subtracting a naive datetime from an aware one raises anyway; failing
    # here says which value was the problem.
    with pytest.raises(ValueError, match="timezone-aware"):
        age_days(moment, scan)


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-MET-011")
def test_all_three_timestamps_are_parsed() -> None:
    stamps = collect(_StubClient(payload()))

    assert stamps.created_at == REFERENCE_CREATED
    assert stamps.updated_at == datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)
    assert stamps.pushed_at == datetime(2026, 7, 12, 11, 0, 0, tzinfo=timezone.utc)


@pytest.mark.requirement("L3-MET-011")
def test_the_parsed_timestamps_are_timezone_aware() -> None:
    # Everything downstream anchors against scan_date, which is aware; a naive
    # value here would fail at the subtraction instead of at the parse.
    stamps = collect(_StubClient(payload()))

    assert stamps.created_at.tzinfo is not None
    assert stamps.updated_at.tzinfo is not None


@pytest.mark.requirement("L3-MET-011")
def test_a_repository_never_pushed_to_is_handled(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG, logger=COLLECT_LOGGER):
        stamps = collect(_StubClient(payload(pushed=None)))

    assert stamps.pushed_at is None
    assert "never been pushed to" in caplog.text


@pytest.mark.requirement("L3-MET-011")
def test_one_query_asks_for_all_three() -> None:
    stub = _StubClient(payload())

    collect(stub)

    query, _ = stub.queries[0]
    assert "createdAt" in query
    assert "updatedAt" in query
    assert "pushedAt" in query
    assert len(stub.queries) == 1


@pytest.mark.requirement("L3-MET-011")
def test_an_unparseable_timestamp_fails_loudly() -> None:
    stub = _StubClient(payload(created="not-a-date"))

    with pytest.raises(GraphQLQueryError, match="could not parse"):
        collect(stub)


@pytest.mark.requirement("L3-MET-011")
def test_a_null_repository_fails_rather_than_raising_a_key_error() -> None:
    stub = _StubClient({"data": {"repository": None}})

    with pytest.raises(RepositoryNotFoundError):
        collect(stub)


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-MET-009")
def test_collected_timestamps_feed_the_elapsed_calculations() -> None:
    scan = ScanIdentifier(scan_date=datetime(2026, 7, 12, 20, 33, 7, tzinfo=timezone.utc))
    stamps = collect(_StubClient(payload()))

    assert age_days(stamps.created_at, scan.scan_date) == pytest.approx(736.545, abs=1e-3)
    assert last_update_hours(stamps.updated_at, scan.scan_date) == pytest.approx(8.5519, abs=1e-3)
