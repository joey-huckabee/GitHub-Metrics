"""Thin wrapper around PyGithub that centralises auth and rate-limit handling.

Where a budget is read from, and why it is not `/rate_limit`
------------------------------------------------------------
The obvious source is the REST `/rate_limit` endpoint, and it is the wrong
one. Measured on 2026-09-05 against a live token, it reported **5000
remaining for both budgets** while the same token had 4988 GraphQL points
and 4984 REST requests left. It does not appear to track spend at all
here, and it fails in the worst direction: a pre-flight reading it accepts
a run whose budget is already gone, which is precisely the failure
`collect.budget` exists to prevent.

Two sources are trustworthy, and both are free:

- **REST**: the `X-RateLimit-Remaining` header on every REST response.
  Verified decrementing one per request. Recorded here rather than read
  from PyGithub's `Github.rate_limiting`, which is **one number for both
  budgets**, set from whatever response came back last: GitHub reports the
  GraphQL points budget under the same header names, so a single GraphQL
  call replaces the REST figure with a number about the other budget.
  `x-ratelimit-resource` is what tells them apart. Until v0.6.6 the
  pre-flight read the contaminated value and was therefore checking the
  GraphQL figure twice.
- **GraphQL**: the `rateLimit` field inside a GraphQL document. A query
  selecting nothing else is not charged - confirmed by issuing it twice
  and reading the same `remaining` - so asking costs nothing of what is
  being asked about.

`rate_limit_snapshot` still calls `/rate_limit`, and that is correct: it is
used to *verify credentials*, where the endpoint's value is irrelevant and
only its status code and scope headers matter.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

import requests
from github import Auth, Github
from github.GithubException import GithubException

from github_metrics.config import Settings

if TYPE_CHECKING:  # pragma: no cover - import cycle only needed for typing
    from github.Repository import Repository

LOGGER = logging.getLogger(__name__)

GRAPHQL_BUDGET_QUERY = "query { rateLimit { remaining resetAt } }"
"""The only reliable source for the GraphQL budget, and it is free.

REST's `/rate_limit` carries a `resources.graphql` section that does not
track GraphQL spend - measured at 5000 while GraphQL itself reported 4988
for the same token at the same moment. A document selecting nothing but
`rateLimit` is not charged, so asking the right service costs nothing.
"""

TRANSPORT_ERRORS: Final = (requests.RequestException,)
"""What is raised when no answer arrived at all.

PyGithub speaks HTTP through `requests` and does not wrap what it raises, so a
dropped connection, a DNS failure or a read timeout surfaces as a
`requests.RequestException` - which is **not** a `GithubException`. Every
`except GithubException` in this package therefore used to let it straight
through, past the per-repository handling, out of the worker and out of the
run: one interruption, a traceback, exit 1, and no CSV at all.

Defined here because this module owns the transport. `collect/` imports the
tuple rather than `requests`, so there is one place that knows what the
transport is and one place to change if it ever changes.

Retries do not make this unnecessary. PyGithub's default `GithubRetry` sets
`total=10` with `backoff_factor=0`, so ten attempts happen essentially at once:
they absorb a single dropped packet and nothing longer. Any real interruption -
a VPN flap, a sleeping laptop, a DNS blip - outlasts them and raises.
"""

PER_PAGE = 100
"""Results per REST page - the maximum every paginated endpoint here accepts.

