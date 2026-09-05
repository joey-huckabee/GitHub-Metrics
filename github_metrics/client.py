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

- **REST**: the `X-RateLimit-Remaining` header on every response, which
  PyGithub exposes as `Github.rate_limiting`. Verified decrementing one
  per request.
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
from typing import TYPE_CHECKING, Any

from github import Auth, Github

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
        return self._github.requester.graphql_query(query, variables)

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
        _, payload = self.graphql(GRAPHQL_BUDGET_QUERY, {})
        limit = payload.get("data", {}).get("rateLimit") or {}
        remaining = limit.get("remaining")
        if remaining is None:
            LOGGER.warning("GraphQL rate limit could not be read; treating it as spent")
            return 0
        return int(remaining)

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
        return self._github.requester.requestJsonAndCheck(
            "GET", f"{self._settings.api_url}/rate_limit"
        )

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
        return self._github.requester.requestJsonAndCheck(
            "GET",
            f"{self._settings.api_url}/repos/{slug}/contributors",
            parameters=parameters,
        )

    def rate_limit_remaining(self) -> int:
        """Return the number of core REST requests still available.

        **Read from response headers, not from `/rate_limit`.** Every REST
        response carries `X-RateLimit-Remaining`, and PyGithub keeps the
        most recent one; measured, it tracks spend exactly - 4986, 4985,
        4984 across three requests. The `/rate_limit` endpoint reported a
        flat 5000 throughout, for the same token, in the same minute.

        The header costs nothing: it arrives on responses the run was
        making anyway.

        Returns:
            Requests remaining as of the last response. Before any request
            has been made this is PyGithub's optimistic default rather than
            a measurement, which is why `check_budget` leans on the GraphQL
            budget - the binding one, and independently readable.
        """
        remaining, _ = self._github.rate_limiting
        return int(remaining)

    def close(self) -> None:
        """Release the underlying HTTP connection pool."""
        self._github.close()

    def __enter__(self) -> GitHubClient:
        """Enter the context manager."""
        return self

    def __exit__(self, *_: object) -> None:
        """Close the client on context exit."""
        self.close()
