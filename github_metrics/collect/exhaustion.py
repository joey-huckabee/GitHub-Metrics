"""What a run does when it reaches the end of its hourly budget.

Until v0.6.0 the answer was always "refuse before starting", which meant an
inventory larger than one hour's quota could not be scanned at all. That was
the capability gap; `--on-exhaustion` closes it.

Three policies, and the default is the one that finishes
--------------------------------------------------------
- **`wait`** (default) sleeps to the hourly reset and carries on, so a large
  inventory completes unattended.
- **`fail`** refuses at the first sign of exhaustion. What a CI job with a step
  timeout should pass.
- **`partial`** collects what fits, marks the rest unmeasured, and exits 9.

Defaulting to `wait` changes what `scan` does, and only for runs that
previously produced nothing usable: a run inside its budget never reaches this
code at all. See `docs/adr/0009-rate-limit-exhaustion-policy.md`.

Estimating, then verifying
--------------------------
Asking GitHub for the remaining budget is free in points but is still an HTTP
round trip, and doing it before every repository would add one to a run that
already makes several per repository.

So the guard keeps a local estimate, decrements it by the known per-repository
minimum, and only asks the API once that estimate falls inside
`VERIFY_MARGIN`. Near the edge - the only place precision matters - every check
is a real reading; far from it, none are. The estimate is deliberately a
*floor*: it subtracts the minimum cost, so it reaches the margin sooner than
the true spend does and never overshoots into a wall it did not see coming.

One thread waits, the rest queue behind it
------------------------------------------
The lock covers the whole check-and-act sequence rather than only the counter.
Eight workers arriving at an exhausted budget must produce **one** sleep, not
eight, and the thread that wakes re-reads the budget so the others are released
against a real number rather than an assumption.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Final

from github_metrics.client import GitHubClient
from github_metrics.collect.budget import MIN_POINTS_PER_REPOSITORY
from github_metrics.errors import RateLimitExhaustedError

LOGGER = logging.getLogger(__name__)

VERIFY_MARGIN: Final = MIN_POINTS_PER_REPOSITORY * 20
"""How close to empty the estimate must be before the API is asked.

