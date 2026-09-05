"""Attributing every commit by walking the history, for when sampling will not do.

Two questions are asked of this data and they need different coverage.

*Where does this project's work come from?* is answered well by the contributor
list: 90% of commits, measured, once no-reply recovery has run. The people it
misses are by construction the ones who contributed least.

*Is there any adversarial contributor here?* is not answered by it at all. A
single one-commit account is exactly what a sample omits, and no threshold
makes that acceptable.

This module answers the second question. `defaultBranchRef.target.history`
attributes each commit to the account its author email belongs to, and it is
**not subject to the 500-author-email ceiling** that bounds the contributors
endpoint - measured, 99 to 100 of every 100 commits come back with an account
attached.

The one query that asks for `nodes`
-----------------------------------
Every other query in this package is held to selecting no connection, because
`nodes` prices a query by the objects it could return and would make the
cheapest route the most expensive one for the largest repositories. This one
selects `nodes` deliberately: there is no other way to see individual commits,
and the pricing that rule avoids is exactly what is being paid for here.

**Measured: one point per page of 100 commits.** So the cost is the
repository's commit count divided by a hundred - 13 points for a
1,250-commit repository, **321** for a 32,016-commit one, against the 9 an
ordinary collection of the latter takes. Roughly 35x, growing with commit
count rather than with contributor count.

That is why this is a flag, off by default, and why the warning that
recommends it names what it will cost.

What it does not change
-----------------------
The records it produces are ordinary contributors: the same detail query, the
same geocoding, the same document shape. What changes is the *population*, and
`statistics.json` records `attribution.method` so that two runs of one
repository by different methods are never diffed as though they measured the
same thing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Final

from github_metrics.client import GitHubClient
from github_metrics.collect.contributors import BOT_LOGIN_SUFFIX, ContributorAccount
from github_metrics.collect.graphql import execute

LOGGER = logging.getLogger(__name__)

PAGE_SIZE: Final = 100
"""Commits per page. GitHub's maximum for a connection, and the divisor in the
cost: one point buys one page, so a smaller page would cost strictly more."""

MAX_PAGES: Final = 2000
"""Pages to walk before giving up, covering 200,000 commits.

A stop rather than a budget: a repository past this would spend a run's entire
hourly quota on one repository, and doing that silently is worse than saying
the attribution is incomplete.
"""

HISTORY_QUERY = f"""
query($owner: String!, $name: String!, $cursor: String) {{
  repository(owner: $owner, name: $name) {{
    defaultBranchRef {{
      target {{
        ... on Commit {{
          history(first: {PAGE_SIZE}, after: $cursor) {{
            pageInfo {{ hasNextPage endCursor }}
            nodes {{ author {{ user {{ databaseId login }} }} }}
          }}
        }}
      }}
    }}
  }}
}}
"""
"""One page of commits, each with the account its author resolves to.

