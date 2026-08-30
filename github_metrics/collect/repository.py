"""Collecting everything one repository contributes to a row, in one query.

Every metric this tool scores comes from a single GraphQL document costing
**one point** of a 5,000-point hourly budget - verified against the live API,
including the counts that REST cannot answer correctly at all. Five thousand
repositories an hour, whatever the size of the inventory.

The per-metric collectors alongside this one are not redundant. They back the
probe commands, where the point is to ask about one metric in isolation while
its definition is being agreed. This module is what a batch run uses.

Owner, and what it means for `organization`
-------------------------------------------
A repository is owned by either a user account or an organisation, and GitHub
says which: `owner.__typename` is exactly `User` or `Organization`. The
`organization` column follows from it - the owner's login when the owner is an
organisation, and **empty** when it is a person. Empty is not a gap; it is how
a row records that the repository is personally owned, and there is no other
column in which that fact could live.

The owner GitHub reports is not always the owner the inventory asked for.
Repositories move, and the API follows the redirect silently:

    inventory says   tiangolo/fastapi
    GitHub says      fastapi/fastapi

Both are kept. `owner` stays as the inventory wrote it, because that is the
value someone has to edit to fix the list; `resolved_owner` records where the
repository actually lives now.

The same is true of the repository's own name, which is why the query asks for
it rather than trusting the inventory. A rename redirects exactly as a transfer
does, so `repoid` can be stale and still work. `resolved_name` is GitHub's
answer, and it is what the `repo_name` column carries.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from github_metrics.client import GitHubClient
from github_metrics.collect.graphql import execute
from github_metrics.collect.timestamps import RepositoryTimestamps
from github_metrics.errors import GraphQLQueryError, RepositoryNotFoundError

LOGGER = logging.getLogger(__name__)

ORGANIZATION_TYPE: Final = "Organization"
"""The `__typename` GitHub reports for an organisation-owned repository."""

REPOSITORY_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    name
    owner { login __typename }
    stargazerCount
    forkCount
    createdAt
    updatedAt
    pushedAt
    hasIssuesEnabled
    closedIssues: issues(states: CLOSED) { totalCount }
    openIssues: issues(states: OPEN) { totalCount }
    releases { totalCount }
    tags: refs(refPrefix: "refs/tags/") { totalCount }
  }
}
"""
"""Every field a row needs. Totals only - no `nodes`, so the cost stays at one."""


@dataclass(frozen=True, slots=True)
class RepoMetaData:
    """Everything GitHub reports about one repository, before any scoring.

    Attributes:
        owner: The owner as the inventory wrote it.
        repoid: The repository name as the inventory wrote it.
        resolved_owner: The owner GitHub reports, which differs when a
            repository has been transferred.
        resolved_name: The name GitHub reports, which differs when a repository
            has been renamed.
        owner_type: `Organization` or `User`.
        stars: Stargazers.
        forks: Forks.
        timestamps: Creation, last update, last push.
        closed_issues: Closed issues, excluding pull requests.
        open_issues: Open issues, excluding pull requests.
        issues_enabled: Whether the issue tracker is switched on.
        releases: Published GitHub Releases.
        tags: Entries under `refs/tags/`.
    """

    owner: str
    repoid: str
    resolved_owner: str
    resolved_name: str
    owner_type: str
    stars: int
    forks: int
    timestamps: RepositoryTimestamps
    closed_issues: int
    open_issues: int
    issues_enabled: bool
    releases: int
    tags: int

    @property
    def organization(self) -> str:
        """The owning organisation, or empty for a personally owned repository.

        Empty carries meaning here: it is the row's way of saying the
        repository belongs to a person rather than an organisation, and no
        other column records that.
        """
        return self.resolved_owner if self.owner_type == ORGANIZATION_TYPE else ""

    @property
    def is_organization(self) -> bool:
        """Whether the repository is owned by an organisation."""
        return self.owner_type == ORGANIZATION_TYPE

    @property
    def was_transferred(self) -> bool:
        """Whether GitHub reports a different owner than the inventory asked for."""
        return self.resolved_owner.casefold() != self.owner.casefold()

    @property
    def was_renamed(self) -> bool:
        """Whether GitHub reports a different name than the inventory asked for."""
        return self.resolved_name.casefold() != self.repoid.casefold()

    @property
    def distinct_versions(self) -> int:
        """Version markers, counting each once. See `collect.releases`."""
        return max(self.releases, self.tags)