Twenty repositories' minimum. Far enough out that a burst of concurrent
collections cannot cross the whole margin between two checks, near enough that
a long run pays for almost no verification.
"""

WAKE_MARGIN: Final = timedelta(seconds=15)
"""Slept past the reset instant, because GitHub's clock is not ours and waking
a second early costs another full hour of waiting."""

MAX_WAIT: Final = timedelta(hours=1) + WAKE_MARGIN
"""Longest single sleep. GitHub's window is an hour, so anything beyond this is
a clock disagreement rather than a real wait, and sleeping on it would hang a
run with no way to tell."""


class ExhaustionPolicy(str, Enum):
    """What to do when the budget runs out mid-run."""

    WAIT = "wait"
    """Sleep to the hourly reset and continue. The default: completing is
    usually the intent, and a run inside its budget never reaches it."""

    FAIL = "fail"
    """Stop immediately, as every release before v0.6.0 did."""

    PARTIAL = "partial"
    """Stop collecting, keep what was gathered, and exit 9. The run says so in
    the data as well as the status - see `statistics.json`."""


class Decision(Enum):
    """What a worker should do with the repository it is holding."""

    PROCEED = "proceed"
    """Budget available; collect it."""

    SKIP = "skip"
    """The run has stopped. Record the repository as never attempted, which is
    what keeps a partial CSV the same length as its inventory."""


class BudgetGuard:
    """Decides, per repository, whether a run may keep spending.

    Shared across the worker pool. Not reusable across runs: it holds the
    estimate and the stop flag for one.
    """

    def __init__(
        self,
        client: GitHubClient,
        policy: ExhaustionPolicy = ExhaustionPolicy.WAIT,
        *,
        available: int | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        """Create a guard for one run.

        Args:
            client: An authenticated client, for verifying the budget.
            policy: What to do when it runs out.
            available: Points believed available at the start. Read from the
                API when omitted.
            sleeper: How to wait. Injected so a test can prove the waiting
                without doing any.
            now: Current time. Injected for the same reason.
        """
        self._client = client
        self.policy = policy
        self._sleep = sleeper
        self._now = now
        self._lock = threading.Lock()
        self._estimate = available if available is not None else client.graphql_points_remaining()
        self._stopped = False
        self.waits = 0
        """How many hourly resets this run slept through."""
        self.exhausted = False
        """Whether the budget ran out at any point, whatever the policy did."""

    @property
    def stopped(self) -> bool:
        """Whether the run has given up collecting anything further."""
        return self._stopped

    def before(self, slug: str) -> Decision:
        """Decide whether one repository may be collected.

        Args:
            slug: The repository, for messages.

        Returns:
            `PROCEED` to collect it, `SKIP` when the run has stopped.

        Raises:
            RateLimitExhaustedError: Under `fail`, when the budget is gone.
        """
        with self._lock:
            if self._stopped:
                return Decision.SKIP

            if self._estimate > VERIFY_MARGIN:
                # Far from the edge. Spend the estimate rather than a round
                # trip; it is a floor, so it arrives at the margin early.
                self._estimate -= MIN_POINTS_PER_REPOSITORY
                return Decision.PROCEED

            return self._verify(slug)

    def _verify(self, slug: str) -> Decision:
        """Ask the API what is really left, and act on the answer.

        Called with the lock held.
        """
        remaining, reset_at = self._client.graphql_budget()
        if remaining >= MIN_POINTS_PER_REPOSITORY:
            self._estimate = remaining - MIN_POINTS_PER_REPOSITORY
            return Decision.PROCEED

        self.exhausted = True
        return self._exhausted(slug, reset_at)

    def _exhausted(self, slug: str, reset_at: datetime | None) -> Decision:
        """Apply the policy. Called with the lock held."""
        if self.policy is ExhaustionPolicy.FAIL:
            raise RateLimitExhaustedError(
                f"the GraphQL budget ran out before {slug}. Wait for the hourly "
                f"reset, collect fewer repositories, or pass --on-exhaustion wait"
            )

        if self.policy is ExhaustionPolicy.PARTIAL:
            LOGGER.warning(
                "The GraphQL budget ran out before %s. --on-exhaustion=partial: "
                "collecting stops here and every repository after it is reported "
                "as unmeasured",
                slug,
            )
            self._stopped = True
            return Decision.SKIP

        self._wait(slug, reset_at)
        # Re-read rather than assume the reset landed: another process may
        # share this token, and waking into a still-empty budget should wait
        # again rather than fail.
        return self._verify(slug)

    def _wait(self, slug: str, reset_at: datetime | None) -> None:
        """Sleep to the hourly reset. Called with the lock held.

        The lock is deliberately **not** released: eight workers arriving at an
        exhausted budget must produce one sleep rather than eight, and the
        thread that wakes re-reads the budget so the others are released
        against a real number.
        """
        seconds = self._seconds_until(reset_at)
        self.waits += 1
        LOGGER.warning(
            "The GraphQL budget ran out before %s. Waiting %.0f seconds for the "
            "hourly reset, then continuing (wait %d of this run)",
            slug,
            seconds,
            self.waits,
        )
        self._sleep(seconds)

    def _seconds_until(self, reset_at: datetime | None) -> float:
        """How long to sleep, bounded at both ends.

        Args:
            reset_at: When GitHub says the hour resets, if it said.

        Returns:
            Seconds to sleep. Never negative, never longer than `MAX_WAIT`: a
            reset in the past means the clocks disagree and the right response
            is to re-check promptly, while one an implausible distance away
            would otherwise hang the run with nothing to explain it.
        """
        if reset_at is None:
            # No reset supplied. A full window is the safe assumption: waking
            # early only burns another verification round trip.
            return MAX_WAIT.total_seconds()
        seconds = (reset_at + WAKE_MARGIN - self._now()).total_seconds()
        return max(0.0, min(seconds, MAX_WAIT.total_seconds()))
