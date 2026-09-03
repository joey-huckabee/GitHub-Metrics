"""Collecting the contributor block of a per-repository document.

Two calls per repository, and the split between them is forced by the API
rather than chosen.

**The list comes from REST.** GraphQL has no connection that reports commits
attributed per account: `mentionableUsers` and `assignableUsers` list people
without counting anything, and `history` counts commits without grouping them
by author in one query. `GET /repos/{owner}/{repo}/contributors` is the only
route that answers the question, so the list is one REST request against a
budget the rest of a run barely touches.

**The details come from GraphQL, in one query for the whole list.** The REST
contributors payload is a minimal account object - login, id, avatar - and
carries no name, company or location. Reading those through PyGithub would
complete each account lazily, which is **one REST request per contributor**:
at a limit of 25 that is 26 requests per repository, so a 200-repository
inventory exhausts REST's 5,000-per-hour budget before it finishes. Aliasing
the accounts into a single GraphQL document asks for all of them at once, and
because every alias selects a single object rather than a connection, the
document has no `nodes` selection and stays at the cheap end of the cost
formula.

That is the same reasoning `collect/repository.py` applies to the metrics
query, for the same reason: the expensive shape is the one that prices by how
many objects could come back.

Aliases are positional
----------------------
A GraphQL alias must match `[_A-Za-z][_0-9A-Za-z]*`, and a GitHub login may
contain hyphens, so a login cannot be its own alias. The aliases are indexes
into the list instead, which needs no escaping and cannot collide.

What a missing account means
----------------------------
An account can be deleted or suspended between the REST list and the GraphQL
lookup, and GitHub answers that alias with `null` rather than failing the
document. The contributor is still recorded, with the login as its name and
nothing resolved, because its `contribution` is a real measurement of this
repository and dropping the record would quietly reduce `contribution_total`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from github.GithubException import GithubException

from github_metrics.client import GitHubClient
from github_metrics.collect.graphql import execute
from github_metrics.errors import ContributorCollectionError
from github_metrics.model.contributor import Address, Contributor

if TYPE_CHECKING:  # pragma: no cover
    from github.NamedUser import NamedUser

    from github_metrics.geo import Geocoder

LOGGER = logging.getLogger(__name__)

DEFAULT_CONTRIBUTOR_LIMIT: Final = 25
"""Contributors inspected per repository, ranked by commits.

