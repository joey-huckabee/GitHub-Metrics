"""Read repository lists from CSV.

This module is deliberately inert: it turns a CSV file into validated
`RepositoryRef` values and reports everything wrong with the input. It performs
no network access and collects no metrics. Nothing here talks to GitHub.

Input format
------------
A header row naming at least `owner` and `repoid`, then one row per
repository::

    owner,repoid
    urllib3,urllib3
    bokeh,bokeh
    pypa,virtualenv

Each row denotes `https://github.com/<owner>/<repoid>`.

Column matching is case-insensitive and whitespace-insensitive, the two columns
may appear in either order, and unrecognised columns are ignored rather than
rejected, so a spreadsheet carrying extra bookkeeping columns still loads.

Failure model
-------------
File-level problems raise (see `github_metrics.errors`). Row-level problems do
not: they accumulate as `RowIssue` records on the returned `IngestResult`, so
one bad row in a large file is reported precisely instead of aborting the read.
Passing `strict=True` inverts this and promotes the first row issue to a
`StrictModeError`.
"""

from __future__ import annotations

import csv
import io
import logging
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from dataclasses_json import DataClassJsonMixin

from github_metrics.errors import (
    ISSUE_DUPLICATE,
    ISSUE_EMPTY_OWNER,
    ISSUE_EMPTY_REPOID,
    ISSUE_FIELD_COUNT,
    ISSUE_INVALID_OWNER,
    ISSUE_INVALID_REPOID,
    HeaderError,
    MalformedCsvError,
    RowIssue,
    SourceDecodeError,
    SourceEmptyError,
    SourceNotFoundError,
    SourceUnreadableError,
    StrictModeError,
    truncate,
)
from github_metrics.validation import validate_owner, validate_repoid

LOGGER = logging.getLogger(__name__)

OWNER_COLUMN: Final = "owner"
REPOID_COLUMN: Final = "repoid"
REQUIRED_COLUMNS: Final = (OWNER_COLUMN, REPOID_COLUMN)

DEFAULT_MAX_WORKERS: Final = 8
"""Upper bound on threads used to read several files at once.

Reading a list of CSVs is I/O bound, so threads help, but the useful range is
narrow: past a handful of concurrent reads the bottleneck is the storage
device, not the waiting. Eight keeps the win without spawning a thread per file
for a directory of hundreds.
"""


@dataclass(frozen=True, slots=True)
class RepositoryRef(DataClassJsonMixin):
    """One repository named by the input list.

    This is a *reference*, not a repository: it records what the input asked
    for and where the request came from. Nothing here has been confirmed to
    exist on GitHub. Syntactic validity is not existence, and establishing
    existence would need the network access this module deliberately avoids.

    Attributes:
        owner: The GitHub account (user or organisation) owning the repository.
        repoid: The repository name within that account.
        source_line: 1-based line of the CSV this reference came from, kept so
            a later failure can be traced back to the row that requested it.
    """

    owner: str
    repoid: str
    source_line: int | None = None

    @property
    def full_name(self) -> str:
        """The `owner/name` slug used throughout the GitHub API."""
        return f"{self.owner}/{self.repoid}"

    @property
    def url(self) -> str:
        """The repository's canonical `https://github.com/...` URL."""
        return f"https://github.com/{self.owner}/{self.repoid}"

    @property
    def key(self) -> tuple[str, str]:
        """Case-folded identity, used for duplicate detection.

        GitHub treats account and repository names case-insensitively, so
        `PyPA/virtualenv` and `pypa/virtualenv` name the same repository and
        must collide.
        """
        return (self.owner.casefold(), self.repoid.casefold())


@dataclass(slots=True)
class IngestResult(DataClassJsonMixin):
    """Everything one CSV yielded, including what was wrong with it.

    Attributes:
        source: Path of the file that was read.
        repositories: Accepted references, in file order, duplicates removed.
        issues: Row-level problems, in the order encountered.
        rows_read: Data rows examined, excluding the header and blank lines.
    """

    source: str
    repositories: list[RepositoryRef] = field(default_factory=list)
    issues: list[RowIssue] = field(default_factory=list)
    rows_read: int = 0

    @property
    def ok(self) -> bool:
        """True when every data row was accepted."""
        return not self.issues

    @property
    def accepted(self) -> int:
        """Number of references accepted."""
        return len(self.repositories)

    @property
    def rejected(self) -> int:
        """Number of data rows that produced no reference."""
        return self.rows_read - self.accepted


def _normalise_header(raw: Sequence[str]) -> list[str]:
    """Lower-case and strip header cells so column matching is forgiving."""
    return [cell.strip().lstrip("\ufeff").casefold() for cell in raw]


