"""Recovering the accounts GitHub declined to link.

GitHub links only the **first 500 author email addresses** in a repository to
accounts. Everything past that arrives as an *anonymous* entry: a name, an
email, a commit count, and nothing else - no login, no id, no location.

Most of those are unreachable. But a large share of them publish a GitHub
no-reply address, and GitHub's own format for one embeds the account it belongs
to::

    69859316+dk96-os@users.noreply.github.com
    ^^^^^^^^ ^^^^^^^
    id       login

That is GitHub's construction rather than a guess, and it round-trips:
`user(login: "dk96-os")` returns `databaseId 69859316`. So an entry the
contributors endpoint refused to link can be linked here, at no risk of
inventing anybody.

Measured on `NousResearch/hermes-agent`: 767 of 2,914 anonymous entries carry
such an address, taking coverage from 396 people and 87.0% of commits to
**1,163 people and 90.3% of commits**.

What cannot be recovered, and why not
-------------------------------------
The remaining entries publish a real address - `elmir.jagudin@maxiv.lu.se`,
`ebraun@o2.pl`. **GitHub exposes no email-to-user lookup**, deliberately, so no
API turns those into accounts. They are counted, with their commits, and left
alone. Guessing from a display name would be inventing a person.

An identity is an email address, not a person
---------------------------------------------
This endpoint identifies contributors by *author email*, so one human with two
git configurations is two anonymous entries - observed directly, the same
display name against `ebraun@o2.pl` and `chris.szafranek@zalando.de`. The
identity count therefore over-counts people by an unknown margin, and
`docs/METRICS.md` says so where the field is defined. It is still the right
denominator: it is what GitHub attributes commits to.

The cost, and why this is a flag
--------------------------------
Unlike the census, this needs the pages themselves - 34 requests for a large
repository against the 4 a normal collection costs. That is affordable for a
repository and expensive for an inventory, so it is a flag rather than an
assumption.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Final

from github.GithubException import GithubException

from github_metrics.client import PER_PAGE, GitHubClient
from github_metrics.collect.contributors import ContributorAccount
from github_metrics.errors import ContributorCollectionError

LOGGER = logging.getLogger(__name__)

NOREPLY = re.compile(
    r"^(?P<id>\d+)\+(?P<login>[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38})"
    r"@users\.noreply\.github\.com$",
    re.IGNORECASE,
)
"""GitHub's own no-reply format, with the account id and login it embeds.

The login part follows GitHub's account grammar rather than `.+`, so a
malformed address cannot produce a login that could never exist. Bounded
repetition throughout, so it cannot backtrack super-linearly on a hostile
string - the same care `scripts/build-trace-matrix.py` needed.

An older form, `login@users.noreply.github.com`, carries no id and is
deliberately **not** matched: without the id there is nothing distinguishing a
real account from a plausible-looking string, and this module's whole claim is
that it invents nobody.
"""

ANONYMOUS_TYPE: Final = "Anonymous"
"""What GitHub calls an entry it could not link to an account."""

MAX_PAGES: Final = 100
"""Pages to walk before giving up.

