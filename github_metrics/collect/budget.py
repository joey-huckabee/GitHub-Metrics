"""Deciding, before anything is collected, whether a run can finish.

Collection spends from two separate hourly budgets, and a run has to fit in
both. Per repository:

- **two GraphQL points** - one for the metrics query, one for the aliased
  contributor-detail query, of 5,000 available;
- **one REST request** - the contributors list, of 5,000 available.

GraphQL is therefore the binding constraint, at 2,500 repositories an hour
against REST's 5,000. Checking only one of the two would let a run start that
cannot finish, which is the failure this module exists to prevent.

Checking up front is the whole point. A run that discovers exhaustion halfway
through has already spent the budget it had, produced a file that is part
measurement and part absence, and given the operator nothing to distinguish the
two - the repositories at the end of the inventory look exactly like
repositories that could not be read. Refusing to start costs one free request
and leaves the quota intact for a smaller run or a later one.

The endpoint used for the check does not count against either budget.

No reserve is held back, so the budgets run to zero and a 2,500-repository
inventory is exactly the largest run a full hourly quota can do. Keeping a few
points back so a later command still works would buy a convenience by refusing
a run the token could actually have finished, which is the wrong trade for a
tool whose job is the batch.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final

from github_metrics.client import GitHubClient
from github_metrics.errors import RateLimitExhaustedError

LOGGER = logging.getLogger(__name__)

POINTS_PER_REPOSITORY: Final = 2
"""GraphQL points one repository costs.

One for `collect.repository`, measured against the live API. One for
`collect.contributors`' detail query, which asks for up to
`DEFAULT_CONTRIBUTOR_LIMIT` accounts as aliased single-object selections - no
connection, so no `nodes`, so the cost formula prices it as one document
rather than by how many accounts could come back.
"""

REQUESTS_PER_REPOSITORY: Final = 1
"""REST requests one repository costs: the contributors list, and nothing else.

The account details deliberately do not come from REST. Completing each
account lazily would be one request per contributor - 26 per repository at
the default limit - and a 200-repository inventory would exhaust the REST
budget before it finished.
"""


@dataclass(frozen=True, slots=True)
class Budget:
    """What a run needs against what the token has, in both currencies.

    Attributes:
        repositories: How many repositories the run would collect.
        required: GraphQL points the run needs.
        available: GraphQL points the token has left this hour.
        requests_required: REST requests the run needs.
        requests_available: REST requests the token has left this hour.
    """

    repositories: int
    required: int
    available: int
    requests_required: int = 0
    requests_available: int = 0

    @property
    def affordable(self) -> bool:
        """Whether the run fits in what is left of both budgets."""
        return self.available >= self.required and self.requests_available >= self.requests_required

    @property
    def shortfall(self) -> int:
        """GraphQL points missing, or zero when that budget covers the run."""
        return max(0, self.required - self.available)

    @property
    def request_shortfall(self) -> int:
        """REST requests missing, or zero when that budget covers the run."""
        return max(0, self.requests_required - self.requests_available)


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
    requests_available = client.rate_limit_remaining()
    budget = Budget(
        repositories=repositories,
        required=repositories * POINTS_PER_REPOSITORY,
        available=available,
        requests_required=repositories * REQUESTS_PER_REPOSITORY,
        requests_available=requests_available,
    )

    if not budget.affordable:
        raise RateLimitExhaustedError(_shortfall_message(budget))

    LOGGER.debug(
        "Budget: %d repositories need %d of %d GraphQL points and %d of %d REST requests",
        repositories,
        budget.required,
        available,
        budget.requests_required,
        requests_available,
    )
    return budget


def _shortfall_message(budget: Budget) -> str:
    """Explain which budget is short, and by how much.

    Naming the wrong one would send an operator to wait out a reset that was
    never the problem, so both are reported when both are short.

    Args:
        budget: The budget that did not fit.

    Returns:
        A message naming every budget that cannot cover the run.
    """
    parts = []
    if budget.shortfall:
        parts.append(
            f"{budget.required} GraphQL points but only {budget.available} remain "
            f"(short by {budget.shortfall})"
        )
    if budget.request_shortfall:
        parts.append(
            f"{budget.requests_required} REST requests but only "
            f"{budget.requests_available} remain (short by {budget.request_shortfall})"
        )
    return (
        f"{budget.repositories} repositories need " + " and ".join(parts) + ". "
        "Wait for the hourly reset, or collect fewer repositories per run"
    )