def _resolve_columns(header: Sequence[str], source: Path) -> tuple[int, int]:
    """Locate the required columns in a header row.

    Args:
        header: The already-normalised header cells.
        source: Path used in the error message.

    Returns:
        The `(owner_index, repoid_index)` pair.

    Raises:
        HeaderError: If either required column is absent, or declared twice.
    """
    indices: dict[str, int] = {}
    for position, name in enumerate(header):
        if name not in REQUIRED_COLUMNS:
            continue
        if name in indices:
            raise HeaderError(
                f"{source}: column {name!r} is declared more than once; "
                f"header was {list(header)!r}"
            )
        indices[name] = position

    missing = [name for name in REQUIRED_COLUMNS if name not in indices]
    if missing:
        raise HeaderError(
            f"{source}: header is missing required column(s) "
            f"{', '.join(repr(name) for name in missing)}; expected "
            f"{', '.join(repr(name) for name in REQUIRED_COLUMNS)} "
            f"but found {list(header)!r}"
        )

    return indices[OWNER_COLUMN], indices[REPOID_COLUMN]


def _read_rows(source: Path) -> list[list[str]]:
    """Read every physical row of a CSV, translating failures to our errors.

    The whole file is materialised rather than streamed. Repository lists are
    inventories - hundreds or thousands of short rows - so the memory cost is
    trivial, and holding the rows lets a duplicate on line 900 be reported
    against its first occurrence on line 12 without a second pass.

    Args:
        source: File to read.

    Returns:
        Rows as lists of cells.

    Raises:
        SourceNotFoundError: The path does not exist.
        SourceUnreadableError: The path cannot be opened as a file.
        SourceDecodeError: The bytes are not valid UTF-8.
        MalformedCsvError: The content is binary or unparseable as CSV.
    """
    try:
        raw = source.read_bytes()
    except FileNotFoundError as exc:
        raise SourceNotFoundError(f"no such file: {source}") from exc
    except IsADirectoryError as exc:
        raise SourceUnreadableError(f"expected a CSV file but {source} is a directory") from exc
    except PermissionError as exc:
        # Windows raises PermissionError, not IsADirectoryError, when a
        # directory is opened for reading, so both paths land on the same
        # error class by different routes.
        raise SourceUnreadableError(f"could not read {source}: {exc}") from exc
    except OSError as exc:
        raise SourceUnreadableError(f"could not read {source}: {exc}") from exc

    # A NUL byte means this is a binary file that was renamed, not a CSV. The
    # csv module no longer rejects one itself, so without this check the NUL
    # travels into a cell and surfaces as an "invalid owner" row issue - once
    # per row, which for a real binary file means thousands of misleading
    # diagnostics instead of one accurate one.
    nul_at = raw.find(b"\x00")
    if nul_at != -1:
        raise MalformedCsvError(
            f"{source} contains a NUL byte at offset {nul_at}; "
            "this looks like a binary file rather than a CSV"
        )

    try:
        # utf-8-sig transparently drops a byte-order mark, which spreadsheet
        # exports on Windows almost always prepend. Without it the first header
        # cell arrives as "\ufeffowner" and the file looks headerless.
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SourceDecodeError(
            f"{source} is not valid UTF-8 at byte {exc.start}; re-export the list as UTF-8"
        ) from exc

    try:
        # newline="" hands newline handling to the csv module, which is what
        # lets it keep a newline inside a quoted field instead of splitting the
        # row there.
        return list(csv.reader(io.StringIO(text, newline="")))
    except csv.Error as exc:
        raise MalformedCsvError(f"{source} is not parseable as CSV: {exc}") from exc


def _is_blank(row: Sequence[str]) -> bool:
    """True for a row carrying no content.

    Both a genuinely empty line and a line of nothing but separators (`,,`)
    count. Trailing separators are a common artefact of spreadsheet exports and
    say nothing about the data, so they are skipped rather than reported.
    """
    return all(not cell.strip() for cell in row)


