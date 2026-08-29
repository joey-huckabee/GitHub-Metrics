"""Metric collectors that turn GitHub API objects into serializable results."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from github import GithubException

from github_metrics.models import ContributorLocation, RepositoryMetrics

if TYPE_CHECKING:  # pragma: no cover
    from github.NamedUser import NamedUser
    from github.Repository import Repository

    from github_metrics.client import GitHubClient
    from github_metrics.geo import Geocoder

LOGGER = logging.getLogger(__name__)

DEFAULT_CONTRIBUTOR_LIMIT = 25


def _license_id(repo: Repository) -> str | None:
    """Return the SPDX id of a repository's license, if it declares one."""
    try:
        license_obj = repo.get_license().license
    except GithubException:  # 404 for repositories without a detected license
        return None
    return str(license_obj.spdx_id) if license_obj is not None else None


def _commits_last_year(repo: Repository) -> int:
    """Return the total number of commits recorded in the last 52 weeks."""
    stats = repo.get_stats_participation()
    if stats is None or not stats.all:
        return 0
    return sum(stats.all)


def collect_repository_metrics(
    client: GitHubClient,
    full_name: str,
    *,
    geocoder: Geocoder | None = None,
    contributor_limit: int = DEFAULT_CONTRIBUTOR_LIMIT,
) -> RepositoryMetrics:
    """Collect point-in-time metrics for a single repository.

    Args:
        client: An authenticated GitHub client.
        full_name: The `owner/name` slug to inspect.
        geocoder: Optional geocoder; when supplied, contributor locations are
            resolved to coordinates.
        contributor_limit: Maximum number of contributors to inspect.

    Returns:
        The collected metrics.
    """
    repo = client.repository(full_name)
    contributors: list[NamedUser] = list(repo.get_contributors()[:contributor_limit])

    locations: list[ContributorLocation] = []
    for user in contributors:
        raw = user.location
        entry = ContributorLocation(login=user.login, raw_location=raw)
        if geocoder is not None and raw:
            coordinates = geocoder.locate(raw)
            if coordinates is not None:
                entry.latitude, entry.longitude = coordinates
        locations.append(entry)

    return RepositoryMetrics(
        full_name=repo.full_name,
        stars=repo.stargazers_count,
        forks=repo.forks_count,
        watchers=repo.subscribers_count,
        open_issues=repo.open_issues_count,
        contributors=len(contributors),
        commits_last_year=_commits_last_year(repo),
        license_id=_license_id(repo),
        primary_language=repo.language,
        archived=repo.archived,
        created_at=repo.created_at,
        pushed_at=repo.pushed_at,
        collected_at=datetime.now(tz=timezone.utc),
        contributor_locations=locations,
    )
