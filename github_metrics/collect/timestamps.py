"""Collecting a repository's timestamps.

Three dates, from one GraphQL query costing one point. All three are free
fields on the repository object, so collecting the two that are not scored
costs nothing and keeps them available.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from github_metrics.client import GitHubClient
from github_metrics.collect.graphql import execute
from github_metrics.errors import GraphQLQueryError, RepositoryNotFoundError

LOGGER = logging.getLogger(__name__)

TIMESTAMPS_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    createdAt
    updatedAt
    pushedAt
  }
}
"""


@dataclass(frozen=True, slots=True)
class RepositoryTimestamps:
    """When a repository was created, last updated, and last pushed to.

    Attributes:
        created_at: Repository creation. Feeds `age_days`.
        updated_at: Last change of any kind, including repository metadata.
            Feeds `last_update_hours`.
        pushed_at: Last push of code to any branch. Collected but not scored;
            it is a free field, and it is the narrower reading of "active" if
            that is ever wanted.
    """

    created_at: datetime
    updated_at: datetime
    pushed_at: datetime | None = None


def _parse(value: str, field: str, slug: str) -> datetime:
    """Parse an ISO-8601 timestamp from the API.

    GitHub returns `Z` for UTC, which `datetime.fromisoformat` rejects before
    Python 3.11. Replacing it keeps the parse working on every supported
    interpreter rather than only the newest.

    Args:
        value: The timestamp string.
        field: The field name, for the error message.
        slug: The repository, for the error message.

    Returns:
        A timezone-aware datetime.

    Raises:
        GraphQLQueryError: If the value cannot be parsed.
    """
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GraphQLQueryError(f"{slug}: could not parse {field} from {value!r}") from exc


def get_timestamps(client: GitHubClient, owner: str, repoid: str) -> RepositoryTimestamps:
    """Fetch a repository's timestamps.

    Args:
        client: An authenticated client.
        owner: The account owning the repository.
        repoid: The repository name.

    Returns:
        The three timestamps, timezone-aware.

    Raises:
        RepositoryNotFoundError: The repository does not exist, is private to
            this token, or was renamed.
        GraphQLQueryError: The API reported an error, or returned a timestamp
            that could not be parsed.
    """
    slug = f"{owner}/{repoid}"
    LOGGER.debug("Collecting timestamps for %s", slug)

    data = execute(
        client,
        TIMESTAMPS_QUERY,
        {"owner": owner, "name": repoid},
        description=f"timestamps for {slug}",
    )

    repository = data.get("repository")
    if not isinstance(repository, dict):
        raise RepositoryNotFoundError(f"{slug}: the API returned no repository and no error")

    pushed_raw = repository.get("pushedAt")
    timestamps = RepositoryTimestamps(
        created_at=_parse(repository["createdAt"], "createdAt", slug),
        updated_at=_parse(repository["updatedAt"], "updatedAt", slug),
        pushed_at=_parse(pushed_raw, "pushedAt", slug) if pushed_raw else None,
    )

    if timestamps.pushed_at is None:
        # A repository with no commits on any branch. Its updated_at still
        # moves, so the metric that uses it is unaffected.
        LOGGER.debug("%s has never been pushed to", slug)

    LOGGER.debug(
        "%s: created %s, updated %s, pushed %s",
        slug,
        timestamps.created_at,
        timestamps.updated_at,
        timestamps.pushed_at if timestamps.pushed_at else "never",
    )
    return timestamps
