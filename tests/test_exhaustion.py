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
        self.observed: tuple[int, datetime | None] | None = None
        """What the last response reported, as the real client records it."""

    def spend(self, points: int) -> None:
        """Charge a repository's real cost, the way a live token would.

        Both faces of the budget move together, because on a real token they
        are one number: what a response reports, and what a verification read
        would return.
        """
        current = self.observed[0] if self.observed else self.readings[0]
        remaining = max(0, current - points)
        self.observed = (remaining, self.reset_at)
        self.readings = [remaining]

    def observed_budget(self) -> tuple[int, datetime | None] | None:
        """The reading that arrives free with every collection response."""
        return self.observed

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
def test_the_estimate_reaches_the_margin_on_the_reserved_minimum_alone() -> None:
    """With nothing observed yet, the reservation is all the guard has."""
    stub = _StubClient(5000)
    # One repository's minimum above the margin: the first call spends the
    # estimate down onto the margin, and the second must verify.
    guard = guard_for(stub, available=VERIFY_MARGIN + MIN_POINTS_PER_REPOSITORY)

    guard.before("owner/first")
    assert stub.reads == 0
    guard.before("owner/second")

    assert stub.reads == 1


@pytest.mark.requirement("L3-EXH-001", "L3-EXH-004")
def test_a_repository_costing_more_than_the_floor_still_reaches_the_margin() -> None:
    """The defect this mechanism was rebuilt for, at its measured cost.

    Subtracting the per-repository minimum was once the whole estimate, and it
    is an *upper* bound on what remains rather than a floor: a repository
    measured at nine points moved it by two. The estimate outran the truth by
    seven a repository and reached the margin after ~2,480 of them, while a
    5,000-point budget really died at 556. So the guard never verified, never
    waited, and never stopped - it approved every repository in any inventory
    anyone would actually scan.

    The responses now carry the real figure, and the guard takes the lower.
    """
    stub = _StubClient(5000)
    # `partial` rather than `wait`, so detection shows up as the run stopping
    # rather than as a sleep this test would have to unpick.
    guard = guard_for(stub, ExhaustionPolicy.PARTIAL, available=5000)

    approved = 0
    for index in range(700):
        if guard.before(f"owner/repo{index}") is not Decision.PROCEED:
            break
        approved += 1
        stub.spend(9)  # measured, API-LIMITS.md

    # 5,000 points at 9 a repository is 555 of them. The guard stops within a
    # repository of that, where the old one approved all 700 without ever
    # asking. It cannot stop *before* the last one overruns - the cost is not
    # known until the work is done - which is what `ran_dry` is for.
    assert 550 <= approved <= 560, approved
    assert guard.exhausted
    assert guard.stopped


@pytest.mark.requirement("L3-EXH-004")
def test_the_estimate_never_rises_to_meet_a_stale_reading() -> None:
    """Observations may only lower it. Between them the reservation stands."""
    stub = _StubClient(5000)
    guard = guard_for(stub, available=5000)
    # Two reservations above the margin, so the third decision lands on it.
    stub.observed = (VERIFY_MARGIN + 2 * MIN_POINTS_PER_REPOSITORY, None)

    guard.before("owner/first")
    stub.observed = (4000, None)  # a reading from before the last spend
    guard.before("owner/second")

    # Still inside the margin, so this decision is a real reading rather than
    # a stale 4,000 the guard had talked itself back up to.
    guard.before("owner/third")
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


# ---------------------------------------------------------------------------
# ran_dry: the backstop for a repository whose cost nothing could predict
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-EXH-005")
def test_a_budget_that_ran_out_mid_repository_waits_and_says_try_again() -> None:
    """No estimate can rule this out, so the API's own refusal is the signal.

    A repository's cost is not known until it is collected: nine points
    ordinarily, one per hundred commits under `--deep-attribution`. The guard
    decides before any of that is known, so a repository can begin inside the
    budget and end outside it.
    """
    slept: list[float] = []
    stub = _StubClient(0, 5000, reset_at=NOW + timedelta(minutes=30))
    guard = guard_for(stub, ExhaustionPolicy.WAIT, slept=slept)

    assert guard.ran_dry("pypa/virtualenv") is Decision.PROCEED

    assert slept, "wait must sleep before telling the caller to try again"
    assert guard.exhausted
    assert not guard.stopped


@pytest.mark.requirement("L3-EXH-005")
def test_a_budget_that_ran_out_mid_repository_stops_a_partial_run() -> None:
    stub = _StubClient(0, reset_at=NOW)
    guard = guard_for(stub, ExhaustionPolicy.PARTIAL)

    assert guard.ran_dry("pypa/virtualenv") is Decision.SKIP

    assert guard.exhausted
    assert guard.stopped


@pytest.mark.requirement("L3-EXH-005")
def test_a_budget_that_ran_out_mid_repository_fails_a_failing_run() -> None:
    stub = _StubClient(0, reset_at=NOW)
    guard = guard_for(stub, ExhaustionPolicy.FAIL)

    with pytest.raises(RateLimitExhaustedError) as caught:
        guard.ran_dry("pypa/virtualenv")

    assert "pypa/virtualenv" in str(caught.value)
    assert guard.exhausted


@pytest.mark.requirement("L3-EXH-005")
def test_a_stopped_run_is_not_restarted_by_a_late_arrival() -> None:
    """Eight workers can be inside a repository when the run stops."""
    stub = _StubClient(0, reset_at=NOW)
    guard = guard_for(stub, ExhaustionPolicy.PARTIAL)
    guard.ran_dry("pypa/virtualenv")

    assert guard.ran_dry("psf/requests") is Decision.SKIP
