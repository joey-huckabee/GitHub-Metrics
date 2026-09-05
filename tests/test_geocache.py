"""Tests for :mod:`github_metrics.geocache`.

The decision under test is that "does a cached answer expire" has three
answers rather than one. A match is trusted for a year, a miss for a month,
and a service failure is never written at all - because an outage recorded as
"unresolved" would be read back forever and would publish an unresolved
address for a place that resolves perfectly well.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from github_metrics.geocache import (
    CACHE_VERSION,
    MATCHED_TTL,
    UNMATCHED_TTL,
    CacheEntry,
    GeocodeCache,
    default_cache_path,
)
from github_metrics.model.contributor import Address, Coordinates

CACHE_LOGGER = "github_metrics.geocache"

RESOLVED = Address(
    query="austin, tx",
    formatted_address="Austin, Travis County, Texas, United States",
    street="Congress Avenue",
    house_number="100",
    suburb="Downtown",
    post_code="78701",
    state="Texas",
    state_code="US-TX",
    state_district="",
    county="Travis County",
    country="United States",
    country_code="us",
    city="Austin",
    internal_location=Coordinates(latitude=30.2711, longitude=-97.7437),
)


def now() -> datetime:
    """The current time, UTC, as the cache records it."""
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Round-tripping, which is what lets the cache be a file at all
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-MET-020")
def test_an_address_survives_the_file_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "geocode.json"
    cache = GeocodeCache(path)
    cache.put("austin, tx", RESOLVED, matched=True)
    cache.save()

    assert GeocodeCache.load(path).get("austin, tx") == RESOLVED


@pytest.mark.requirement("L3-MET-020")
def test_an_empty_component_stays_distinct_from_an_absent_one(tmp_path: Path) -> None:
    """`""` is a measurement and `None` is not, on both sides of the file.

    A country-level match genuinely has no city, and collapsing that into
    `None` would make it indistinguishable from a lookup that never ran.
    """
    country_only = Address(query="united states", country="United States", city="")
    path = tmp_path / "geocode.json"
    cache = GeocodeCache(path)
    cache.put("united states", country_only, matched=True)
    cache.save()

    restored = GeocodeCache.load(path).get("united states")

    assert restored is not None
    assert restored.city == ""
    assert restored.state is None


@pytest.mark.requirement("L3-MET-020")
def test_a_zero_coordinate_survives_and_an_absent_one_stays_absent(tmp_path: Path) -> None:
    """0,0 is a real position in the Gulf of Guinea, so it has to round-trip."""
    null_island = Address(query="null island", internal_location=Coordinates(0.0, 0.0))
    path = tmp_path / "geocode.json"
    cache = GeocodeCache(path)
    cache.put("null island", null_island, matched=True)
    cache.put("nowhere", Address(query="nowhere"), matched=False)
    cache.save()

    reloaded = GeocodeCache.load(path)
    island = reloaded.get("null island")
    nowhere = reloaded.get("nowhere")

    assert island is not None
    assert island.internal_location == Coordinates(0.0, 0.0)
    assert nowhere is not None
    assert nowhere.internal_location == Coordinates(None, None)


# ---------------------------------------------------------------------------
# Expiry: three outcomes, and only two of them are ever written
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-MET-020")
def test_a_match_and_a_miss_are_trusted_for_different_lengths_of_time() -> None:
    assert timedelta(days=365) == MATCHED_TTL
    assert timedelta(days=30) == UNMATCHED_TTL
    assert UNMATCHED_TTL < MATCHED_TTL


@pytest.mark.requirement("L3-MET-020")
@pytest.mark.parametrize(
    ("matched", "age", "still_there"),
    [
        (True, timedelta(days=100), True),
        (True, timedelta(days=400), False),
        (False, timedelta(days=10), True),
        (False, timedelta(days=40), False),
    ],
)
def test_an_entry_expires_by_what_it_recorded(
    matched: bool, age: timedelta, still_there: bool
) -> None:
    entry = CacheEntry(address=RESOLVED, matched=matched, resolved_at=now() - age)
    cache = GeocodeCache(None, {"austin, tx": entry})

    assert (cache.get("austin, tx") is not None) is still_there


@pytest.mark.requirement("L3-MET-020")
def test_an_expired_entry_is_dropped_rather_than_left_to_accumulate() -> None:
    stale = CacheEntry(address=RESOLVED, matched=False, resolved_at=now() - timedelta(days=90))
    cache = GeocodeCache(None, {"gone": stale})

    assert cache.get("gone") is None
    assert len(cache) == 0


@pytest.mark.requirement("L3-MET-020")
def test_a_clock_that_moved_backwards_does_not_discard_good_entries() -> None:
    """A future timestamp is treated as fresh; it is not the cache's problem."""
    future = CacheEntry(address=RESOLVED, matched=True, resolved_at=now() + timedelta(days=5))

    assert GeocodeCache(None, {"austin, tx": future}).get("austin, tx") is not None


