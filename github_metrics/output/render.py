"""Writing results as CSV, JSON, and a console table."""

from __future__ import annotations

import csv
import json
from collections.abc import Sequence
from typing import TextIO

from github_metrics.model.software import EMPTY, SoftwareRow
from github_metrics.output.fields import resolve_fields

CONSOLE_SEPARATOR = "-" * 60
"""Rule drawn between console blocks."""


def write_csv(
    rows: Sequence[SoftwareRow],
    stream: TextIO,
    *,
    columns: Sequence[str] | None = None,
) -> None:
    """Write rows as CSV, header first.

    The header is written even when there are no rows, so a consumer always
    receives a well-formed file and can tell "no repositories" from "the run
    produced nothing at all".

    Args:
        rows: Rows to write, in the order to write them.
        stream: Destination. Open it with `newline=""` if it is a file, as the
            `csv` module requires.
        columns: Columns to emit. Defaults to all of them.
    """
    selected = resolve_fields(columns)
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(selected)
    for row in rows:
        writer.writerow(row.to_iterable(selected))


def write_json(
    rows: Sequence[SoftwareRow],
    stream: TextIO,
    *,
    columns: Sequence[str] | None = None,
    indent: int | None = 2,
) -> None:
    """Write rows as a JSON array of objects.

    The object keys are the CSV column names, and the run identity repeats in
    every object exactly as it repeats in every CSV row. The two formats
    therefore carry the same information in the same shape, and a consumer can
    move between them without a mapping layer.

    Args:
        rows: Rows to write, in the order to write them.
        stream: Destination.
        columns: Columns to emit. Defaults to all of them.
        indent: Passed to `json.dumps`; `None` produces compact output.
    """
    selected = resolve_fields(columns)
    payload = [row.to_mapping(selected) for row in rows]
    stream.write(json.dumps(payload, indent=indent))
    stream.write("\n")


def render_console(
    rows: Sequence[SoftwareRow],
    *,
    columns: Sequence[str] | None = None,
) -> str:
    """Render rows as vertical blocks, one per repository.

    Vertical rather than horizontal because there are nineteen columns: a
    horizontal table wraps or is truncated on any normal terminal, and a
    truncated metric is worse than no metric. Turning the columns into rows
    keeps every value readable and the block width constant.

    Args:
        rows: Rows to render, in order.
        columns: Columns to include. Defaults to all of them.

    Returns:
        The rendered text, without a trailing newline.
    """
    selected = resolve_fields(columns)
    if not rows:
        return "no repositories"

    label_width = max(len(name) for name in selected)
    blocks: list[str] = []
    for row in rows:
        values = row.to_iterable(selected)
        lines = [
            f"{name:<{label_width}}  {value if value != EMPTY else '-'}"
            for name, value in zip(selected, values, strict=True)
        ]
        blocks.append("\n".join(lines))

    return f"\n{CONSOLE_SEPARATOR}\n".join(blocks)
