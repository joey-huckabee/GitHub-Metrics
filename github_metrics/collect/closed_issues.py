"""Collecting closed-issue counts for a repository.

This module fetches; `github_metrics.analysis.closed_issues` scores what it
fetched. The split keeps the scoring a pure function of an integer, which is
what lets the bands be tested without a network, a token, or a mock.

What "closed issues" means here
-------------------------------
Issues only. **Pull requests are excluded**, which is the whole reason this
uses GraphQL — see `github_metrics.collect.graphql` for why REST cannot answer
the question. For `cline/cline` the distinction is 3,770 closed issues against
7,001 closed pull requests, so including them would nearly triple the number
and would measure something other than what the column is named after.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from github_metrics.client import GitHubClient
from github_metrics.collect.graphql import execute
from github_metrics.errors import RepositoryNotFoundError

LOGGER = logging.getLogger(__name__)

CLOSED_ISSUES_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    hasIssuesEnabled
    closedIssues: issues(states: CLOSED) { totalCount }
    openIssues: issues(states: OPEN) { totalCount }
  }
}
"""
"""Counts only. No issue bodies are fetched, so the cost is one point."""


@dataclass(frozen=True, slots=True)
class ClosedIssueCounts:
    """Issue counts for one repository.

    Attributes:
        closed: Closed issues, excluding pull requests.
        open: Open issues, excluding pull requests.
        issues_enabled: Whether the repository has its issue tracker turned on.
            This is the honest version of "does this project use issues": a
            repository with the tracker disabled reports zero closed issues,
            which is indistinguishable from an enabled tracker nobody has
            closed anything in, unless this flag is carried alongside.

    The derived `total` and `has_issues` properties are computed from these
    three; `has_issues` preserves the signal the original implementation
    returned alongside the count.
    """

    closed: int
    open: int
    issues_enabled: bool

    @property
    def total(self) -> int:
        """Open plus closed issues, excluding pull requests."""
        return self.open + self.closed

    @property
    def has_issues(self) -> bool:
        """Whether the repository has any issue at all."""
        return self.total > 0


def get_closed_issues(client: GitHubClient, owner: str, repoid: str) -> ClosedIssueCounts:
    """Fetch issue counts for one repository.

    Costs one GraphQL point regardless of how many issues the repository has,
    because only counts are requested.

    Args:
        client: An authenticated client.
        owner: The account owning the repository.
        repoid: The repository name.

    Returns:
        The counts, with pull requests excluded.

    Raises:
        RepositoryNotFoundError: The repository does not exist, is private to
            this token, or was renamed.
        GraphQLQueryError: The API reported some other error.
    """
    slug = f"{owner}/{repoid}"
    LOGGER.debug("Collecting closed-issue counts for %s", slug)

    data = execute(
        client,
        CLOSED_ISSUES_QUERY,
        {"owner": owner, "name": repoid},
        description=f"closed issues for {slug}",
    )

    repository = data.get("repository")
    if not isinstance(repository, dict):
        # A null repository with no errors array should not happen, but a
        # KeyError here would surface as a bug in our code rather than as a
        # problem with the response.
        raise RepositoryNotFoundError(f"{slug}: the API returned no repository and no error")

    counts = ClosedIssueCounts(
        closed=int(repository["closedIssues"]["totalCount"]),
        open=int(repository["openIssues"]["totalCount"]),
        issues_enabled=bool(repository["hasIssuesEnabled"]),
    )

    if not counts.issues_enabled:
        # Worth saying out loud: a zero here is a property of the repository's
        # configuration, not of its maintenance activity, and scoring it as
        # though it were the latter would penalise a project that tracks its
        # work somewhere else entirely.
        LOGGER.warning(
            "%s has its issue tracker disabled; closed=%d is configuration, not activity",
            slug,
            counts.closed,
        )
    elif not counts.has_issues:
        LOGGER.info("%s has an issue tracker enabled but no issues at all", slug)

    LOGGER.info(
        "%s: %d closed issues, %d open, %d total (pull requests excluded)",
        slug,
        counts.closed,
        counts.open,
        counts.total,
    )
    return counts
