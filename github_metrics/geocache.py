"""The geocode cache: what was looked up, what came back, and when.

Geocoding is paced at one request per second, so a location resolved once
should never be paid for twice - not within a run, and not between runs. This
module owns the on-disk half of that: the file format, when an entry stops
being trustworthy, and the atomic write.

Why this is not in `geo.py`
---------------------------
`geo.py` is collection - it opens sockets - and the structural rule of this
package is that **collection never touches a disk format**. A cache file is
not an output artifact, but it is unambiguously a file format, so it lives
here: this module parses and writes and never reaches the network, `geo.py`
reaches the network and never parses a file, and the CLI is where the two are
joined. `GeocodeCache` is handed to a `Geocoder`, not built by one.

Three outcomes, three policies
------------------------------
The decision that shapes this module is that "does a cached answer expire" has
**three** answers rather than one. See
`docs/adr/0007-persistent-geocode-cache.md`.

- **Matched** - a location resolved to a place. Places do not move, so this
  needs no expiry for correctness. `MATCHED_TTL` is a year anyway, to pick up
  gazetteer improvements, and costs one second per location per year.
- **No match** - the gazetteer has nothing for this string. Cached, because
  `she/her` and `earth` are common enough on GitHub that re-asking them every
  run would forfeit much of the benefit. Expires in `UNMATCHED_TTL`, thirty
  days, because a miss is a statement about *coverage* and coverage grows.
- **Service error** - Nominatim was unreachable, timed out, or refused.
  **Never written here at all.** Persisting one would let a single bad
  afternoon poison every location it touched: each later run would read
  "unresolved" from the cache and never ask again, publishing an unresolved
  address for a place that resolves perfectly well. An error that looks like
  data survives review, which is the failure this repository refuses
  everywhere else.

A cache is not a measurement
----------------------------
A cached answer produces exactly the address a fresh answer would, so nothing
downstream can tell how a value was obtained. Deleting the file costs time and
loses nothing.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Final

from github_metrics.model.contributor import Address

LOGGER = logging.getLogger(__name__)

CACHE_VERSION: Final = 1
"""Format version. A file written by any other version is ignored, not
migrated: a cache is rebuildable, so reading it wrongly is the only outcome
worth avoiding."""

CACHE_FILENAME: Final = "geocode.json"
"""Name of the cache file inside the cache directory."""

APPLICATION_DIRECTORY: Final = "github-metrics"
"""Directory this tool owns inside the platform cache location."""

MATCHED_TTL: Final = timedelta(days=365)
"""How long a resolved location is trusted. Not a correctness bound."""

UNMATCHED_TTL: Final = timedelta(days=30)
"""How long a miss is trusted. Shorter, because gazetteer coverage grows."""


def default_cache_path() -> Path:
    """Where the cache lives when nothing says otherwise.

    Returns:
        `%LOCALAPPDATA%/github-metrics/geocode.json` on Windows, and
        `$XDG_CACHE_HOME/github-metrics/geocode.json` elsewhere, falling back
        to `~/.cache` when that variable is unset. These are the conventional
        locations for regenerable data, which is what this is.
    """
    if os.name == "nt":
        base = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
    else:
        configured = os.getenv("XDG_CACHE_HOME")
        root = Path(configured) if configured else Path.home() / ".cache"
    return root / APPLICATION_DIRECTORY / CACHE_FILENAME


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """One remembered lookup.

    Attributes:
        address: What the lookup produced. For a miss this carries `query`
            and nothing else, which is the same value a fresh miss produces.
        matched: Whether the gazetteer found the place. Decides which expiry
            applies, and is the only reason the two are distinguishable once
            written.
        resolved_at: When the lookup ran, UTC.
    """

    address: Address
    matched: bool
    resolved_at: datetime

    @property
    def ttl(self) -> timedelta:
        """How long this entry is trusted, by what it recorded."""
        return MATCHED_TTL if self.matched else UNMATCHED_TTL

    def is_fresh(self, now: datetime) -> bool:
        """Whether this entry may still be used.

        Args:
            now: The current time, UTC.

        Returns:
            `True` while the entry is inside its TTL. An entry whose timestamp
            is in the future is treated as fresh rather than as corrupt - a
            clock that moved backwards is not the cache's problem, and
            discarding good entries over it would be the worse failure.
        """
        return now - self.resolved_at < self.ttl

    def to_mapping(self) -> dict[str, Any]:
        """Render as a JSON-ready mapping."""
        return {
            "matched": self.matched,
            "resolved_at": self.resolved_at.isoformat(),
            "address": self.address.to_mapping(),
        }

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> CacheEntry | None:
        """Rebuild one entry, or `None` if the payload cannot be trusted.

        Args:
            payload: One entry as `to_mapping` renders it.

        Returns:
            The entry, or `None` when a required part is missing or
            unparseable. A bad entry is dropped rather than raising: the cost
            is one second to look the location up again, and refusing to load
            a whole cache over one damaged row would be a worse trade.
        """
        address = payload.get("address")
        stamp = payload.get("resolved_at")
        if not isinstance(address, dict) or not isinstance(stamp, str):
            return None
        try:
            resolved_at = datetime.fromisoformat(stamp)
        except ValueError:
            return None
        if resolved_at.tzinfo is None:
            resolved_at = resolved_at.replace(tzinfo=timezone.utc)
        return cls(
            address=Address.from_mapping(address),
            matched=bool(payload.get("matched", False)),
            resolved_at=resolved_at,
        )


class GeocodeCache:
    """Locations already looked up, held across runs.

    Not thread-safe by itself. `Geocoder` owns the lock, because it is the
    thing several workers call at once and the lock has to cover the
    check-then-ask sequence rather than only the store.
    """

    def __init__(self, path: Path | None = None, entries: dict[str, CacheEntry] | None = None):
        """Create a cache bound to a file.

        Args:
            path: Where the cache is read from and written to. `None` makes
                this an in-memory cache that never persists, which is what a
                library caller gets unless it asks otherwise.
            entries: Starting contents, for tests and for `load`.
        """
        self.path = path
        self._entries: dict[str, CacheEntry] = dict(entries or {})
        self._dirty = False

    def __len__(self) -> int:
        """How many live entries the cache holds."""
        return len(self._entries)

    @property
    def dirty(self) -> bool:
        """Whether anything has been stored since the last load or save."""
        return self._dirty

    def get(self, key: str, *, now: datetime | None = None) -> Address | None:
        """Look one location up.

        Args:
            key: The normalised, case-folded location.
            now: Current time, UTC. Defaults to now.

        Returns:
            The remembered address, or `None` when the location is unknown or
            its entry has expired. An expired entry is dropped on the way out,
            so a long-lived cache does not accumulate them.
        """
        entry = self._entries.get(key)
        if entry is None:
            return None
        if not entry.is_fresh(now or _utcnow()):
            LOGGER.debug("Cached location %r has expired; it will be looked up again", key)
            del self._entries[key]
            self._dirty = True
            return None
        return entry.address

    def put(self, key: str, address: Address, *, matched: bool) -> None:
        """Remember one lookup.

        Only ever called for an answer the gazetteer actually gave. A service
        failure never reaches here - see the module docstring for why that is
        the important part of this design rather than an omission.

        Args:
            key: The normalised, case-folded location.
            address: What the lookup produced.
            matched: Whether the gazetteer found the place.
        """
        self._entries[key] = CacheEntry(address=address, matched=matched, resolved_at=_utcnow())
        self._dirty = True

    @classmethod
    def load(cls, path: Path | None) -> GeocodeCache:
        """Read a cache from disk, tolerating every way that can fail.

        A cache is rebuildable, so no failure here is worth raising: a missing
        file is the first run, and an unreadable one costs a slow run rather
        than a broken one. Both start empty, and the unreadable case warns
        because it is the one worth knowing about.

        Args:
            path: The file to read, or `None` for an in-memory cache.

        Returns:
            The cache, holding whichever entries were readable and unexpired.
        """
        if path is None:
            return cls(None)
        if not path.is_file():
            LOGGER.debug("No geocode cache at %s; starting empty", path)
            return cls(path)

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            LOGGER.warning("Geocode cache at %s could not be read (%s); starting empty", path, exc)
            return cls(path)

        if not isinstance(payload, dict) or payload.get("version") != CACHE_VERSION:
            LOGGER.warning(
                "Geocode cache at %s is not version %d; starting empty", path, CACHE_VERSION
            )
            return cls(path)

        raw = payload.get("entries")
        if not isinstance(raw, dict):
            return cls(path)

        now = _utcnow()
        entries: dict[str, CacheEntry] = {}
        expired = 0
        for key, value in raw.items():
            if not isinstance(value, dict):
                continue
            entry = CacheEntry.from_mapping(value)
            if entry is None:
                continue
            if not entry.is_fresh(now):
                expired += 1
                continue
            entries[str(key)] = entry

        LOGGER.info(
            "Geocode cache: %d locations loaded from %s (%d expired)", len(entries), path, expired
        )
        return cls(path, entries)

    def save(self) -> None:
        """Write the cache back, atomically, if there is anything new.

        Written to a temporary file in the same directory and renamed over the
        original, so an interrupted run leaves the previous cache intact
        rather than a truncated one. `Path.replace` is atomic within a
        filesystem, which a sibling temporary file guarantees.

        A failure to write is logged and swallowed: losing a cache costs the
        next run some time, and it is not a reason to fail a run whose
        measurements are already written.
        """
        if self.path is None or not self._dirty:
            return

        payload = {
            "version": CACHE_VERSION,
            "entries": {key: entry.to_mapping() for key, entry in self._entries.items()},
        }
        temporary = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(payload), encoding="utf-8")
            temporary.replace(self.path)
        except OSError as exc:
            LOGGER.warning("Geocode cache at %s could not be written (%s)", self.path, exc)
            # Suppressed, not just `missing_ok`: when the destination's parent
            # is not a directory, `unlink` raises `NotADirectoryError` rather
            # than `FileNotFoundError`, so the cleanup would escape the handler
            # it lives in and fail a run whose measurements are already
            # written. Windows and POSIX disagree about which error arrives
            # first here, which is how this reached CI green on one and red on
            # the other.
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
            return

        self._dirty = False
        LOGGER.info("Geocode cache: %d locations written to %s", len(self._entries), self.path)


def _utcnow() -> datetime:
    """The current time, UTC and timezone-aware."""
    return datetime.now(timezone.utc)
