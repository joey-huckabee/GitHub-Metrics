"""Writing `statistics.json`, the third artifact of a run.

One file per scan, beside `githubmetrics.csv` and carrying the same `scan_id`,
so the three artifacts join on the run that produced them.

Why it is a run-level file rather than a key in each document
-------------------------------------------------------------
Some of what it records has no per-repository grain at all - what the run
spent, whether the budget held, what the geocode cache did - and a
cross-repository question ("which of these 400 repositories is badly
under-attributed?") should not require opening 400 files. The per-repository
detail lives in an array inside it, in input order, so it still aligns with the
CSV row for row.

Written last, and unconditionally
---------------------------------
Written after the CSV and the documents, because it reports on them - including
how many documents were written and why the rest were not. It is written even
when the run collected nothing: a statistics file saying zero repositories were
collected is a useful answer, and its absence would be indistinguishable from
the tool never having run.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Final

from github_metrics.errors import DocumentDirectoryError
from github_metrics.model.statistics import ScanStatistics

LOGGER = logging.getLogger(__name__)

STATISTICS_FILENAME: Final = "statistics.json"
"""Name of the artifact inside the output directory."""

INDENT: Final = 2
"""Indentation. Readable in a terminal and diffable between runs, which is the
same reason the per-repository documents are indented."""


def write_statistics(root: Path, statistics: ScanStatistics) -> Path:
    """Write one run's statistics document.

    Args:
        root: The prepared output directory.
        statistics: What the run produced.

    Returns:
        The path written.

    Raises:
        DocumentDirectoryError: The file could not be written. Raised rather
            than swallowed: unlike one unwritable per-repository document, this
            file is the only record of what the run's numbers are worth, and a
            run that silently produced measurements without their bounds is the
            state this artifact exists to prevent.
    """
    path = root / STATISTICS_FILENAME
    payload = statistics.to_mapping()

    try:
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=INDENT, ensure_ascii=False)
            handle.write("\n")
    except OSError as exc:
        raise DocumentDirectoryError(f"could not write {path}: {exc}") from exc

    LOGGER.debug("Wrote statistics for %d repositories to %s", len(statistics.repositories), path)
    return path