Fixed rather than configurable. GitHub orders the list by contribution
descending, so the first 25 accounts carry the great majority of a
repository's commits, while the tail is long enough that collecting all of it
would let a run's cost be set by its largest repository. `contribution_total`
counts what was collected, and `docs/METRICS.md` says so.
"""


@dataclass(frozen=True, slots=True)
class ContributorAccount:
    """One entry of the REST contributors list, before any detail is added.

    Attributes:
        login: The account's login, used to look its detail up.
        github_id: The account's numeric id, as a string.
        contribution: Commits attributed to the account in this repository.
    """

    login: str
    github_id: str
    contribution: int


def _details_query(count: int) -> str:
    """Build a document asking for `count` accounts by alias.

    Args:
        count: How many accounts the document should ask about.

    Returns:
        A GraphQL document with one aliased `user` selection per account.
        Single-object selections only - no connection, and so no `nodes`.
    """
    fields = "{ databaseId name company location }"
    variables = ", ".join(f"$login{index}: String!" for index in range(count))
    selections = "\n".join(
        f"  u{index}: user(login: $login{index}) {fields}" for index in range(count)
    )
    return f"query({variables}) {{\n{selections}\n}}\n"


def get_contributor_accounts(
    client: GitHubClient,
    owner: str,
    repoid: str,
    *,
    limit: int = DEFAULT_CONTRIBUTOR_LIMIT,
) -> list[ContributorAccount]:
    """Fetch the ranked contributor list for one repository.

    Args:
        client: An authenticated client.
        owner: The owner as the inventory wrote it.
        repoid: The repository name as the inventory wrote it.
        limit: How many contributors to keep, ranked by commits descending.

    Returns:
        Up to `limit` accounts, most commits first.

    Raises:
        ContributorCollectionError: The list could not be read.
    """
    slug = f"{owner}/{repoid}"
    try:
        repository = client.repository(slug)
        # PyGithub's paginated list is untyped, so the slice is too.
        accounts: list[NamedUser] = list(repository.get_contributors()[:limit])
    except GithubException as exc:
        raise ContributorCollectionError(f"{slug}: could not read contributors: {exc}") from exc

    if len(accounts) == limit:
        # The aggregate that follows counts what was collected, not what
        # exists. Saying so at DEBUG is what makes a total reconcilable later.
        LOGGER.debug("%s: contributor list truncated at the limit of %d", slug, limit)

    return [
        ContributorAccount(
            login=str(account.login),
            github_id=str(account.id),
            contribution=int(account.contributions),
        )
        for account in accounts
    ]


def get_account_details(
    client: GitHubClient,
    accounts: list[ContributorAccount],
    *,
    slug: str,
) -> dict[str, dict[str, Any]]:
    """Fetch name, company and location for every account, in one query.

    Args:
        client: An authenticated client.
        accounts: The accounts to look up.
        slug: The repository the accounts came from, for messages.

    Returns:
        Login to the account's detail payload. An account GitHub answered with
        `null` - deleted or suspended since the list was read - is absent.
    """
    if not accounts:
        return {}

    variables = {f"login{index}": account.login for index, account in enumerate(accounts)}
    data = execute(
        client,
        _details_query(len(accounts)),
        variables,
        description=f"contributor detail for {slug}",
    )

    details: dict[str, dict[str, Any]] = {}
    for index, account in enumerate(accounts):
        payload = data.get(f"u{index}")
        if isinstance(payload, dict):
            details[account.login] = payload
        else:
            LOGGER.debug("%s: no detail for %s; the account may be gone", slug, account.login)
    return details


def get_contributors(
    client: GitHubClient,
    owner: str,
    repoid: str,
    *,
    geocoder: Geocoder | None = None,
    limit: int = DEFAULT_CONTRIBUTOR_LIMIT,
) -> list[Contributor]:
    """Collect the contributor block for one repository.

    Args:
        client: An authenticated client.
        owner: The owner as the inventory wrote it.
        repoid: The repository name as the inventory wrote it.
        geocoder: Resolves locations to addresses. When `None`, every address
            is left unresolved, which stays distinguishable from a lookup that
            found nothing.
        limit: How many contributors to keep, ranked by commits descending.

    Returns:
        The contributors, most commits first. The run identity is not stamped
        here; `analysis.row` applies it alongside the row's own.

    Raises:
        ContributorCollectionError: The contributor list could not be read.
    """
    slug = f"{owner}/{repoid}"
    accounts = get_contributor_accounts(client, owner, repoid, limit=limit)
    details = get_account_details(client, accounts, slug=slug)

    contributors = [
        _build(account, details.get(account.login, {}), geocoder) for account in accounts
    ]
    LOGGER.debug(
        "%s: %d contributors, %d commits between them",
        slug,
        len(contributors),
        sum(entry.contribution or 0 for entry in contributors),
    )
    return contributors


def _build(
    account: ContributorAccount,
    detail: dict[str, Any],
    geocoder: Geocoder | None,
) -> Contributor:
    """Assemble one contributor from its list entry and its detail payload."""
    raw_location = detail.get("location")
    location = str(raw_location) if raw_location else None

    address = Address()
    if geocoder is not None and location:
        address = geocoder.locate(location)

    company = detail.get("company")
    name = detail.get("name")

    return Contributor(
        github_id=account.github_id,
        # The login is the fallback because a record with no name at all
        # cannot be told from one belonging to an account that is gone.
        name=str(name) if name else account.login,
        organization=str(company) if company else "",
        location=location,
        internal_address=address,
        contribution=account.contribution,
        # Undefined until docs/METRICS.md settles them. None, not False:
        # False is an assertion about a named person that nothing has made.
        foreign=None,
        adversarial=None,
    )