Only `databaseId` and `login` are taken. The account's name, company and
location could be selected here too at no extra node cost, but they are left to
the ordinary detail query so that a deeply-attributed contributor and a listed
one are built by exactly one code path.
"""


@dataclass(frozen=True, slots=True)
class HistoryAttribution:
    """Every commit on the default branch, attributed where it can be.

    Attributes:
        accounts: One entry per account, with the commits attributed to it.
            Ranked by commits, like the contributors endpoint's own order.
        commits_walked: Commits examined.
        unattributed_commits: Commits whose author email belongs to no GitHub
            account. Nothing can attribute these - not this route and not any
            other - so they are the irreducible floor on coverage.
        pages: Pages fetched, which is also the GraphQL points spent.
        truncated: Whether `MAX_PAGES` stopped the walk before the history did.
    """

    accounts: tuple[ContributorAccount, ...] = ()
    commits_walked: int = 0
    unattributed_commits: int = 0
    pages: int = 0
    truncated: bool = False


def attribute_from_history(
    client: GitHubClient,
    owner: str,
    repoid: str,
) -> HistoryAttribution:
    """Walk a repository's default branch and attribute every commit.

    Args:
        client: An authenticated client.
        owner: The owner as the inventory wrote it.
        repoid: The repository name as the inventory wrote it.

    Returns:
        The attribution. An empty result for a repository with no default
        branch, which is a repository with no commits.

    Raises:
        RepositoryNotFoundError: The repository could not be read.
        GraphQLQueryError: The query failed for any other reason.
    """
    slug = f"{owner}/{repoid}"
    counts: dict[str, _Account] = {}
    walked = unattributed = pages = 0
    cursor: str | None = None
    truncated = False

    for page in range(1, MAX_PAGES + 1):
        history = _page(client, slug, owner, repoid, cursor=cursor, page=page)
        if history is None:
            break
        pages += 1

        for node in history.get("nodes") or []:
            walked += 1
            if not _attribute(node, counts):
                unattributed += 1

        info = history.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            break
        cursor = info.get("endCursor")
        if page == MAX_PAGES:
            truncated = True
            LOGGER.warning(
                "%s: stopped attributing after %d commits; its history is longer "
                "than one run should spend on a single repository",
                slug,
                walked,
            )

    accounts = tuple(
        ContributorAccount(
            login=item.login,
            github_id=item.github_id,
            contribution=item.commits,
            # `Commit.author.user` carries no account type, so the reserved
            # suffix stands in for it. Without this a deeply-attributed run
            # would report zero bots while carrying four of them - a number
            # that looks measured and is not.
            is_bot=item.login.endswith(BOT_LOGIN_SUFFIX),
        )
        for item in sorted(counts.values(), key=lambda item: item.commits, reverse=True)
    )
    LOGGER.debug(
        "%s: %d commits walked in %d pages, %d accounts, %d unattributed",
        slug,
        walked,
        pages,
        len(accounts),
        unattributed,
    )
    return HistoryAttribution(
        accounts=accounts,
        commits_walked=walked,
        unattributed_commits=unattributed,
        pages=pages,
        truncated=truncated,
    )


@dataclass
class _Account:
    """One account's running commit count."""

    login: str
    github_id: str
    commits: int = 0


def _page(
    client: GitHubClient,
    slug: str,
    owner: str,
    repoid: str,
    *,
    cursor: str | None,
    page: int,
) -> dict[str, Any] | None:
    """Fetch one page of history, or `None` when there is none to fetch."""
    data = execute(
        client,
        HISTORY_QUERY,
        {"owner": owner, "name": repoid, "cursor": cursor},
        description=f"commit history for {slug} (page {page})",
    )
    repository = data.get("repository") or {}
    branch = repository.get("defaultBranchRef")
    if not isinstance(branch, dict):
        # No default branch: a repository with no commits at all, which is a
        # real state rather than a failure.
        return None
    target = branch.get("target")
    if not isinstance(target, dict):
        return None
    history = target.get("history")
    return history if isinstance(history, dict) else None


def _attribute(node: Any, into: dict[str, _Account]) -> bool:
    """Credit one commit to its author's account, if it has one.

    Args:
        node: One `history` node.
        into: Accounts seen so far, keyed by login and mutated in place.

    Returns:
        Whether the commit was attributed. `False` means the author email
        belongs to no GitHub account - nothing can attribute it, so it counts
        toward the irreducible floor rather than being silently dropped.
    """
    if not isinstance(node, dict):
        return False
    author = node.get("author")
    user = author.get("user") if isinstance(author, dict) else None
    if not isinstance(user, dict):
        return False
    login = user.get("login")
    if not login:
        return False

    existing = into.get(str(login))
    if existing is None:
        into[str(login)] = _Account(
            login=str(login), github_id=str(user.get("databaseId") or ""), commits=1
        )
    else:
        existing.commits += 1
    return True
