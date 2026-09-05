"""Counting every contributor identity, including the ones with no account.

The contributor list this tool collects is bounded by GitHub linking only the
**first 500 author email addresses** in a repository to accounts. Everything
past that is reported as an *anonymous* entry carrying a name and an email and
nothing else - no login, no id, no location.

Those entries are not worth collecting: there is no account to look up, no
location to geocode, and no residency determination any rule could make. But
their **count** is worth everything, because without it
`contributors.coverage_percent` reads 100% for a repository where the real
figure is 12%, and a number that overstates its own completeness is worse than
no number at all.

One request, not thirty-four
---------------------------
The obvious way to count them is to page the whole `anon=1` list, which is 34
requests for a large repository and would make REST the binding budget for a
whole inventory.

It is not necessary. GitHub paginates this endpoint by offset and returns a
`rel="last"` link, so **`per_page=1` makes the last page number the total
count**. One request, whatever the repository's size:

    NousResearch/hermes-agent -> page=3310 -> 3,310 identities
    pypa/virtualenv           -> page=161  -> 161 identities

That is cheap enough to do for every repository by default, which is why the
honest denominator is on rather than opt-in.

What this cannot tell you
-------------------------
The count alone says how many identities exist, not how many commits they
hold - that needs the pages themselves, which is what
`collect.anonymous` is for and why it is not free. `Exclusion.commits` is
`None` rather than `0` when only the census ran, because zero would claim the
anonymous tail contributed nothing.
"""

from __future__ import annotations

import logging
import re
from typing import Final

from github.GithubException import GithubException

from github_metrics.client import GitHubClient
from github_metrics.errors import ContributorCollectionError

LOGGER = logging.getLogger(__name__)

LAST_LINK = re.compile(r'<([^>]*)>;[^,]*?rel="last"')
r"""Isolates the `rel="last"` entry of a `Link` header.

Anchored on `rel="last"` specifically: the header also carries `next`, `first`
and `prev`, and matching the wrong one would report a count that is merely the
current position.

Two steps rather than one pattern, because the page number is **not** at the
end of the URL - GitHub writes `...&page=3310&per_page=1`, so a pattern
expecting `page=(\d+)>` matches nothing and silently falls through to a count
of one. `[^>]*` and `[^,]*?` are both bounded by characters that cannot appear
in what they consume, so neither can backtrack super-linearly.
"""

PAGE_NUMBER = re.compile(r"[?&]page=(\d+)")
"""Reads the page number out of one link URL."""


CENSUS_PER_PAGE: Final = 1
"""One entry per page, so the last page number *is* the total count."""


def count_identities(client: GitHubClient, owner: str, repoid: str) -> int | None:
    """Count every contributor identity GitHub reports, anonymous included.

    Args:
        client: An authenticated client.
        owner: The owner as the inventory wrote it.
        repoid: The repository name as the inventory wrote it.

    Returns:
        The number of identities, or `None` when the count could not be read.
        `None` rather than a fallback: an invented denominator would make
        coverage look measured when it was guessed.

    Raises:
        ContributorCollectionError: The request itself failed. Raised as this
            type so the runner degrades the repository rather than ending the
            run, the same as every other contributor-side failure.
    """
    slug = f"{owner}/{repoid}"
    try:
        headers, payload = client.contributors_page(
            slug, page=1, per_page=CENSUS_PER_PAGE, anonymous=True
        )
    except GithubException as exc:
        raise ContributorCollectionError(
            f"{slug}: could not count contributor identities: {exc}"
        ) from exc

    total = _last_page(headers.get("link") or headers.get("Link") or "")
    if total is not None:
        LOGGER.debug("%s: %d contributor identities including anonymous", slug, total)
        return total

    # No `rel="last"` means there is only one page, so the count is however
    # many entries came back - which for per_page=1 is one, or zero for a
    # repository with no contributors at all.
    if isinstance(payload, list):
        LOGGER.debug("%s: %d contributor identities (single page)", slug, len(payload))
        return len(payload)

    LOGGER.warning("%s: contributor identity count could not be read", slug)
    return None


def _last_page(link: str) -> int | None:
    """Read the final page number out of a `Link` header.

    Args:
        link: The raw header value, or `""` when absent.

    Returns:
        The last page number, or `None` when the header carries no `rel="last"`
        entry - which is what a single-page result looks like.
    """
    entry = LAST_LINK.search(link)
    if entry is None:
        return None
    page = PAGE_NUMBER.search(entry.group(1))
    return int(page.group(1)) if page else None
