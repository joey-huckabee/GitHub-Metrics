"""Turning whatever was typed on the command line into an ordered list of refs.

Every collector takes the same inputs, because an analyst should not have to
remember which command accepts which. A source is one of three things:

    pypa/virtualenv                       a slug
    https://github.com/pypa/virtualenv    a URL, in any of its usual disguises
    inventory.csv                         a two-column CSV of the first form

Deciding which is which has to be predictable rather than clever, so the rules
are checked in this order and no further:

1. It looks like a URL - a scheme, an `ssh` form, or a leading `github.com/`.
2. It exists on disk as a file.
3. It ends in `.csv`, so a mistyped path reports a missing file rather than an
   invalid repository name, which is the diagnosis someone can act on.
4. Otherwise it is a slug.

The consequence worth stating: a repository whose name ends in `.csv` has to be
given as a URL. That is a real limitation and a cheap one, and it is better
than a rule that guesses.

Order is the input's order. Sources are read concurrently, but the results are
assembled by walking the arguments as written, so two runs of the same command
produce the same file and a diff between them shows changed data rather than
reordered rows.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from github_metrics.errors import ISSUE_DUPLICATE, RowIssue
from github_metrics.sources.csv_inventory import RepositoryRef, read_repository_csvs
from github_metrics.sources.reference import looks_like_a_url, parse_reference

LOGGER = logging.getLogger(__name__)

CSV_SUFFIX: Final = ".csv"
ARGUMENT_SOURCE: Final = "<argument>"
"""What an issue names when the reference came from the command line."""


@dataclass(slots=True)
class ResolvedSources:
    """Every reference the command line named, and everything wrong with it.

    Attributes:
        repositories: Accepted references, in the order the arguments were
            written, with repetitions removed.
        issues: Problems, in the order encountered.
        rows_read: References examined, including the ones refused.
        files: Paths that were read as CSV inventories, in argument order.
    """

    repositories: list[RepositoryRef] = field(default_factory=list)
    issues: list[RowIssue] = field(default_factory=list)
    rows_read: int = 0
    files: list[Path] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when every reference was accepted."""
        return not self.issues

    @property
    def accepted(self) -> int:
        """Number of references accepted."""
        return len(self.repositories)

    @property
    def rejected(self) -> int:
        """Number of references examined that produced nothing."""
        return self.rows_read - self.accepted


def is_csv_source(value: str) -> bool:
    """Whether an argument should be read as a CSV inventory.

    Args:
        value: One command-line argument.

    Returns:
        True for a path to read, False for a repository to name.
    """
    if looks_like_a_url(value):
        return False
    return Path(value).is_file() or value.casefold().endswith(CSV_SUFFIX)


def resolve_sources(
    values: Sequence[str],
    *,
    strict: bool = False,
    max_workers: int | None = None,
) -> ResolvedSources:
    """Read every source named on the command line.

    Args:
        values: The arguments, in the order they were written.
        strict: Promote the first problem to an exception, as ingestion does.
        max_workers: Threads for reading CSV files. Defaults to
            `min(files, 8)`.

    Returns:
        The accepted references and the problems, in input order.

    Raises:
        IngestError: In strict mode, or when a file cannot be read at all.
    """
    paths = [Path(value) for value in values if is_csv_source(value)]
    per_file = (
        list(read_repository_csvs(paths, strict=strict, max_workers=max_workers)) if paths else []
    )

    LOGGER.debug(
        "Resolving %d source(s): %d file(s), %d named directly",
        len(values),
        len(paths),
        len(values) - len(paths),
    )

    resolved = ResolvedSources(files=paths)
    seen: dict[tuple[str, str], str] = {}
    files = iter(per_file)

    for value in values:
        if is_csv_source(value):
            result = next(files)
            resolved.rows_read += result.rows_read
            resolved.issues.extend(result.issues)
            _keep(resolved, result.repositories, seen, str(result.source))
            continue

        resolved.rows_read += 1
        parsed = parse_reference(value, source=ARGUMENT_SOURCE)
        if isinstance(parsed, RowIssue):
            resolved.issues.append(parsed)
            continue
        _keep(resolved, [parsed], seen, ARGUMENT_SOURCE)

    LOGGER.info(
        "Resolved %d repositories from %d source(s) (%d rejected)",
        resolved.accepted,
        len(values),
        resolved.rejected,
    )
    return resolved


def _keep(
    resolved: ResolvedSources,
    references: Sequence[RepositoryRef],
    seen: dict[tuple[str, str], str],
    source: str,
) -> None:
    """Add references, refusing one already named by an earlier source.

    Within a file the CSV reader already removes repetitions. This catches the
    case it cannot see: the same repository in two files, or in a file and on
    the command line. Collecting it twice would spend the rate limit twice and
    put two identical rows in the output, which no consumer can tell apart from
    a genuine duplicate in the inventory.
    """
    for reference in references:
        first = seen.get(reference.key)
        if first is not None:
            resolved.issues.append(
                RowIssue(
                    code=ISSUE_DUPLICATE,
                    message=(
                        f"{reference.full_name} was already named by {first}; "
                        "collecting it twice would spend the rate limit twice"
                    ),
                    line=reference.source_line,
                    source=source,
                )
            )
            continue
        seen[reference.key] = source
        resolved.repositories.append(reference)
