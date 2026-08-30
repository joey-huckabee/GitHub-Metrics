"""Error taxonomy for github-metrics.

Every failure this package can report carries a stable code of the form
`GM-<AREA>-<NNN>`. Codes are permanent: a retired code is never reused, so a
log line or a support ticket quoting one stays meaningful across releases. The
authoritative descriptions live in `docs/ERROR-CATALOG.md`.

Two distinct shapes appear here:

- **Exceptions** for conditions that make the whole operation impossible, such
  as a missing file or an unusable header. These abort the unit of work.
- **`RowIssue`** for conditions that spoil a single input row while leaving the
  rest of the file usable. These are data, not control flow: in lenient mode
  they accumulate on the result, and only in strict mode are they promoted to
  a `StrictModeError`.

Keeping the two apart is what lets one bad row in a thousand-row file be
reported precisely instead of collapsing the run.
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_RAW_ECHO = 120
"""Longest prefix of an offending row echoed back in an issue message."""


class GitHubMetricsError(Exception):
    """Base class for every error this package raises deliberately.

    Catching this catches everything github-metrics reports on purpose, while
    still letting genuine bugs (`TypeError`, `AttributeError`) escape.
    """

    code = "GM-000"

    def __str__(self) -> str:
        """Prefix the message with the stable error code."""
        return f"[{self.code}] {super().__str__()}"


class IngestError(GitHubMetricsError):
    """Base class for failures reading a repository list."""

    code = "GM-ING-000"


class SourceNotFoundError(IngestError):
    """The named CSV does not exist."""

    code = "GM-ING-001"


class SourceUnreadableError(IngestError):
    """The path exists but cannot be read as a file.

    Covers a directory given where a file was expected, a permission denial,
    and any other OSError raised while opening.
    """

    code = "GM-ING-002"


class SourceEmptyError(IngestError):
    """The file contains no header row.

    A zero-byte file and a file of nothing but blank lines both land here. An
    empty file is almost always a truncated download or a failed export, so it
    is reported rather than silently yielding zero repositories.
    """

    code = "GM-ING-003"


class HeaderError(IngestError):
    """The header row does not declare the required columns."""

    code = "GM-ING-004"


class SourceDecodeError(IngestError):
    """The file is not valid UTF-8."""

    code = "GM-ING-005"


class MalformedCsvError(IngestError):
    """The file is not parseable as CSV at all.

    Raised for an embedded NUL byte, which this package detects itself
    because the `csv` module passes one through, and for anything the `csv`
    module does reject, such as a field longer than the interpreter's
    field-size limit. Both indicate a binary file handed over as CSV.
    """

    code = "GM-ING-006"


class StrictModeError(IngestError):
    """A row-level issue was found while running in strict mode."""

    code = "GM-ING-020"


class CollectionError(GitHubMetricsError):
    """Base class for failures collecting data from the GitHub API."""

    code = "GM-COL-000"


class RepositoryNotFoundError(CollectionError):
    """The repository does not exist, is private, or was renamed.

    Syntactic validation at ingestion cannot detect any of these: a
    well-formed reference names a plausible repository, not one that exists.
    This is where that distinction finally surfaces.
    """

    code = "GM-COL-001"


class GraphQLQueryError(CollectionError):
    """The GraphQL API returned errors for a query.

    GraphQL answers with HTTP 200 and an `errors` array rather than an HTTP
    error status, so a failure here is invisible to any check that only looks
    at the status code.
    """

    code = "GM-COL-002"


class OutputError(GitHubMetricsError):
    """Base class for failures producing output."""

    code = "GM-OUT-000"


class UnknownFieldError(OutputError):
    """A selected field is not a column of the output."""

    code = "GM-OUT-001"


class OutputDestinationError(OutputError):
    """The requested output file cannot be written.

    Raised before collection begins where possible. Discovering that the
    destination directory does not exist after a run has spent its API budget
    is the expensive way to find out.
    """

    code = "GM-OUT-002"


# --------------------------------------------------------------------------
# Row-level issue codes
# --------------------------------------------------------------------------

ISSUE_FIELD_COUNT = "GM-ING-010"
"""A data row has a different number of fields than the header declares."""

ISSUE_EMPTY_OWNER = "GM-ING-011"
"""The `owner` cell is empty or whitespace only."""

ISSUE_EMPTY_REPOID = "GM-ING-012"
"""The `repoid` cell is empty or whitespace only."""

ISSUE_INVALID_OWNER = "GM-ING-013"
"""The `owner` cell is not a syntactically valid GitHub account name."""

ISSUE_INVALID_REPOID = "GM-ING-014"
"""The `repoid` cell is not a syntactically valid GitHub repository name."""

ISSUE_DUPLICATE = "GM-ING-015"
"""The row repeats a repository already seen in this file."""


@dataclass(frozen=True, slots=True)
class RowIssue:
    """One problem with one input row.

    Attributes:
        code: A stable `GM-ING-0NN` code from this module.
        line: The 1-based physical line number in the source file. Physical
            rather than logical, so it matches what an editor shows even when
            a quoted field spans several lines.
        message: A human-readable description naming the offending value.
        source: The file the row came from, for messages that aggregate
            several files.
    """

    code: str
    line: int
    message: str
    source: str | None = None

    def __str__(self) -> str:
        """Render as `path:line: [CODE] message`, the usual compiler shape."""
        where = f"{self.source}:{self.line}" if self.source else f"line {self.line}"
        return f"{where}: [{self.code}] {self.message}"


def truncate(value: str, limit: int = MAX_RAW_ECHO) -> str:
    """Shorten a value for safe echoing in an error message.

    A malformed CSV can carry a megabyte on one line; echoing it whole would
    flood the log that is supposed to explain the problem.

    Args:
        value: The text to shorten.
        limit: Maximum number of characters to keep.

    Returns:
        `value` unchanged if short enough, otherwise a prefix with an ellipsis.
    """
    if len(value) <= limit:
        return value
    return value[:limit] + "\u2026"
