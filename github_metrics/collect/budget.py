"""Deciding, before anything is collected, whether a run can finish.

Collection costs exactly one GraphQL point per repository, so the arithmetic
is a comparison rather than an estimate: a run of four hundred repositories
needs four hundred points of the five thousand available each hour.

Checking up front is the whole point. A run that discovers exhaustion halfway
through has already spent the budget it had, produced a file that is part
measurement and part absence, and given the operator nothing to distinguish the
two - the repositories at the end of the inventory look exactly like
repositories that could not be read. Refusing to start costs one free request
and leaves the quota intact for a smaller run or a later one.

The endpoint used for the check does not count against either budget.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final

from github_metrics.client import GitHubClient
from github_metrics.errors import RateLimitExhaustedError

LOGGER = logging.getLogger(__name__)

POINTS_PER_REPOSITORY: Final = 1
"""What `collect.repository` costs. Measured against the live API, not assumed."""

RESERVE_POINTS: Final = 10
"""Points left unspent, so a run cannot leave the token at exactly zero.

A token with nothing left is not just a finished run: the next command an
operator tries - a probe, a rate-limit check, a retry of one bad row - fails
too, and looks like a broken tool rather than a spent budget.
"""


@dataclass(frozen=True, slots=True)
class Budget:
    """What a run needs against what the token has.

    Attributes:
        repositories: How many repositories the run would collect.
        required: Points the run needs, including the reserve.
        available: Points the token has left this hour.
    """

    repositories: int
    required: int
    available: int

    @property
    def affordable(self) -> bool:
        """Whether the run fits in what is left."""
        return self.available >= self.required

    @property
    def shortfall(self) -> int:
        """Points missing, or zero when the run fits."""
        return max(0, self.required - self.available)


def check_budget(client: GitHubClient, repositories: int) -> Budget:
    """Confirm the run can finish before it starts.

    Args:
        client: An authenticated client.
        repositories: How many repositories the run would collect.

    Returns:
        The budget, for reporting.

    Raises:
        RateLimitExhaustedError: If the remaining points cannot cover the run.
    """
    available = client.graphql_points_remaining()
    budget = Budget(
        repositories=repositories,
        required=repositories * POINTS_PER_REPOSITORY + RESERVE_POINTS,
        available=available,
    )

    if not budget.affordable:
        raise RateLimitExhaustedError(
            f"{repositories} repositories need {budget.required} GraphQL points "
            f"(including a reserve of {RESERVE_POINTS}) but only {available} remain. "
            f"Short by {budget.shortfall}. Wait for the hourly reset, or collect fewer "
            "repositories per run"
        )

    LOGGER.debug(
        "Budget: %d repositories need %d of %d GraphQL points, leaving %d",
        repositories,
        budget.required,
        available,
        available - budget.required,
    )
    return budget
