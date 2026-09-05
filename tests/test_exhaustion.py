"""Tests for what a run does when its hourly budget runs out.

Two things matter here and neither is the flag itself.

The first is that **`wait` is the default**, so a run larger than one hour's
quota finishes rather than being refused - the capability this exists for.

The second is that a run which stops early must **say so in the data**, not
just in a log line. A partial CSV that is merely shorter cannot be told from a
shorter inventory, so every named repository keeps a row and the ones never
reached are marked rather than omitted.

Nothing here sleeps: the clock and the sleep are injected, so the waiting is
proved without being done.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import cast

import pytest

from github_metrics.client import GitHubClient
from github_metrics.collect.budget import MIN_POINTS_PER_REPOSITORY
from github_metrics.collect.exhaustion import (
    MAX_WAIT,
    VERIFY_MARGIN,
    BudgetGuard,
    Decision,
    ExhaustionPolicy,
)
from github_metrics.errors import RateLimitExhaustedError

EXHAUSTION_LOGGER = "github_metrics.collect.exhaustion"

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


class _StubClient:
    """Reports a budget that can be told to change between reads."""

    def __init__(self, *readings: int, reset_at: datetime | None = None) -> None:
        self.readings = list(readings) or [5000]
        self.reset_at = reset_at
        self.reads = 0

    def graphql_budget(self) -> tuple[int, datetime | None]:
        """Answer the guard's verification."""
        self.reads += 1
        value = self.readings[min(self.reads - 1, len(self.readings) - 1)]
        return value, self.reset_at

    def graphql_points_remaining(self) -> int:
        """Only used when the guard is built without a starting figure."""
        return self.readings[0]


def guard_for(
    stub: _StubClient,
    policy: ExhaustionPolicy = ExhaustionPolicy.WAIT,
    *,
    available: int = 5000,
    slept: list[float] | None = None,
) -> BudgetGuard:
    """Build a guard whose waiting is recorded rather than performed."""
    return BudgetGuard(
        cast(GitHubClient, stub),
        policy,
        available=available,
        sleeper=(slept.append if slept is not None else lambda _seconds: None),
        now=lambda: NOW,
    )


# ---------------------------------------------------------------------------
# The estimate, which is what keeps this cheap
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-EXH-001")
def test_a_run_far_from_the_edge_never_asks_the_api() -> None:
    """Verifying before every repository would add a round trip to a run that
    already makes several per repository."""
    stub = _StubClient(5000)
    guard = guard_for(stub)

    for index in range(20):
        assert guard.before(f"owner/repo{index}") is Decision.PROCEED

    assert stub.reads == 0


@pytest.mark.requirement("L3-EXH-001")
def test_the_api_is_asked_once_the_estimate_reaches_the_margin() -> None:
    """Near the edge is the only place precision matters, so every check there
    is a real reading."""
    stub = _StubClient(4000)
    guard = guard_for(stub, available=VERIFY_MARGIN)

    guard.before("owner/repo")

    assert stub.reads == 1


@pytest.mark.requirement("L3-EXH-001")
def test_the_estimate_is_a_floor_so_it_reaches_the_margin_early() -> None:
    """It subtracts the per-repository *minimum*, and a real repository costs
    more, so the guard arrives at the margin before the true spend does."""
    stub = _StubClient(5000)
    # One repository's minimum above the margin: the first call spends the
    # estimate down onto the margin, and the second must verify.
    guard = guard_for(stub, available=VERIFY_MARGIN + MIN_POINTS_PER_REPOSITORY)

    guard.before("owner/first")
    assert stub.reads == 0
    guard.before("owner/second")

    assert stub.reads == 1


# ---------------------------------------------------------------------------
# fail
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-EXH-002")
def test_fail_stops_at_the_first_sign_of_exhaustion() -> None:
    stub = _StubClient(0)
    guard = guard_for(stub, ExhaustionPolicy.FAIL, available=0)

    with pytest.raises(RateLimitExhaustedError) as caught:
        guard.before("pypa/virtualenv")

    assert "pypa/virtualenv" in str(caught.value)
    assert "--on-exhaustion wait" in str(caught.value)


