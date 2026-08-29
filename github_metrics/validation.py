"""Syntactic validation of GitHub account and repository names.

The rules below mirror what github.com accepts at sign-up and at repository
creation. They are deliberately *syntactic*: a name that passes here is
well-formed, which is not the same as existing. Confirming existence needs the
network, and the ingestion path that calls this module makes no network access
at all.

Validating early is still worth it. A typo such as a pasted URL in the owner
column, or a trailing comment glued to a name, is cheap to catch here and
expensive to diagnose later as a 404 from the API among hundreds of others.

Every function returns `None` for a valid name, or a short phrase explaining
the problem, suitable for embedding in an error message. Returning the reason
rather than a bare boolean is what lets the error catalog distinguish "too
long" from "illegal character" without a second inspection pass.
"""

from __future__ import annotations

import re
from typing import Final

MAX_OWNER_LENGTH: Final = 39
"""GitHub caps account names at 39 characters."""

MAX_REPOID_LENGTH: Final = 100
"""GitHub caps repository names at 100 characters."""

_OWNER_CHARS: Final = re.compile(r"^[A-Za-z0-9-]+$")
_REPOID_CHARS: Final = re.compile(r"^[A-Za-z0-9._-]+$")

RESERVED_REPOIDS: Final = frozenset({".", ".."})
"""Names that would resolve to a path segment rather than a repository."""


def validate_owner(owner: str) -> str | None:
    """Check a GitHub account (user or organisation) name.

    The accepted grammar is one or more alphanumerics or hyphens, no more than
    `MAX_OWNER_LENGTH` characters, not beginning or ending with a hyphen, and
    with no consecutive hyphens.

    Args:
        owner: The candidate name, already stripped of surrounding whitespace.

    Returns:
        `None` when the name is well-formed, otherwise a phrase describing the
        first problem found.

    Examples:
        >>> validate_owner("pypa") is None
        True
        >>> validate_owner("https://github.com/pypa")
        "may only contain letters, digits and hyphens"
    """
    if not owner:
        return "is empty"
    if len(owner) > MAX_OWNER_LENGTH:
        return f"is {len(owner)} characters; the limit is {MAX_OWNER_LENGTH}"
    if not _OWNER_CHARS.match(owner):
        return "may only contain letters, digits and hyphens"
    if owner.startswith("-") or owner.endswith("-"):
        return "may not begin or end with a hyphen"
    if "--" in owner:
        return "may not contain consecutive hyphens"
    return None


def validate_repoid(repoid: str) -> str | None:
    """Check a GitHub repository name.

    The accepted grammar is one or more alphanumerics, hyphens, underscores or
    dots, no more than `MAX_REPOID_LENGTH` characters, not `.` or `..`, and not
    ending in `.git`.

    The `.git` exclusion matters for this project specifically: the most common
    way to produce a repository list is to paste clone URLs and strip the host,
    which leaves the suffix behind. GitHub rejects such a name, so accepting it
    here would only defer the failure to a 404 much later.

    Args:
        repoid: The candidate name, already stripped of surrounding whitespace.

    Returns:
        `None` when the name is well-formed, otherwise a phrase describing the
        first problem found.

    Examples:
        >>> validate_repoid("virtualenv") is None
        True
        >>> validate_repoid("virtualenv.git")
        "may not end in '.git'"
    """
    if not repoid:
        return "is empty"
    if len(repoid) > MAX_REPOID_LENGTH:
        return f"is {len(repoid)} characters; the limit is {MAX_REPOID_LENGTH}"
    if repoid in RESERVED_REPOIDS:
        return f"{repoid!r} is reserved"
    if not _REPOID_CHARS.match(repoid):
        return "may only contain letters, digits, hyphens, underscores and dots"
    if repoid.casefold().endswith(".git"):
        return "may not end in '.git'"
    return None
