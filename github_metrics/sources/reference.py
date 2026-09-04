"""Turning one written repository reference into a `RepositoryRef`.

An analyst names a repository in whichever form is nearest to hand. Most often
that is the slug, `pypa/virtualenv`; almost as often it is whatever was in the
address bar, which carries a scheme, a host, sometimes `www.`, sometimes a
trailing `.git`, and frequently a sub-path left over from browsing the issues.
Refusing those would be refusing a correct answer on a technicality.

So the forms below are accepted and reduced to the same two values. Nothing
here reaches the network: a reference is what the input asked for, and whether
it exists is a question for collection.

    pypa/virtualenv
    https://github.com/pypa/virtualenv
    https://www.github.com/pypa/virtualenv/
    http://github.com/pypa/virtualenv.git
    github.com/pypa/virtualenv/tree/main/docs
    git@github.com:pypa/virtualenv.git

A host other than GitHub's is refused rather than assumed. `GITHUB_API_URL`
can point collection at a GitHub Enterprise instance, but a URL is a claim
about where a repository lives, and quietly reading `gitlab.com/foo/bar` as
`foo/bar` would turn a mistake into a plausible row. On Enterprise, name the
repository by its slug.
"""

from __future__ import annotations

import logging
import re
from typing import Final
from urllib.parse import urlsplit

from github_metrics.errors import (
    ISSUE_FOREIGN_HOST,
    ISSUE_INVALID_OWNER,
    ISSUE_INVALID_REPOID,
    ISSUE_MALFORMED_REFERENCE,
    RowIssue,
)
from github_metrics.sources.csv_inventory import RepositoryRef
from github_metrics.validation import validate_owner, validate_repoid

LOGGER = logging.getLogger(__name__)

GITHUB_HOSTS: Final = frozenset({"github.com", "www.github.com"})
"""Hosts a URL may name. Anything else is refused rather than assumed."""

_SSH: Final = re.compile(r"^(?:ssh://)?git@(?P<host>[^:/]+)[:/](?P<path>.+)$")
"""`git@github.com:owner/repo.git`, the form the clone button offers."""

_SCHEME: Final = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")


def looks_like_a_url(text: str) -> bool:
    """Whether a reference is written as a URL rather than as a slug.

    Args:
        text: The reference as written.

    Returns:
        True if it carries a scheme, an `@` host, or a leading `github.com/`.
    """
    stripped = text.strip()
    return bool(
        _SCHEME.match(stripped)
        or _SSH.match(stripped)
        or stripped.casefold().split("/", 1)[0] in GITHUB_HOSTS
    )


def parse_reference(text: str, *, source: str = "<argument>") -> RepositoryRef | RowIssue:
    """Read one repository reference.

    Args:
        text: The reference, as a slug or a GitHub URL.
        source: What to name in an issue, for a message that says where the
            bad value came from.

    Returns:
        The reference, or a `RowIssue` describing why it was refused. A reason
        rather than a boolean, because "invalid" is not actionable and
        "repository name may not contain '/'" is.
    """
    stripped = text.strip()
    if not stripped:
        return RowIssue(code=ISSUE_MALFORMED_REFERENCE, message="empty reference", source=source)

    owner, repoid, refusal, code = (
        _split_url(stripped) if looks_like_a_url(stripped) else _split_slug(stripped)
    )
    if refusal is not None:
        LOGGER.debug("Refused reference %r: %s", stripped, refusal)
        return RowIssue(code=code, message=refusal, source=source)

    for reason, code in (
        (validate_owner(owner), ISSUE_INVALID_OWNER),
        (validate_repoid(repoid), ISSUE_INVALID_REPOID),
    ):
        if reason is not None:
            LOGGER.debug("Refused reference %r: %s", stripped, reason)
            return RowIssue(code=code, message=reason, source=source)

    LOGGER.debug("Reference %r read as %s/%s", stripped, owner, repoid)
    return RepositoryRef(owner=owner, repoid=repoid)


SLUG_PARTS: Final = 2
"""A reference names exactly two things: an owner and a repository.

The number appears in three comparisons - the slug split, the minimum a URL
path must carry, and the point past which a URL is carrying browsing debris -
and they are the same fact each time.
"""


def _split_slug(text: str) -> tuple[str, str, str | None, str]:
    """Split `owner/repoid`, reporting the shape rather than just rejecting it."""
    parts = text.split("/")
    if len(parts) != SLUG_PARTS:
        described = "no '/'" if len(parts) == 1 else f"{len(parts) - 1} '/' separators"
        return (
            "",
            "",
            f"{text!r} is not owner/repoid: it has {described}",
            ISSUE_MALFORMED_REFERENCE,
        )
    return parts[0].strip(), parts[1].strip(), None, ""


def _split_url(text: str) -> tuple[str, str, str | None, str]:
    """Split a GitHub URL, tolerating the decorations a browser or clone adds."""
    ssh = _SSH.match(text)
    if ssh:
        host, path = ssh.group("host"), ssh.group("path")
    else:
        candidate = text if _SCHEME.match(text) else f"https://{text}"
        split = urlsplit(candidate)
        host, path = split.netloc, split.path

    host = host.split("@")[-1].split(":")[0].casefold()
    if host not in GITHUB_HOSTS:
        return (
            "",
            "",
            f"{host or 'that host'} is not GitHub. On GitHub Enterprise, name the "
            "repository as owner/repoid and point GITHUB_API_URL at the instance",
            ISSUE_FOREIGN_HOST,
        )

    segments = [segment for segment in path.split("/") if segment]
    if len(segments) < SLUG_PARTS:
        return (
            "",
            "",
            f"{text!r} names no repository: a GitHub URL needs owner and name",
            ISSUE_MALFORMED_REFERENCE,
        )

    owner, repoid = segments[0], segments[1]
    if repoid.endswith(".git"):
        # The clone URL. Stripping it is the difference between accepting what
        # the clone button produced and refusing it over four characters.
        repoid = repoid[: -len(".git")]

    if len(segments) > SLUG_PARTS:
        # `/tree/main`, `/issues/42`, `/blob/...`: browsing debris, not part of
        # the reference, and dropping it silently would be the wrong kind of
        # quiet - a URL naming a file still names the repository.
        LOGGER.debug(
            "Ignored %d trailing URL segments after %s/%s", len(segments) - 2, owner, repoid
        )

    return owner, repoid, None, ""
