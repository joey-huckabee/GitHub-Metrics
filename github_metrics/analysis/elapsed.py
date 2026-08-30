"""Elapsed time between a repository's timestamps and the scan.

Everything here is anchored to `scan_date` - the single instant recorded once
per run - rather than to the moment each repository happened to be fetched.

Why the anchor matters
----------------------
The implementation this replaces measured elapsed time per repository, at fetch
time. Its own reference row shows the cost: an `age_days` of 736.5466017006597
against a `created_at` of `2024-07-06T07:28:10Z` implies a "now" of
`20:35:16`, which is **129 seconds after** the `scan_date` of `20:33:07`
recorded in the same row.

Two consequences follow, and both are worse than the 129 seconds suggest:

- **Rows in one file are not comparable.** On a run taking forty minutes, the
  last repository's ages are measured against an instant forty minutes later
  than the first's. Every later row looks fractionally older and staler, and
  the bias is systematic rather than noise.
- **Order changes the numbers.** Re-running the same inventory with the rows
  shuffled produces different values for the same repositories, which makes two
  scans of one inventory undiffable.

Anchoring to `scan_date` removes both. It also makes the arithmetic checkable
by a reader, because `scan_date` is a column in the row: given it and a
repository's creation date, `age_days` can be recomputed by hand.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Final

LOGGER = logging.getLogger(__name__)

SECONDS_PER_HOUR: Final = 3600.0
SECONDS_PER_DAY: Final = 86400.0


def _elapsed_seconds(moment: datetime, scan_date: datetime, label: str) -> float:
    """Seconds from `moment` to `scan_date`, never negative.

    Args:
        moment: The repository timestamp.
        scan_date: The instant the run started.
        label: What is being measured, for the log message.

    Returns:
        The elapsed seconds, clamped at zero.

    Raises:
        ValueError: If either timestamp is naive. A naive datetime is ambiguous
            the moment it leaves the machine that made it, and subtracting one
            from an aware one raises anyway - failing here says why.
    """
    if moment.tzinfo is None or scan_date.tzinfo is None:
        raise ValueError(
            f"{label} needs timezone-aware timestamps; got moment={moment!r}, "
            f"scan_date={scan_date!r}"
        )

    seconds = (scan_date - moment).total_seconds()

    if seconds < 0:
        # A repository timestamp after the scan started. Possible when the scan
        # is long-running and the repository is updated mid-run, and possible
        # from clock skew. Either way the elapsed time is zero, not negative.
        LOGGER.warning(
            "%s is negative: the repository timestamp %s is after the scan date %s, "
            "by %.1f seconds. Treating the elapsed time as zero.",
            label,
            moment,
            scan_date,
            -seconds,
        )
        return 0.0

    return seconds


def age_days(created_at: datetime, scan_date: datetime) -> float:
    """Days between a repository's creation and the scan.

    Args:
        created_at: When the repository was created.
        scan_date: The instant the run started.

    Returns:
        Elapsed days, at full precision. No rounding: this is a measurement,
        and trimming it at the boundary discards resolution the caller may
        want.
    """
    days = _elapsed_seconds(created_at, scan_date, "age_days") / SECONDS_PER_DAY
    LOGGER.debug("Repository age %.6f days (created %s, scan %s)", days, created_at, scan_date)
    return days


def last_update_hours(updated_at: datetime, scan_date: datetime) -> float:
    """Hours between a repository's last update and the scan.

    `updated_at` is the timestamp used, not `pushed_at`. The two differ
    materially - measured across real repositories the gap reached 71 hours -
    because `pushed_at` moves only when code is pushed while `updated_at` also
    moves when repository metadata changes. `updated_at` is the broader signal
    of "something happened here", which is the reading this metric takes.

    Args:
        updated_at: When the repository was last updated.
        scan_date: The instant the run started.

    Returns:
        Elapsed hours, at full precision.
    """
    hours = _elapsed_seconds(updated_at, scan_date, "last_update_hours") / SECONDS_PER_HOUR
    LOGGER.debug(
        "Last updated %.6f hours before the scan (updated %s, scan %s)",
        hours,
        updated_at,
        scan_date,
    )
    return hours