# ---------------------------------------------------------------------------
# partial
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-EXH-002")
def test_partial_stops_collecting_and_says_which_repository_it_stopped_at(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stub = _StubClient(0)
    guard = guard_for(stub, ExhaustionPolicy.PARTIAL, available=0)

    with caplog.at_level(logging.WARNING, logger=EXHAUSTION_LOGGER):
        decision = guard.before("pypa/virtualenv")

    assert decision is Decision.SKIP
    assert guard.stopped
    assert "pypa/virtualenv" in caplog.text


@pytest.mark.requirement("L3-EXH-002")
def test_once_stopped_nothing_else_is_attempted_or_asked() -> None:
    """The remaining repositories must not each re-check a budget that is gone."""
    stub = _StubClient(0)
    guard = guard_for(stub, ExhaustionPolicy.PARTIAL, available=0)

    guard.before("first/one")
    reads_after_stopping = stub.reads

    assert guard.before("second/one") is Decision.SKIP
    assert guard.before("third/one") is Decision.SKIP
    assert stub.reads == reads_after_stopping


# ---------------------------------------------------------------------------
# wait
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-EXH-003")
def test_wait_sleeps_to_the_reset_and_then_continues() -> None:
    slept: list[float] = []
    # Empty, then full again after the reset.
    stub = _StubClient(0, 5000, reset_at=NOW + timedelta(minutes=10))
    guard = guard_for(stub, ExhaustionPolicy.WAIT, available=0, slept=slept)

    assert guard.before("pypa/virtualenv") is Decision.PROCEED
    assert guard.waits == 1
    # Ten minutes plus the margin that stops it waking a second early.
    assert 600 < slept[0] <= 615


@pytest.mark.requirement("L3-EXH-003")
def test_waking_into_a_still_empty_budget_waits_again() -> None:
    """Another process may share this token, so the reset is verified rather
    than assumed."""
    slept: list[float] = []
    stub = _StubClient(0, 0, 5000, reset_at=NOW + timedelta(minutes=5))
    guard = guard_for(stub, ExhaustionPolicy.WAIT, available=0, slept=slept)

    assert guard.before("pypa/virtualenv") is Decision.PROCEED
    assert guard.waits == 2
    assert len(slept) == 2


@pytest.mark.requirement("L3-EXH-003")
def test_a_reset_already_past_does_not_sleep_at_all() -> None:
    """Clocks disagree; the response is to re-check promptly, not to wait."""
    slept: list[float] = []
    stub = _StubClient(0, 5000, reset_at=NOW - timedelta(hours=2))
    guard = guard_for(stub, ExhaustionPolicy.WAIT, available=0, slept=slept)

    guard.before("pypa/virtualenv")

    assert slept == [0.0]


@pytest.mark.requirement("L3-EXH-003")
def test_an_implausible_reset_is_capped_rather_than_hanging_the_run() -> None:
    slept: list[float] = []
    stub = _StubClient(0, 5000, reset_at=NOW + timedelta(days=30))
    guard = guard_for(stub, ExhaustionPolicy.WAIT, available=0, slept=slept)

    guard.before("pypa/virtualenv")

    assert slept[0] == MAX_WAIT.total_seconds()


@pytest.mark.requirement("L3-EXH-003")
def test_a_missing_reset_time_waits_a_full_window() -> None:
    """Waking early only costs another free verification."""
    slept: list[float] = []
    stub = _StubClient(0, 5000, reset_at=None)
    guard = guard_for(stub, ExhaustionPolicy.WAIT, available=0, slept=slept)

    guard.before("pypa/virtualenv")

    assert slept[0] == MAX_WAIT.total_seconds()


# ---------------------------------------------------------------------------
# What the run reports afterwards
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-EXH-002")
def test_exhaustion_is_recorded_even_when_the_policy_recovered_from_it() -> None:
    """`wait` finishes the run, and the statistics still say the wall was hit."""
    stub = _StubClient(0, 5000, reset_at=NOW)
    guard = guard_for(stub, ExhaustionPolicy.WAIT, available=0)

    guard.before("pypa/virtualenv")

    assert guard.exhausted
    assert not guard.stopped


@pytest.mark.requirement("L3-EXH-002")
def test_a_run_that_never_ran_short_reports_neither() -> None:
    guard = guard_for(_StubClient(5000))

    guard.before("pypa/virtualenv")

    assert not guard.exhausted
    assert not guard.stopped
    assert guard.waits == 0
