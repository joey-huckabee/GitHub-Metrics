"""Executing GraphQL queries against the GitHub API.

Why GraphQL rather than REST, for counts specifically
-----------------------------------------------------
The REST API cannot answer "how many closed issues does this repository have".

- The repository object exposes only `open_issues_count`, and that number
  **includes pull requests**. For `cline/cline` it reads 1148, which is 691
  open issues plus 457 open pull requests. There is no closed equivalent.
- The issues endpoint returns pull requests alongside issues and offers no way
  to exclude them server-side.
- Counting by pagination no longer works either. GitHub moved the issues
  endpoint to cursor pagination, so responses carry `rel="next"` but no
  `rel="last"`. Any library that derives a total from the last-page link -
  PyGithub's `PaginatedList.totalCount` among them - now returns **1** for
  every repository, silently.

GraphQL returns an exact `totalCount`, excludes pull requests from `issues` by
construction, and costs **one point** of a 5000-point hourly budget for the
whole repository. The equivalent REST route needs three requests and still
gets the number wrong.

Rate limiting
-------------
GraphQL has its own 5000-point-per-hour budget, separate from REST's 5000
requests per hour. A repository query costs 1 point, so the two budgets are
comparable in practice while the GraphQL one buys far more per unit.
"""

from __future__ import annotations

import logging
from typing import Any

from github_metrics.client import GitHubClient
from github_metrics.errors import GraphQLQueryError, RepositoryNotFoundError

LOGGER = logging.getLogger(__name__)

NOT_FOUND_TYPE = "NOT_FOUND"
"""The `type` GitHub sets on a GraphQL error for a repository that is absent."""


def execute(
    client: GitHubClient,
    query: str,
    variables: dict[str, Any],
    *,
    description: str = "query",
) -> dict[str, Any]:
    """Run one GraphQL query and return its `data` payload.

    GraphQL reports failures with HTTP 200 and an `errors` array rather than an
    error status, so a caller checking only the status code sees success. This
    function inspects the array and raises, which is what turns a silent
    partial answer into a diagnosable failure.

    Args:
        client: An authenticated client.
        query: The GraphQL document.
        variables: Values for the document's declared variables.
        description: Short label used in log messages, so a failure names the
            query that produced it rather than only the file.

    Returns:
        The `data` object from the response.

    Raises:
        RepositoryNotFoundError: The query named a repository that does not
            exist, is private to this token, or was renamed.
        GraphQLQueryError: Any other error reported by the API, or a response
            carrying no `data` at all.
    """
    LOGGER.debug("GraphQL %s: variables=%r", description, variables)

    _, payload = client.graphql(query, variables)

    errors = payload.get("errors")
    if errors:
        messages = "; ".join(str(error.get("message", error)) for error in errors)
        types = {error.get("type") for error in errors}
        LOGGER.debug("GraphQL %s returned %d error(s): %s", description, len(errors), messages)

        if NOT_FOUND_TYPE in types:
            raise RepositoryNotFoundError(f"{description}: {messages}")
        raise GraphQLQueryError(f"{description} failed: {messages}")

    data = payload.get("data")
    if not isinstance(data, dict):
        raise GraphQLQueryError(f"{description}: response contained no data object")

    LOGGER.debug("GraphQL %s succeeded", description)
    return data
