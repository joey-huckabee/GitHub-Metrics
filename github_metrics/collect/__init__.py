"""Collecting data from the GitHub API.

Everything in this package reaches the network. Nothing in it parses a disk
format; that belongs to `github_metrics.sources`. See `docs/ARCHITECTURE.md`.
"""

from github_metrics.collect.closed_issues import ClosedIssueCounts, get_closed_issues
from github_metrics.collect.releases import ReleaseCounts, get_release_counts
from github_metrics.collect.repository import RepoMetaData, get_repository
from github_metrics.collect.timestamps import RepositoryTimestamps, get_timestamps

__all__ = [
    "ClosedIssueCounts",
    "ReleaseCounts",
    "RepoMetaData",
    "RepositoryTimestamps",
    "get_closed_issues",
    "get_release_counts",
    "get_repository",
    "get_timestamps",
]
