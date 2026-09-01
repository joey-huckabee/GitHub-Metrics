"""Choosing which columns to emit.

Selection is a rendering filter, and only that. It was specified as a
rate-limit lever too - a column nobody asked for needs no data, so the
selection would decide which calls a run makes - but that was designed when
collection was assumed to be several REST calls per repository. Every column a
row needs now comes from one GraphQL query costing one point, so there is no
call left to skip and nothing cheaper than one point to reach for. The lever
was withdrawn rather than carried forward; see `docs/ROADMAP.md`.
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
    "name",
    "owner",
    "url",
    "scan_date",
    "scan_id",
)
"""Columns that identify the row and the run rather than measuring anything.

Every one of these survives a failed read: they come from the input row and
from the scan, so a repository that 404s still produces a row that says which
repository it was. `name` prefers GitHub's value when there is one, and `url`
is built from `owner` and `name`, so both have an answer either way.

`organization` is deliberately not among them. It reads like identity, but only
the API can report it — so it costs a call, and it is empty for an
unfetchable repository like every other collected value.
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
