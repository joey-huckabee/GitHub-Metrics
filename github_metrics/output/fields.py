"""Choosing which columns to emit.

Selecting fields is not only a rendering filter. Because a column that nobody
asked for needs no data, the selection also decides which API calls a run has
to make, which is what makes it a rate-limit lever rather than cosmetics. That
mapping lives with the collection layer; this module owns the vocabulary it
selects from.
"""

from __future__ import annotations

import difflib
from collections.abc import Sequence
from typing import Final

from github_metrics.errors import UnknownFieldError
from github_metrics.model.software import SoftwareRow

ALL_FIELDS: Final[tuple[str, ...]] = SoftwareRow.to_header()
"""Every emittable column, in canonical output order."""

IDENTITY_FIELDS: Final[tuple[str, ...]] = (
    "client_name",
    "owner",
    "organization",
    "scan_date",
    "scan_id",
)
"""Columns that identify the row and the run rather than measuring anything.

These need no API call, so selecting them costs nothing.
"""


def resolve_fields(selection: Sequence[str] | None) -> tuple[str, ...]:
    """Turn a caller's field selection into the columns to emit.

    Selecting nothing selects everything, so the common case needs no
    argument. A selection is always returned in **canonical order**, never in
    the order it was given: two runs asking for the same columns should
    produce byte-identical headers, and a diff between them should show
    changed data rather than reordered columns.

    Duplicates in the selection are collapsed rather than rejected, since
    naming a column twice expresses the same intent as naming it once.

    Args:
        selection: Column names, in any order and any case. `None` or empty
            selects every column.

    Returns:
        The columns to emit, in canonical order.

    Raises:
        UnknownFieldError: If a name is not a column. The message names the
            closest match when there is one, because these are typed by hand.
    """
    if not selection:
        return ALL_FIELDS

    known = set(ALL_FIELDS)
    wanted: set[str] = set()
    for raw in selection:
        name = raw.strip().casefold()
        if not name:
            continue
        if name not in known:
            raise UnknownFieldError(_unknown_message(name))
        wanted.add(name)

    if not wanted:
        # A selection of nothing but blanks and separators is the same request
        # as no selection at all.
        return ALL_FIELDS

    return tuple(name for name in ALL_FIELDS if name in wanted)


def split_selection(raw: str) -> list[str]:
    """Split a comma-separated field list from a command line.

    Args:
        raw: Something like `"stars, forks,total_score"`.

    Returns:
        The individual names, with surrounding whitespace removed and empty
        entries dropped, so a trailing comma is not an error.
    """
    return [part.strip() for part in raw.split(",") if part.strip()]


def _unknown_message(name: str) -> str:
    """Build the message for an unrecognised field name."""
    close = difflib.get_close_matches(name, ALL_FIELDS, n=1, cutoff=0.6)
    suggestion = f"; did you mean {close[0]!r}?" if close else ""
    valid = ", ".join(ALL_FIELDS)
    return f"unknown field {name!r}{suggestion} Valid fields are: {valid}"