# ---------------------------------------------------------------------------
# Every way reading a cache can fail costs a slow run, never a broken one
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-MET-020")
def test_a_missing_file_is_the_first_run(tmp_path: Path) -> None:
    cache = GeocodeCache.load(tmp_path / "absent.json")

    assert len(cache) == 0
    assert cache.get("anything") is None


@pytest.mark.requirement("L3-MET-020")
def test_an_unreadable_file_starts_empty_and_says_so(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "geocode.json"
    path.write_text("{not json at all", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger=CACHE_LOGGER):
        cache = GeocodeCache.load(path)

    assert len(cache) == 0
    assert "could not be read" in caplog.text


@pytest.mark.requirement("L3-MET-020")
def test_a_file_from_another_format_version_is_ignored_rather_than_migrated(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A cache is rebuildable, so reading it wrongly is the only bad outcome."""
    path = tmp_path / "geocode.json"
    path.write_text(json.dumps({"version": CACHE_VERSION + 1, "entries": {}}), encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger=CACHE_LOGGER):
        assert len(GeocodeCache.load(path)) == 0
    assert "not version" in caplog.text


@pytest.mark.requirement("L3-MET-020")
def test_one_damaged_entry_does_not_cost_the_whole_cache(tmp_path: Path) -> None:
    path = tmp_path / "geocode.json"
    good = CacheEntry(address=RESOLVED, matched=True, resolved_at=now())
    path.write_text(
        json.dumps(
            {
                "version": CACHE_VERSION,
                "entries": {
                    "austin, tx": good.to_mapping(),
                    "broken": {"matched": True},
                    "unparseable": {"matched": True, "resolved_at": "not a date", "address": {}},
                },
            }
        ),
        encoding="utf-8",
    )

    cache = GeocodeCache.load(path)

    assert cache.get("austin, tx") is not None
    assert len(cache) == 1


@pytest.mark.requirement("L3-MET-020")
def test_saving_is_atomic_and_leaves_no_temporary_behind(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "geocode.json"
    cache = GeocodeCache(path)
    cache.put("austin, tx", RESOLVED, matched=True)
    cache.save()

    assert path.is_file()
    assert sorted(item.name for item in path.parent.iterdir()) == ["geocode.json"]


@pytest.mark.requirement("L3-MET-020")
def test_a_cache_with_nothing_new_is_not_rewritten(tmp_path: Path) -> None:
    path = tmp_path / "geocode.json"
    GeocodeCache(path).save()

    assert not path.exists()


@pytest.mark.requirement("L3-MET-020")
def test_an_unwritable_destination_is_a_warning_rather_than_a_failed_run(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The measurements are already written; losing a cache is not worth a crash.

    The destination's parent is a regular file, so both the write and the
    cleanup that follows it fail - and they fail with different errors on
    Windows and POSIX. Neither may escape.
    """
    blocked = tmp_path / "afile"
    blocked.write_text("not a directory", encoding="utf-8")
    cache = GeocodeCache(blocked / "geocode.json")
    cache.put("austin, tx", RESOLVED, matched=True)

    with caplog.at_level(logging.WARNING, logger=CACHE_LOGGER):
        cache.save()

    assert "could not be written" in caplog.text


@pytest.mark.requirement("L3-MET-020")
def test_a_cache_with_no_path_never_touches_a_disk(tmp_path: Path) -> None:
    """What a library caller gets unless it asks for a file."""
    cache = GeocodeCache(None)
    cache.put("austin, tx", RESOLVED, matched=True)
    cache.save()

    assert not list(tmp_path.iterdir())
    assert cache.get("austin, tx") == RESOLVED


@pytest.mark.requirement("L3-MET-020")
def test_the_default_location_is_the_platform_cache_directory() -> None:
    path = default_cache_path()

    assert path.name == "geocode.json"
    assert path.parent.name == "github-metrics"
