"""Tests for :mod:`github_metrics.models`."""

from __future__ import annotations

from datetime import datetime, timezone

from github_metrics import __version__
from github_metrics.models import ContributorLocation, RepositoryMetrics


def test_repository_metrics_round_trips_through_json() -> None:
    original = RepositoryMetrics(
        full_name="python/cpython",
        stars=60000,
        created_at=datetime(2017, 2, 10, 19, 23, 51, tzinfo=timezone.utc),
        contributor_locations=[ContributorLocation(login="gvanrossum", raw_location="California")],
    )

    restored = RepositoryMetrics.from_json(original.to_json())

    assert restored == original


def test_optional_datetimes_encode_as_none() -> None:
    payload = RepositoryMetrics(full_name="owner/repo").to_dict()

    assert payload["created_at"] is None
    assert payload["pushed_at"] is None
    assert payload["contributor_locations"] == []


def test_snapshot_records_the_tool_version() -> None:
    payload = RepositoryMetrics(full_name="owner/repo").to_dict()

    assert payload["tool_version"] == __version__