At `PER_PAGE` of 100 this covers 10,000 identities, which is far past anything
observed. It exists so a paginating bug cannot turn into an unbounded run.
"""


@dataclass(frozen=True, slots=True)
class AnonymousTally:
    """What the anonymous tail of a contributor list contains.

    Attributes:
        identities: Anonymous entries seen. Each is an author *email address*,
            so one person may account for several.
        commits: Their commits, summed. Known only because the pages were
            walked; the cheap census cannot report this.
        recovered: Accounts rescued from a no-reply address, ready to collect
            like any other. **Fewer than `recovered_identities`** whenever
            someone committed under more than one no-reply address.
        recovered_identities: Anonymous *entries* those accounts came from.
            This is the figure the coverage breakdown uses, because the
            breakdown is of identities and mixing the two units would stop it
            adding up.
        unrecoverable_people: Entries publishing a real address, which no API
            resolves.
        unrecoverable_commits: Their commits.
    """

    identities: int = 0
    commits: int = 0
    recovered: tuple[ContributorAccount, ...] = ()
    recovered_identities: int = 0
    unrecoverable_people: int = 0
    unrecoverable_commits: int = 0


@dataclass
class _Recovered:
    """One account being assembled from possibly several no-reply addresses."""

    login: str
    github_id: str
    contribution: int = 0
    addresses: int = field(default=0)


def collect_anonymous(
    client: GitHubClient,
    owner: str,
    repoid: str,
    *,
    per_page: int = PER_PAGE,
) -> AnonymousTally:
    """Walk the anonymous tail and recover what can be recovered.

    Args:
        client: An authenticated client.
        owner: The owner as the inventory wrote it.
        repoid: The repository name as the inventory wrote it.
        per_page: Entries per page, at most 100.

    Returns:
        What the tail contained, and the accounts rescued from it.

    Raises:
        ContributorCollectionError: A page could not be read. Raised as this
            type so the runner degrades the repository rather than ending the
            run.
    """
    slug = f"{owner}/{repoid}"
    recovered: dict[str, _Recovered] = {}
    identities = commits = unrecoverable_people = unrecoverable_commits = 0

    for page in range(1, MAX_PAGES + 1):
        entries = _page(client, slug, page=page, per_page=per_page)
        if not entries:
            break

        for entry in entries:
            if entry.get("type") != ANONYMOUS_TYPE:
                # A linked account, already collected by the normal path.
                continue
            identities += 1
            contribution = int(entry.get("contributions") or 0)
            commits += contribution
            if not _recover(entry, contribution, recovered):
                unrecoverable_people += 1
                unrecoverable_commits += contribution

        if len(entries) < per_page:
            break

    accounts = tuple(
        ContributorAccount(
            login=item.login,
            github_id=item.github_id,
            contribution=item.contribution,
        )
        for item in recovered.values()
    )
    LOGGER.debug(
        "%s: %d anonymous identities holding %d commits; %d recovered into %d "
        "accounts, %d unrecoverable",
        slug,
        identities,
        commits,
        sum(item.addresses for item in recovered.values()),
        len(accounts),
        unrecoverable_people,
    )
    return AnonymousTally(
        identities=identities,
        commits=commits,
        recovered=accounts,
        recovered_identities=sum(item.addresses for item in recovered.values()),
        unrecoverable_people=unrecoverable_people,
        unrecoverable_commits=unrecoverable_commits,
    )


def _page(client: GitHubClient, slug: str, *, page: int, per_page: int) -> list[dict[str, Any]]:
    """Fetch one page of the anonymous-inclusive contributor list."""
    try:
        _, payload = client.contributors_page(slug, page=page, per_page=per_page, anonymous=True)
    except GithubException as exc:
        raise ContributorCollectionError(
            f"{slug}: could not read anonymous contributors: {exc}"
        ) from exc
    if not isinstance(payload, list):
        return []
    return [entry for entry in payload if isinstance(entry, dict)]


def _recover(entry: dict[str, Any], contribution: int, into: dict[str, _Recovered]) -> bool:
    """Link one anonymous entry to an account, if its address carries one.

    Args:
        entry: The anonymous entry as GitHub returned it.
        contribution: Its commits.
        into: Accounts recovered so far, keyed by login and mutated in place.

    Returns:
        Whether the entry was recovered. A login already present has its
        commits added rather than replaced: one account can hold several
        no-reply addresses, and keeping only the last would understate it.
    """
    match = NOREPLY.match(str(entry.get("email") or ""))
    if match is None:
        return False

    login = match.group("login")
    existing = into.get(login)
    if existing is None:
        into[login] = _Recovered(
            login=login,
            github_id=match.group("id"),
            contribution=contribution,
            addresses=1,
        )
    else:
        existing.contribution += contribution
        existing.addresses += 1
    return True
