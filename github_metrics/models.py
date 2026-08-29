"""Serializable result types produced by the metric collectors."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from dataclasses_json import DataClassJsonMixin, config

from github_metrics import __version__


def _encode_dt(value: datetime | None) -> str | None:
    """Encode an optional datetime as ISO-8601."""
    return value.isoformat() if value is not None else None


def _decode_dt(value: str | None) -> datetime | None:
    """Decode an optional ISO-8601 string into a datetime."""
    return datetime.fromisoformat(value) if value else None


def _dt_field() -> dict[str, Any]:
    """Return dataclasses-json metadata for an optional ISO-8601 datetime."""
    return config(encoder=_encode_dt, decoder=_decode_dt)


@dataclass
class ContributorLocation(DataClassJsonMixin):
    """A contributor's self-reported location, optionally geocoded."""

    login: str
    raw_location: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    country: str | None = None


@dataclass
class RepositoryMetrics(DataClassJsonMixin):
    """Point-in-time metrics for a single repository."""

    full_name: str
    #: Version of github-metrics that produced this snapshot.
    tool_version: str = field(default_factory=lambda: __version__)
    stars: int = 0
    forks: int = 0
    watchers: int = 0
    open_issues: int = 0
    contributors: int = 0
    commits_last_year: int = 0
    license_id: str | None = None
    primary_language: str | None = None
    archived: bool = False
    created_at: datetime | None = field(default=None, metadata=_dt_field())
    pushed_at: datetime | None = field(default=None, metadata=_dt_field())
    collected_at: datetime | None = field(default=None, metadata=_dt_field())
    contributor_locations: list[ContributorLocation] = field(default_factory=list)
