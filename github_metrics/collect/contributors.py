"""Collecting the contributor block of a per-repository document.

Two calls per repository, and the split between them is forced by the API
rather than chosen.

**The list comes from REST.** GraphQL has no connection that reports commits
attributed per account: `mentionableUsers` and `assignableUsers` list people
without counting anything, and `history` counts commits without grouping them
by author in one query. `GET /repos/{owner}/{repo}/contributors` is the only
route that answers the question, so the list is one REST request against a
budget the rest of a run barely touches.

**The details come from GraphQL, in aliased documents.** The REST
contributors payload is a minimal account object - login, id, avatar - and
carries no name, company or location. Reading those through PyGithub would
complete each account lazily, which is **one REST request per contributor**,
so any repository of consequence would exhaust REST's 5,000-per-hour budget on
its own. Aliasing the accounts into a GraphQL document asks for many at once,
and because every alias selects a single object rather than a connection, the
document has no `nodes` selection and stays at the cheap end of the cost
formula.

Since v0.5.0 collects every contributor rather than the first 25, that
document is issued in chunks of `DETAIL_CHUNK_SIZE`. The reason is the
**ten-second processing window** rather than cost: GitHub prices a query by
its connections, this one has none, so a chunk costs one point whether it
carries five aliases or fifty - but a query GitHub cannot finish in ten
seconds is terminated, and an unbounded alias count is a bet against that.

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
Some logins in the REST list do not resolve to a `User`:

- **A bot.** `dependabot[bot]`, `github-actions[bot]` and a repository's own
  automation appear in the contributors list like anyone else, but GraphQL
  models them as `Bot` rather than `User`, so `user(login:)` does not find
  them. These are common - a large repository is more likely to have one than
  not.
- **A deleted or suspended account**, between the REST list being read and the
  detail lookup being made.

GitHub reports both by returning `null` for that alias **and** adding a
`NOT_FOUND` entry to the response's `errors` array, with HTTP 200 and the other
forty-nine accounts resolved correctly beside it.

That second half was assumed away until a live run proved otherwise, and it
cost the whole scan: PyGithub maps a lone `NOT_FOUND` to
`UnknownObjectException`, `graphql.execute` read that as "the repository does
not exist", and the resulting `RepositoryNotFoundError` is not a
`ContributorCollectionError` - so it escaped the runner's per-repository
handling and aborted the entire run, producing no CSV at all. One bot in one
repository's contributor list was enough. Hence `tolerate_missing`, which says
that in this document, and only in this document, a `NOT_FOUND` is an answer
about one account rather than about the repository.

The contributor is still recorded, with the login as its name and nothing
resolved, because its `contribution` is a real measurement of this repository
and dropping the record would quietly reduce `contribution_total`.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from github.GithubException import GithubException

from github_metrics.client import GitHubClient
from github_metrics.collect.graphql import execute
from github_metrics.errors import CollectionError, ContributorCollectionError
from github_metrics.model.contributor import Address, Contributor

if TYPE_CHECKING:  # pragma: no cover
    from github.NamedUser import NamedUser

    from github_metrics.geo import Geocoder

LOGGER = logging.getLogger(__name__)

DEFAULT_CONTRIBUTOR_LIMIT: Final[int | None] = None
"""Contributors inspected per repository, ranked by commits. `None` is all.

This was 25 until v0.5.0, carried over from the `contributors` command v0.2.0
retired and never chosen for the current design. It decided what
`contribution_total` counted and therefore what every percentage derived from
it would mean, which made it a measurement decision wearing a tuning knob's
clothes - and the accounts it dropped were exactly the long tail the
downstream residency analysis needs. See
`docs/adr/0006-collect-every-contributor.md`.

The parameter survives so a library caller can still bound a run. Nothing in
the CLI sets it.
"""

BOT_ACCOUNT_TYPE: Final = "Bot"
"""What GitHub calls an App account in the contributors list.

Authoritative for GitHub Apps - `dependabot[bot]`, `github-actions[bot]` and a
repository's own automation all report it - and it is the same fact that makes
the GraphQL detail query unable to resolve them, since a `Bot` is not a `User`.

A bot running under an ordinary user account reports as `User`, and nothing
here guesses otherwise. A login that merely looks automated is recorded as the
account it is.
"""

DETAIL_CHUNK_SIZE: Final = 50
"""Accounts asked about in one aliased detail document.

