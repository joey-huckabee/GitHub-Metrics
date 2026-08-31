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

A moved repository is a defective reference
------------------------------------------
The owner and name GitHub reports are not always the ones the inventory asked
for. Repositories are renamed and transferred, and the API follows the redirect
silently:

    inventory says   tiangolo/fastapi        pypa/pep517
    GitHub says      fastapi/fastapi         pypa/pyproject-hooks

Nothing fails, which is exactly the danger: the row would be collected against
a repository the inventory no longer names, every number in it would be right,
and nothing in the output would say the reference was stale.

So a mismatch raises `RepositoryMovedError` rather than returning data. The
inventory is the record of what is being measured; a reference that no longer
matches it is a defect in the list, not a successful read. The row still
appears in the output — identity columns filled, measurements empty — the run
warns with the current location so the fix is a copy and paste, and it exits
degraded rather than clean.

Case is not a mismatch. GitHub account and repository names are
case-insensitive, so `PyPA/virtualenv` and `pypa/virtualenv` are the same
reference and only the spelling differs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from github_metrics.client import GitHubClient
from github_metrics.collect.graphql import execute
from github_metrics.collect.timestamps import RepositoryTimestamps
from github_metrics.errors import (
    GraphQLQueryError,
    RepositoryMovedError,
    RepositoryNotFoundError,
)

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
        RepositoryMovedError: GitHub reports a different owner or name, so the
            reference resolves but no longer matches the inventory.
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
    _check_still_matches(slug, metadata)
    _log_shape(slug, metadata)
    return metadata


def _check_still_matches(slug: str, metadata: RepoMetaData) -> None:
    """Refuse a repository GitHub reports under a different name or owner.

    Args:
        slug: The reference as the inventory wrote it.
        metadata: What GitHub reported.

    Raises:
        RepositoryMovedError: If the two disagree by more than case.
    """
    if not (metadata.was_renamed or metadata.was_transferred):
        return

    current = f"{metadata.resolved_owner}/{metadata.resolved_name}"
    kind = "renamed" if metadata.was_renamed and not metadata.was_transferred else "moved"

    # A warning as well as the exception: the exception ends this row, while
    # the log is where someone reading a batch run finds the replacement to
    # paste into the inventory.
    LOGGER.warning(
        "%s has been %s to %s. GitHub still redirects, so the reference resolves, but it "
        "no longer names the repository the inventory asked for. No data collected; "
        "update the inventory to %s",
        slug,
        kind,
        current,
        current,
    )
    raise RepositoryMovedError(f"{slug} has been {kind} to {current}; update the inventory")


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