PyGithub defaults to 30. That was invisible while the contributor list stopped
at 25, because 25 fitted in one page and one page was one request; it is the
difference between 5 requests and 17 for a 500-contributor repository now that
the list is read in full. The REST budget is spent almost entirely on that one
endpoint, so this is the cheapest lever there is.
"""


class GitHubClient:
    """Authenticated GitHub API client."""

    def __init__(self, settings: Settings) -> None:
        """Create a client from resolved settings."""
        self._settings = settings
        self._github = Github(
            auth=Auth.Token(settings.github_token),
            base_url=settings.api_url,
            per_page=PER_PAGE,
        )
        # Shared across the worker pool, so the last reading needs a lock.
        self._observed_lock = threading.Lock()
        self._observed: tuple[int, datetime | None] | None = None
        self._observed_rest: int | None = None

    def repository(self, full_name: str) -> Repository:
        """Fetch a repository by its `owner/name` identifier.

        Args:
            full_name: The `owner/name` slug, e.g. `python/cpython`.

        Returns:
            The PyGithub repository object.
        """
        LOGGER.debug("Fetching repository %s", full_name)
        return self._github.get_repo(full_name)

    def graphql(
        self, query: str, variables: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Run a GraphQL query against the same authenticated session.

        Counts that REST cannot answer correctly - closed issues in
        particular - come from GraphQL. Reusing PyGithub's requester keeps one
        set of credentials and one connection pool, and avoids adding an HTTP
        client dependency for the sake of one endpoint.

        Args:
            query: The GraphQL document.
            variables: Values for the document's declared variables.

        Returns:
            The `(headers, payload)` pair the requester produces. The payload
            carries `data` and, on failure, `errors` - GraphQL answers with
            HTTP 200 either way, so the caller must inspect it.
        """
        LOGGER.debug("GraphQL request with variables %r", variables)
        try:
            headers, payload = self._github.requester.graphql_query(query, variables)
        except (GithubException, *TRANSPORT_ERRORS) as exc:
            # A failed response still reports the budget, and a failure is
            # exactly when the budget is worth knowing. A transport failure
            # carries no response at all, so there is nothing to read and
            # `_observe` ignores it.
            self._observe(getattr(exc, "data", None))
            raise
        self._observe(payload)
        return headers, payload

    def _observe(self, payload: Any) -> None:
        """Record a `rateLimit` reading that arrived with a response.

        Every collection document selects `rateLimit`, which costs nothing -
        the price of a query counts connections, and this adds none - so the
        true remaining budget arrives with the answer rather than needing a
        round trip of its own. This is the only place it is recorded, because
        this is the one method every GraphQL query in the package goes through.

        Args:
            payload: A response body, or anything else; a shape that carries
                no reading is ignored rather than guessed at.
        """
        if not isinstance(payload, dict):
            return
        data = payload.get("data")
        limit = data.get("rateLimit") if isinstance(data, dict) else None
        if not isinstance(limit, dict) or limit.get("remaining") is None:
            return
        with self._observed_lock:
            self._observed = (int(limit["remaining"]), _reset_at(limit.get("resetAt")))

    def observed_budget(self) -> tuple[int, datetime | None] | None:
        """Return the most recent budget reading, without asking the API.

        Returns:
            Points remaining and the reset instant as of the last response
            seen, or `None` if no response has carried a reading yet. The
            value is a *lower* bound on nothing: queries in flight may have
            spent since, which is why the guard keeps its own reservation on
            top rather than trusting this alone.
        """
        with self._observed_lock:
            return self._observed

    def graphql_points_remaining(self) -> int:
        """Return the GraphQL points still available this hour.

        GraphQL has its own 5000-point budget, separate from REST's 5000
        requests. Checking the wrong one is an easy way to believe a run has
        headroom it does not have.

        **Read from GraphQL, not from REST.** The REST `/rate_limit`
        endpoint's `resources.graphql` section does not track GraphQL spend:
        measured, it reported 5000 remaining while the GraphQL API itself
        reported 4988 for the same token at the same moment. A pre-flight
        reading the REST figure would accept a run whose budget was already
        gone, which is the exact failure `check_budget` exists to prevent.

        The query is free. GitHub does not charge for a document selecting
        nothing but `rateLimit` - confirmed by issuing it twice and reading
        the same `remaining` both times - so the check costs nothing of what
        it is checking.

        Returns:
            Points remaining, or `0` if the field could not be read. Zero is
            the safe failure: it refuses a run rather than letting one start
            on a number nothing confirmed.
        """
        remaining, _ = self.graphql_budget()
        return remaining

    def graphql_budget(self) -> tuple[int, datetime | None]:
        """Return the GraphQL points remaining and when the hour resets.

        The reset time is what `--on-exhaustion wait` sleeps until, and it
        comes from the same free query as the remaining count, so knowing when
        to wake costs nothing extra.

        Returns:
            Points remaining and the reset instant. Remaining is `0` if the
            field could not be read - the safe failure, since it refuses a run
            rather than letting one start on a number nothing confirmed - and
            the reset is `None` when GitHub did not give one.
        """
        _, payload = self.graphql(GRAPHQL_BUDGET_QUERY, {})
        limit = payload.get("data", {}).get("rateLimit") or {}
        remaining = limit.get("remaining")
        if remaining is None:
            LOGGER.warning("GraphQL rate limit could not be read; treating it as spent")
            return 0, None
        return int(remaining), _reset_at(limit.get("resetAt"))

    def rate_limit_snapshot(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """Fetch the rate-limit endpoint, headers included.

        This is the cheapest way to confirm a token works: the endpoint does
        not count against the rate limit, and it answers 401 for a token that
        is not valid. The headers carry the OAuth scopes, so one call reports
        both validity and permissions.

        Returns:
            The `(headers, payload)` pair, unparsed, so a caller can read the
            scope headers as well as the budgets.
        """
        LOGGER.debug("Requesting the rate-limit snapshot")
        headers, data = self._github.requester.requestJsonAndCheck(
            "GET", f"{self._settings.api_url}/rate_limit"
        )
        # The body of this endpoint is not trusted - measured at 5000 while the
        # token had 4984 - but its *headers* are the same accurate ones every
        # REST response carries, and this request is not itself counted.
        self._observe_rest(headers)
        return headers, data

    def contributors_page(
        self,
        slug: str,
        *,
        page: int = 1,
        per_page: int = 100,
        anonymous: bool = False,
    ) -> tuple[dict[str, Any], Any]:
        """Fetch one page of the contributors list, headers included.

        PyGithub's `PaginatedList` hides the `Link` header, and that header is
        the whole point here: with `per_page=1` its `rel="last"` page number is
        the total identity count, which turns a 34-request census into a
        one-request one.

        Args:
            slug: The `owner/name` identifier.
            page: Which page to fetch, 1-based.
            per_page: Entries per page, at most 100.
            anonymous: Include contributors GitHub could not link to an
                account. These carry a name and an email and nothing else.

        Returns:
            The `(headers, payload)` pair, so the caller can read pagination
            as well as content.
        """
        parameters: dict[str, Any] = {"page": page, "per_page": per_page}
        if anonymous:
            parameters["anon"] = "1"
        LOGGER.debug("Requesting contributors page %d for %s (anon=%s)", page, slug, anonymous)
        headers, payload = self._github.requester.requestJsonAndCheck(
            "GET",
            f"{self._settings.api_url}/repos/{slug}/contributors",
            parameters=parameters,
        )
        self._observe_rest(headers)
        return headers, payload

    def _observe_rest(self, headers: Any) -> None:
        """Record the core REST budget a REST response reported.

        Kept here rather than read from PyGithub because PyGithub keeps **one**
        `rate_limiting` for both budgets, set from whatever response came back
        last. GitHub reports the GraphQL *points* budget in the same header
        names REST uses for its *requests* budget, so a single GraphQL call
        replaces the REST figure with a number about the other budget entirely.

        `x-ratelimit-resource` is what tells them apart, and it is why this
        checks it: a reading is taken only from a response that says it is
        about `core`.

        Args:
            headers: A REST response's headers, or anything else; a shape that
                carries no core reading is ignored rather than guessed at.
        """
        if not isinstance(headers, dict):
            return
        lowered = {str(key).lower(): value for key, value in headers.items()}
        resource = lowered.get("x-ratelimit-resource")
        if resource is not None and str(resource) != "core":
            return
        remaining = lowered.get("x-ratelimit-remaining")
        if remaining is None:
            return
        try:
            self._observed_rest = int(float(remaining))
        except (TypeError, ValueError):
            LOGGER.warning("REST rate limit header %r could not be read", remaining)

    def rate_limit_remaining(self) -> int:
        """Return the number of core REST requests still available.

        **Read from REST response headers, and only from REST ones.** Every
        REST response carries `X-RateLimit-Remaining`, and measured it tracks
        spend exactly - 4986, 4985, 4984 across three requests - while the
        `/rate_limit` body reported a flat 5000 throughout for the same token
        in the same minute. The header costs nothing: it arrives on responses
        the run was making anyway.

        It is *not* read from PyGithub's `rate_limiting`, which is one number
        for both budgets, set from whatever response came back last. GitHub
        reports the GraphQL points budget under the same header names, so a
        single GraphQL call replaces the REST figure with a number about the
        other budget. That is what made this return the GraphQL figure to the
        pre-flight until v0.6.6, since `check_budget` reads the GraphQL budget
        first; `_observe_rest` keeps the two apart by checking
        `x-ratelimit-resource`.

        Returns:
            Requests remaining as of the last REST response. When none has
            been seen yet, the rate-limit snapshot is fetched to get one - it
            is free and is not itself counted.
        """
        if self._observed_rest is None:
            self.rate_limit_snapshot()
        if self._observed_rest is None:
            # No response has reported the budget. Zero is the safe failure,
            # as it is for the GraphQL budget: it refuses a run rather than
            # letting one start on a number nothing confirmed.
            LOGGER.warning("REST rate limit could not be read; treating it as spent")
            return 0
        return self._observed_rest

    def close(self) -> None:
        """Release the underlying HTTP connection pool."""
        self._github.close()

    def __enter__(self) -> GitHubClient:
        """Enter the context manager."""
        return self

    def __exit__(self, *_: object) -> None:
        """Close the client on context exit."""
        self.close()


def _reset_at(value: Any) -> datetime | None:
    """Parse GitHub's `resetAt`, which is ISO-8601 with a `Z` suffix.

    Args:
        value: The field as GitHub returned it.

    Returns:
        The instant, or `None` when it is absent or unparseable. `None` rather
        than a guess: a caller waiting for a reset needs to fall back to a
        bounded sleep rather than to a time nothing supplied.
    """
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        LOGGER.warning("GraphQL rate limit reset time %r could not be read", value)
        return None
