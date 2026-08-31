"""Where an inventory comes from.

Ingestion turns whatever named a repository - a slug, a URL, a CSV file - into
validated `RepositoryRef` values. Nothing in this package reaches the network,
which is what lets a bad inventory be diagnosed before any rate limit is spent
and lets `validate` run on a machine with no credentials at all.
"""

from github_metrics.sources.csv_inventory import (
    DEFAULT_MAX_WORKERS,
    IngestResult,
    RepositoryRef,
    read_repository_csv,
    read_repository_csvs,
)
from github_metrics.sources.reference import parse_reference
from github_metrics.sources.resolve import ResolvedSources, is_csv_source, resolve_sources

__all__ = [
    "DEFAULT_MAX_WORKERS",
    "IngestResult",
    "RepositoryRef",
    "ResolvedSources",
    "is_csv_source",
    "parse_reference",
    "read_repository_csv",
    "read_repository_csvs",
    "resolve_sources",
]
