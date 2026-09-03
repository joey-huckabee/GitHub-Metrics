"""Writing one JSON document per repository.

The document is `SoftwareRow` plus a `contributors` array, in that order, and
nothing else. That is not a convenience: it is what makes the two artifacts of
a run joinable. Every key in `githubmetrics.csv` is a key here, spelled the
same way, so moving between them needs no translation table - and both carry
the `scan_id` and `scan_date` of the one run that produced them.

Where the files go
------------------
    <root>/<owner>/<repoid>.json

**One directory per owner, not one flattened name.** The alternative,
`<owner>-<repoid>.json`, silently loses repositories: hyphens are legal in
both an account name and a repository name, so `foo-bar/baz` and `foo/bar-baz`
both flatten onto `foo-bar-baz.json`. Those are two different repositories
rather than a duplicate pair, so the duplicate detection that guards an
inventory (`GM-ING-015`) correctly reports nothing, one file overwrites the
other, and the run exits 0 with a repository missing. A path separator is not
a legal character in either name, so the nested form cannot express that
collision at all - it is removed by construction rather than detected.

**Always lower case.** GitHub account and repository names are
case-insensitive, so `PyPA/virtualenv` and `pypa/virtualenv` name one
repository, and `RepositoryRef.key` already folds case to say exactly that. A
case-sensitive path would contradict the tool's own notion of identity: on
Linux two spellings would produce two files for one repository, and on Windows
and macOS the second would overwrite the first while the run reported two
successes. The name grammar is ASCII-only, so `str.lower()` and
`str.casefold()` agree, and `.` and `..` are already refused as repository
names, so no segment can escape the root.

Windows reserved device names need no handling: the reservation applies to the
bare name, and these always carry a `.json` suffix. `con.json`, `nul.json` and
`com1.json` create, list and read back correctly.

**No file for a repository that could not be read.** The run warns, continues
and exits 4, and the CSV keeps its identity-only row. The asymmetry is the
point. A CSV row is positional - one row per accepted input row - so omitting
one would shift what every later row means. A directory has no positions, so
an absent file says "named, not measured" on its own. Writing the file anyway
would be worse: a document carrying an empty contributor array and a
`contribution_total` of zero is indistinguishable from a repository that
genuinely has no contributors. The CSV can afford its empty row because its
empty *fields* say "not measured"; a zero has no way to say that.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

from github_metrics.errors import DocumentDirectoryError
from github_metrics.model.contributor import Contributor
from github_metrics.model.software import SoftwareRow

LOGGER = logging.getLogger(__name__)

DEFAULT_DOCUMENT_ROOT: Final = "githubmetrics"
"""Directory holding the per-repository documents when none is named."""

CONTRIBUTORS_KEY: Final = "contributors"
"""The one key in a document that is not a column of `SoftwareRow`."""

INDENT: Final = 2
"""Documents are read by people as well as parsed, so they are indented."""


def build_document(row: SoftwareRow, contributors: Sequence[Contributor]) -> dict[str, Any]:
    """Assemble one repository's document.

    Args:
        row: The row this repository produced, already scored and stamped.
        contributors: Its contributors, most commits first.

    Returns:
        Every column of `SoftwareRow` in canonical order, then `contributors`.
        The array is last rather than in the middle so that the document's key
        order is the CSV's header followed by one addition, which is a rule a
        test can state in one sentence.
    """
    document = row.to_mapping()
    document[CONTRIBUTORS_KEY] = [entry.to_mapping() for entry in contributors]
    return document


def document_path(root: Path, owner: str, repoid: str) -> Path:
    """Work out where one repository's document belongs.

    Args:
        root: The directory holding the tree.
        owner: The owner as the inventory wrote it.
        repoid: The repository name as the inventory wrote it.

    Returns:
        `<root>/<owner>/<repoid>.json`, lower-cased throughout. Built from the
        input spelling rather than from what GitHub reported, so an operator
        can predict where a row will land without running the tool - the two
        differ only in case for a repository that collection accepted, since a
        renamed or transferred one is refused rather than collected.
    """
    return root / owner.lower() / f"{repoid.lower()}.json"


def prepare_root(root: Path | None) -> Path:
    """Resolve and create the directory the documents go in.

    Args:
        root: Where the caller wants the tree, or `None` for the default.

    Returns:
        The directory, created if it did not exist.

    Raises:
        DocumentDirectoryError: The path exists and is not a directory, or it
            could not be created. Raised before collection begins, because the
            alternative is discovering it after a run has spent an hour of
            quota.
    """
    resolved = Path(DEFAULT_DOCUMENT_ROOT) if root is None else root

    if resolved.exists() and not resolved.is_dir():
        raise DocumentDirectoryError(
            f"{resolved} exists and is not a directory, so the per-repository "
            "documents have nowhere to go"
        )

    try:
        resolved.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DocumentDirectoryError(f"could not create {resolved}: {exc}") from exc

    return resolved


def write_document(
    root: Path,
    row: SoftwareRow,
    contributors: Sequence[Contributor],
) -> Path:
    """Write one repository's document.

    Args:
        root: The directory holding the tree, already prepared.
        row: The row this repository produced.
        contributors: Its contributors, most commits first.

    Returns:
        The path written.

    Raises:
        DocumentDirectoryError: The file could not be written.
    """
    path = document_path(root, row.owner, row.name)
    payload = json.dumps(build_document(row, contributors), indent=INDENT)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # newline="\n" because this is a data interchange artifact and the
        # repository is LF throughout; the default would write CRLF here on
        # Windows and make two platforms produce different bytes for one run.
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload + "\n")
    except OSError as exc:
        raise DocumentDirectoryError(f"could not write {path}: {exc}") from exc

    LOGGER.debug("Wrote %s", path)
    return path