def read_repository_csv(source: Path | str, *, strict: bool = False) -> IngestResult:
    """Read one CSV of `owner,repoid` rows.

    No network access occurs and no metrics are collected. Validation is
    syntactic only: a reference that passes here names a *plausible*
    repository, not necessarily one that exists.

    Args:
        source: Path to the CSV.
        strict: When true, the first row-level issue raises `StrictModeError`
            instead of being collected. File-level problems raise either way.

    Returns:
        The accepted references and any row-level issues.

    Raises:
        SourceNotFoundError: The path does not exist.
        SourceUnreadableError: The path cannot be opened as a file.
        SourceEmptyError: The file has no header row.
        SourceDecodeError: The file is not valid UTF-8.
        HeaderError: The header lacks a required column.
        MalformedCsvError: The file is not parseable as CSV.
        StrictModeError: `strict` is set and a row-level issue was found.
    """
    path = Path(source)
    rows = _read_rows(path)

    header_index = next((i for i, row in enumerate(rows) if not _is_blank(row)), None)
    if header_index is None:
        raise SourceEmptyError(
            f"{path} contains no header row; expected a first line naming "
            f"{', '.join(REQUIRED_COLUMNS)}"
        )

    owner_at, repoid_at = _resolve_columns(_normalise_header(rows[header_index]), path)
    widest_required = max(owner_at, repoid_at)

    result = IngestResult(source=str(path))
    seen: dict[tuple[str, str], int] = {}

    def record(code: str, line: int, message: str) -> None:
        """Collect an issue, or raise it when running strictly."""
        issue = RowIssue(code=code, line=line, message=message, source=str(path))
        if strict:
            raise StrictModeError(f"{issue} (strict mode)")
        LOGGER.debug("%s", issue)
        result.issues.append(issue)

    # The physical line number is derived from the row index because every row
    # here consumed exactly one line. A quoted field spanning lines would make
    # that drift, but such a field cannot occur in a valid owner or repoid, and
    # a row containing one is rejected anyway.
    for line, row in enumerate(rows[header_index + 1 :], start=header_index + 2):
        if _is_blank(row):
            continue

        result.rows_read += 1
        echo = truncate(",".join(row))

        if len(row) <= widest_required:
            record(
                ISSUE_FIELD_COUNT,
                line,
                f"expected at least {widest_required + 1} fields but found "
                f"{len(row)}: {echo!r}",
            )
            continue

        owner = row[owner_at].strip()
        repoid = row[repoid_at].strip()

        if not owner:
            record(ISSUE_EMPTY_OWNER, line, f"owner is empty in {echo!r}")
            continue
        if not repoid:
            record(ISSUE_EMPTY_REPOID, line, f"repoid is empty in {echo!r}")
            continue

        owner_problem = validate_owner(owner)
        if owner_problem is not None:
            record(
                ISSUE_INVALID_OWNER,
                line,
                f"invalid owner {truncate(owner)!r}: {owner_problem}",
            )
            continue

        repoid_problem = validate_repoid(repoid)
        if repoid_problem is not None:
            record(
                ISSUE_INVALID_REPOID,
                line,
                f"invalid repoid {truncate(repoid)!r}: {repoid_problem}",
            )
            continue

        reference = RepositoryRef(owner=owner, repoid=repoid, source_line=line)
        first_seen = seen.get(reference.key)
        if first_seen is not None:
            record(
                ISSUE_DUPLICATE,
                line,
                f"{reference.full_name} already appears on line {first_seen}; "
                "keeping the first occurrence",
            )
            continue

        seen[reference.key] = line
        result.repositories.append(reference)

    LOGGER.info(
        "Read %d repositories from %s (%d data rows, %d rejected)",
        result.accepted,
        path,
        result.rows_read,
        result.rejected,
    )
    return result


def read_repository_csvs(
    sources: Iterable[Path | str],
    *,
    strict: bool = False,
    max_workers: int | None = None,
) -> list[IngestResult]:
    """Read several CSVs concurrently.

    Each file is independent, so they are read on a thread pool. Results come
    back in the order the sources were given, never in completion order: an
    inventory that reorders itself between runs is not diffable, and the order
    must not depend on which disk read happened to finish first.

    For the same reason, `strict=True` raises the error belonging to the
    *earliest* source in input order rather than whichever thread failed first.
    Without that rule the reported error would vary from run to run for a batch
    containing more than one bad file.

    Concurrency here is across files. Parsing within a single file stays
    sequential; see `docs/adr/0002-concurrency-across-files-not-within-a-file.md`.

    Args:
        sources: Paths to read. May be empty, in which case no threads start.
        strict: Applied to every file; see `read_repository_csv`.
        max_workers: Thread cap. Defaults to the smaller of the source count
            and `DEFAULT_MAX_WORKERS`.

    Returns:
        One result per source, in input order.

    Raises:
        IngestError: Any error `read_repository_csv` raises, for the earliest
            failing source in input order.
    """
    paths = [Path(source) for source in sources]
    if not paths:
        return []

    workers = max_workers if max_workers is not None else min(len(paths), DEFAULT_MAX_WORKERS)
    workers = max(1, workers)

    LOGGER.debug("Reading %d source(s) with %d worker(s)", len(paths), workers)

    def read_one(path: Path) -> IngestResult:
        return read_repository_csv(path, strict=strict)

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ingest") as pool:
        # Executor.map preserves input order and re-raises the first exception
        # by that order, which is exactly the determinism promised above.
        # as_completed would instead surface whichever call failed soonest.
        return list(pool.map(read_one, paths))
