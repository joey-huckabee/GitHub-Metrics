"""Deciding, before anything is collected, whether a run can obviously not finish.

Collection spends from two separate hourly budgets, and a run has to fit in
both. Per repository, **at minimum**:

- **two GraphQL points** - one for the metrics query, one for the first chunk
  of the aliased contributor-detail query, of 5,000 available;
- **one REST request** - the first page of the contributors list, of 5,000
  available.

A floor, not a cost
-------------------
This module used to promise something stronger, and the promise is gone. Until
v0.5.0 the contributor list stopped at 25 accounts, which fitted in one REST
page and one detail query, so a repository cost *exactly* two points and one
request and the pre-flight was a **comparison**: a run that started could
finish.

`docs/adr/0006-collect-every-contributor.md` removed the limit. A repository's
cost now depends on how many contributors it has, which nobody knows until the
request that reveals it has been spent. The check is therefore a **lower
bound**: it refuses a run that cannot afford even the minimum, and passing is
**necessary but not sufficient**.

Inventing an average contributor count and multiplying by it was considered and
rejected. It would produce a number indistinguishable in shape from the old
guarantee and unequal to it in meaning, and a check that reports more
confidence than it has is the failure mode this repository has already been
caught by three times.

What the check still buys
-------------------------
The common case it was built for is unchanged: a token with little or nothing
left this hour is refused before it spends an hour producing a file that is
part measurement and part absence, with nothing to distinguish the two - the
repositories at the end of the inventory looking exactly like repositories that
could not be read. Refusing to start costs one free request.

The endpoint used for the check does not count against either budget.

No reserve is held back, so the budgets run to zero. Keeping a few points back
so a later command still works would buy a convenience by refusing a run the
token could actually have finished, which is the wrong trade for a tool whose
job is the batch.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final

from github_metrics.client import GitHubClient
from github_metrics.errors import RateLimitExhaustedError

LOGGER = logging.getLogger(__name__)

MIN_POINTS_PER_REPOSITORY: Final = 2
"""GraphQL points one repository costs **at minimum**.

One for `collect.repository`, measured against the live API. One for the first
chunk of `collect.contributors`' detail query, which asks for up to
`DETAIL_CHUNK_SIZE` accounts as aliased single-object selections - no
connection, so no `nodes`, so the cost formula prices each chunk as one
document rather than by how many accounts could come back.

A repository with more than `DETAIL_CHUNK_SIZE` contributors costs one further
point per additional chunk, and that count is not known before the list is
read. This is a floor.
"""

MIN_REQUESTS_PER_REPOSITORY: Final = 1
"""REST requests one repository costs **at minimum**: one contributors page.

The account details deliberately do not come from REST. Completing each
account lazily would be one request per contributor, and any repository of
consequence would exhaust the REST budget on its own.

At `client.PER_PAGE` of 100 a repository costs one request per hundred
contributors, so this too is a floor rather than a cost.
"""


@dataclass(frozen=True, slots=True)
class Budget:
    """What a run needs against what the token has, in both currencies.

    Attributes:
        repositories: How many repositories the run would collect.
        required: GraphQL points the run needs **at least**.
        available: GraphQL points the token has left this hour.
        requests_required: REST requests the run needs **at least**.
        requests_available: REST requests the token has left this hour.
    """

    repositories: int
    required: int
    available: int
    requests_required: int = 0
    requests_available: int = 0

    @property
    def affordable(self) -> bool:
        """Whether the run's **minimum** fits in what is left of both budgets.

        Necessary, not sufficient: a repository with many contributors costs
        more than the minimum, and how many it has is not known here. See the
        module docstring.
        """
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
    """Refuse a run that cannot afford even its minimum cost.

    This does **not** confirm the run can finish; no check here can, since a
    repository's cost depends on a contributor count that reading the list is
    what reveals. See the module docstring.

    Args:
        client: An authenticated client.
        repositories: How many repositories the run would collect.

    Returns:
        The budget, for reporting.

    Raises:
        RateLimitExhaustedError: If the remaining budget cannot cover even the
            minimum the run will spend.
    """
    available = client.graphql_points_remaining()
    requests_available = client.rate_limit_remaining()
    budget = Budget(
        repositories=repositories,
        required=repositories * MIN_POINTS_PER_REPOSITORY,
        available=available,
        requests_required=repositories * MIN_REQUESTS_PER_REPOSITORY,
        requests_available=requests_available,
    )

    if not budget.affordable:
        raise RateLimitExhaustedError(_shortfall_message(budget))

    LOGGER.debug(
        "Budget: %d repositories need at least %d of %d GraphQL points "
        "and at least %d of %d REST requests",
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
        f"{budget.repositories} repositories need at least " + " and ".join(parts) + ". "
        "Wait for the hourly reset, or collect fewer repositories per run"
    )
