"""Deciding where output goes."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from github_metrics.errors import OutputDestinationError

DEFAULT_FILENAME: Final = "githubmetrics.csv"
"""Filename used when the caller names a directory rather than a file."""

DEFAULT_JSON_FILENAME: Final = "githubmetrics.json"
"""Filename used for JSON output when the caller names a directory."""


def resolve_destination(
    destination: Path | str | None,
    *,
    json_format: bool = False,
) -> Path | None:
    """Work out which file to write, if any.

    Three cases, in the order a caller will meet them:

    - **Nothing given** — the caller wants the console, so no path is
      returned.
    - **An existing directory** — the caller said where but not what, so the
      default filename is used inside it.
    - **Anything else** — treated as the full path of the file to write, even
      if its parent does not exist yet.

    A path ending in a separator is treated as a directory even when it does
    not exist, since that spelling can only have been meant as one.

    Args:
        destination: What the caller asked for.
        json_format: Selects the default filename when a directory is given.

    Returns:
        The file to write, or `None` to render to the console.

    Raises:
        OutputDestinationError: If the parent directory does not exist. Failing
            here beats failing after a run has spent its API budget.
    """
    if destination is None:
        return None

    raw = str(destination)
    looks_like_directory = raw.endswith(("/", "\\"))
    path = Path(destination)

    if path.is_dir() or looks_like_directory:
        path = path / (DEFAULT_JSON_FILENAME if json_format else DEFAULT_FILENAME)

    parent = path.parent
    if not parent.exists():
        raise OutputDestinationError(f"cannot write {path}: the directory {parent} does not exist")
    if not parent.is_dir():
        raise OutputDestinationError(f"cannot write {path}: {parent} is not a directory")

    return path