Not a cost control - GitHub prices a query by its connections and this one has
none, so it costs a point whatever it carries. It is a **timeout** control:
GitHub terminates any query it has not processed in ten seconds, and several
hundred aliased account lookups in one document is not a safe bet against
that. Fifty keeps every document far inside the window regardless of how large
the repository is.
"""


@dataclass(frozen=True, slots=True)
class ContributorAccount:
    """One entry of the REST contributors list, before any detail is added.

    Attributes:
        login: The account's login, used to look its detail up.
        github_id: The account's numeric id, as a string.
        contribution: Commits attributed to the account in this repository.
        is_bot: Whether GitHub reported `type: "Bot"` for this entry.
    """

    login: str
    github_id: str
    contribution: int
    is_bot: bool = False


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
    limit: int | None = DEFAULT_CONTRIBUTOR_LIMIT,
) -> list[ContributorAccount]:
    """Fetch the ranked contributor list for one repository.

    Reads every page unless `limit` says otherwise. `anon` is deliberately not
    requested, which is GitHub's default: past the first 500 author email
    addresses GitHub stops linking commits to accounts and reports the rest as
    anonymous entries carrying no login, and an entry with no account is one
    this tool can neither look up nor attribute. `docs/METRICS.md` records that
    ceiling as a property of the source.

    Args:
        client: An authenticated client.
        owner: The owner as the inventory wrote it.
        repoid: The repository name as the inventory wrote it.
        limit: How many contributors to keep, ranked by commits descending.
            `None` keeps every one GitHub returns.

    Returns:
        The accounts, most commits first.

    Raises:
        ContributorCollectionError: The list could not be read.
    """
    slug = f"{owner}/{repoid}"
    try:
        repository = client.repository(slug)
        # PyGithub's paginated list is untyped, so what comes out of it is too.
        paginated = repository.get_contributors()
        accounts: list[NamedUser] = list(paginated if limit is None else paginated[:limit])
    except GithubException as exc:
        raise ContributorCollectionError(f"{slug}: could not read contributors: {exc}") from exc

    if limit is not None and len(accounts) == limit:
        # The aggregate that follows counts what was collected, not what
        # exists. Saying so at DEBUG is what makes a total reconcilable later.
        LOGGER.debug("%s: contributor list truncated at the limit of %d", slug, limit)
    else:
        LOGGER.debug("%s: %d contributors listed", slug, len(accounts))

    return [
        ContributorAccount(
            login=str(account.login),
            github_id=str(account.id),
            contribution=int(account.contributions),
            # GitHub's own classification, not a guess from the login. A
            # GitHub App is reported as `Bot` here and cannot be resolved as a
            # `User` by the detail query, which is why the two facts arrive
            # together.
            is_bot=str(account.type) == BOT_ACCOUNT_TYPE,
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
        Login to the account's detail payload. An account GitHub answered
        with `null` - a bot, or deleted or suspended since the list was
        read - is absent.

    Raises:
        ContributorCollectionError: A chunk could not be read. Raised as
            this type specifically so the runner degrades the repository to
            a row without a document rather than abandoning the run.
    """
    if not accounts:
        return {}

    details: dict[str, dict[str, Any]] = {}
    # Chunked for the ten-second processing window, not for cost. Aliases are
    # numbered within their chunk, so no document grows with the repository.
    for start in range(0, len(accounts), DETAIL_CHUNK_SIZE):
        chunk = accounts[start : start + DETAIL_CHUNK_SIZE]
        variables = {f"login{index}": account.login for index, account in enumerate(chunk)}
        try:
            data = execute(
                client,
                _details_query(len(chunk)),
                variables,
                description=f"contributor detail for {slug} ({start + 1}-{start + len(chunk)})",
                # A login that does not resolve to a `User` is an expected
                # answer here, not a failed query. This document names no
                # repository, so a NOT_FOUND in it cannot mean one.
                tolerate_missing=True,
            )
        except CollectionError as exc:
            # Translated rather than propagated. `execute` raises errors
            # that are not `ContributorCollectionError`, and the runner
            # only catches that one for the contributor half - so an
            # untranslated failure here escapes the per-repository
            # handling and takes the entire run down with it, producing no
            # CSV at all. The contract is a row and no document.
            raise ContributorCollectionError(
                f"{slug}: could not read contributor detail: {exc}"
            ) from exc
        for index, account in enumerate(chunk):
            payload = data.get(f"u{index}")
            if isinstance(payload, dict):
                details[account.login] = payload
            else:
                LOGGER.debug("%s: no detail for %s; the account may be gone", slug, account.login)

    LOGGER.debug(
        "%s: detail for %d of %d accounts in %d queries",
        slug,
        len(details),
        len(accounts),
        _chunk_count(len(accounts)),
    )
    return details


def _chunk_count(accounts: int) -> int:
    """How many detail queries `accounts` accounts need."""
    return -(-accounts // DETAIL_CHUNK_SIZE)


def get_contributors(
    client: GitHubClient,
    owner: str,
    repoid: str,
    *,
    geocoder: Geocoder | None = None,
    limit: int | None = DEFAULT_CONTRIBUTOR_LIMIT,
    extra: Sequence[ContributorAccount] = (),
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
            `None`, the default, keeps every one GitHub returns.
        extra: Accounts recovered from elsewhere - `collect.anonymous` rescues
            them from no-reply addresses - to collect alongside the listed
            ones. Their detail comes from the same aliased query, so they cost
            no extra request beyond the chunk they land in.

    Returns:
        The contributors, most commits first. The run identity is not stamped
        here; `analysis.row` applies it alongside the row's own.

    Raises:
        ContributorCollectionError: The contributor list could not be read.
    """
    slug = f"{owner}/{repoid}"
    listed = get_contributor_accounts(client, owner, repoid, limit=limit)
    # Recovered accounts join the ranking rather than being appended: they are
    # ordinary contributors that one endpoint declined to name, and a
    # concentration figure computed over an unsorted list would be wrong.
    accounts = sorted([*listed, *extra], key=lambda account: account.contribution, reverse=True)
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
        is_bot=account.is_bot,
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