def _parse(value: str, field: str, slug: str) -> datetime:
    """Parse an ISO-8601 timestamp, tolerating GitHub's trailing `Z`."""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GraphQLQueryError(f"{slug}: could not parse {field} from {value!r}") from exc


def get_repository(client: GitHubClient, owner: str, repoid: str) -> RepoMetaData:
    """Fetch everything one repository contributes to a row.

    Args:
        client: An authenticated client.
        owner: The owner as the inventory wrote it.
        repoid: The repository name as the inventory wrote it.

    Returns:
        The collected metadata, unscored.

    Raises:
        RepositoryNotFoundError: The repository does not exist, is private to
            this token, or was renamed beyond GitHub's own redirect.
        GraphQLQueryError: The API reported an error, or returned a value that
            could not be parsed.
    """
    slug = f"{owner}/{repoid}"
    LOGGER.debug("Collecting repository metadata for %s", slug)

    data = execute(
        client,
        REPOSITORY_QUERY,
        {"owner": owner, "name": repoid},
        description=f"repository metadata for {slug}",
    )

    repository = data.get("repository")
    if not isinstance(repository, dict):
        raise RepositoryNotFoundError(f"{slug}: the API returned no repository and no error")

    metadata = _build(owner, repoid, slug, repository)
    _log_shape(slug, metadata)
    return metadata


def _build(owner: str, repoid: str, slug: str, repository: dict[str, Any]) -> RepoMetaData:
    """Assemble the dataclass from a repository payload."""
    owner_node = repository.get("owner") or {}
    pushed_raw = repository.get("pushedAt")

    return RepoMetaData(
        owner=owner,
        repoid=repoid,
        resolved_owner=str(owner_node.get("login", owner)),
        resolved_name=str(repository.get("name") or repoid),
        owner_type=str(owner_node.get("__typename", "")),
        stars=int(repository["stargazerCount"]),
        forks=int(repository["forkCount"]),
        timestamps=RepositoryTimestamps(
            created_at=_parse(repository["createdAt"], "createdAt", slug),
            updated_at=_parse(repository["updatedAt"], "updatedAt", slug),
            pushed_at=_parse(pushed_raw, "pushedAt", slug) if pushed_raw else None,
        ),
        closed_issues=int(repository["closedIssues"]["totalCount"]),
        open_issues=int(repository["openIssues"]["totalCount"]),
        issues_enabled=bool(repository["hasIssuesEnabled"]),
        releases=int(repository["releases"]["totalCount"]),
        tags=int(repository["tags"]["totalCount"]),
    )


def _log_shape(slug: str, metadata: RepoMetaData) -> None:
    """Narrate what was collected, and anything unusual about it."""
    LOGGER.debug(
        "%s: %d stars, %d forks, %d closed issues, %d distinct versions, owned by %s (%s)",
        slug,
        metadata.stars,
        metadata.forks,
        metadata.closed_issues,
        metadata.distinct_versions,
        metadata.resolved_owner,
        metadata.owner_type or "unknown",
    )

    if metadata.was_renamed:
        # Same situation as a transfer, and just as invisible: GitHub redirects,
        # so the entry works while no longer matching what the inventory says.
        LOGGER.warning(
            "%s is now named %s; the inventory entry still resolves but no longer matches",
            slug,
            metadata.resolved_name,
        )

    if metadata.was_transferred:
        # The inventory is out of date but still resolves, because GitHub
        # redirects. Worth saying so: the row will carry one owner and a
        # different organisation, and that is not a bug.
        LOGGER.warning(
            "%s now lives at %s/%s; GitHub followed the redirect, so the inventory "
            "entry still works but no longer matches",
            slug,
            metadata.resolved_owner,
            metadata.repoid,
        )

    if not metadata.is_organization:
        LOGGER.debug(
            "%s is owned by an individual account, so its organization column is empty",
            slug,
        )

    if not metadata.issues_enabled:
        LOGGER.warning(
            "%s has its issue tracker disabled; closed=%d is configuration, not activity",
            slug,
            metadata.closed_issues,
        )
