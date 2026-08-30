"""Thin wrapper around PyGithub that centralises auth and rate-limit handling."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from github import Auth, Github

from github_metrics.config import Settings

if TYPE_CHECKING:  # pragma: no cover - import cycle only needed for typing
    from github.Repository import Repository

LOGGER = logging.getLogger(__name__)


class GitHubClient:
    """Authenticated GitHub API client."""

    def __init__(self, settings: Settings) -> None:
        """Create a client from resolved settings."""
        self._settings = settings
        self._github = Github(auth=Auth.Token(settings.github_token), base_url=settings.api_url)

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
        """
        return int(self._github.get_rate_limit().resources.graphql.remaining)

    def rate_limit_remaining(self) -> int:
        """Return the number of core API requests still available."""
        return int(self._github.get_rate_limit().resources.core.remaining)

    def close(self) -> None:
        """Release the underlying HTTP connection pool."""
        self._github.close()

    def __enter__(self) -> GitHubClient:
        """Enter the context manager."""
        return self

    def __exit__(self, *_: object) -> None:
        """Close the client on context exit."""
        self.close()
