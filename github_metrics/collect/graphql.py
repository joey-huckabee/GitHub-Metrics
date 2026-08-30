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

How failures arrive
-------------------
GraphQL reports failure with HTTP 200 and an `errors` array rather than an
error status. PyGithub inspects that array itself and raises rather than
returning it, so **failures reach this module as exceptions, not as payloads**:

- exactly one error typed `NOT_FOUND` becomes `UnknownObjectException`;
- anything else - including a response whose *several* errors include a
  `NOT_FOUND` - is collapsed into a generic 400 exception.

Both carry the original response on `.data`, so the classification survives the
collapse: this module re-reads it rather than trusting the status PyGithub
chose. The payload path below is kept as a second layer for the case where a
response carries errors without raising at all.

Rate limiting
-------------
GraphQL has its own 5000-point-per-hour budget, separate from REST's 5000
requests per hour. A repository query costs 1 point, so the two budgets are
comparable in practice while the GraphQL one buys far more per unit.
"""

from __future__ import annotations

import logging
from typing import Any

from github.GithubException import GithubException, UnknownObjectException

from github_metrics.client import GitHubClient
from github_metrics.errors import GraphQLQueryError, RepositoryNotFoundError

LOGGER = logging.getLogger(__name__)

NOT_FOUND_TYPE = "NOT_FOUND"
"""The `type` GitHub sets on a GraphQL error for a repository that is absent."""


def _errors_in(data: Any) -> list[dict[str, Any]]:
    """Return the GraphQL `errors` array from a response, if it has one."""
    if not isinstance(data, dict):
        return []
    errors = data.get("errors")
    if not isinstance(errors, list):
        return []
    return [error for error in errors if isinstance(error, dict)]


def _summarise(errors: list[dict[str, Any]]) -> str:
    """Join the API's own error messages into one line."""
    return "; ".join(str(error.get("message", error)) for error in errors)


def _mentions_not_found(data: Any) -> bool:
    """True if any error in a response is typed `NOT_FOUND`."""
    return any(error.get("type") == NOT_FOUND_TYPE for error in _errors_in(data))


def execute(
    client: GitHubClient,
    query: str,
    variables: dict[str, Any],
    *,
    description: str = "query",
) -> dict[str, Any]:
    """Run one GraphQL query and return its `data` payload.

    Translates every failure into this package's own taxonomy, so a caller
    never has to know that the transport is PyGithub or that GraphQL signals
    failure in the response body.

    Args:
        client: An authenticated client.
        query: The GraphQL document.
        variables: Values for the document's declared variables.
        description: Short label used in messages, so a failure names the query
            that produced it rather than only the file.

    Returns:
        The `data` object from the response.

    Raises:
        RepositoryNotFoundError: The query named a repository that does not
            exist, is private to this token, or was renamed.
        GraphQLQueryError: Any other error reported by the API, or a response
            carrying no `data` at all.
    """
    LOGGER.debug("GraphQL %s: variables=%r", description, variables)

    try:
        _, payload = client.graphql(query, variables)
    except UnknownObjectException as exc:
        LOGGER.debug("GraphQL %s: repository not found", description)
        raise RepositoryNotFoundError(f"{description}: {exc.message or exc}") from exc
    except GithubException as exc:
        # PyGithub only maps a *lone* NOT_FOUND to UnknownObjectException; a
        # response carrying several errors is collapsed to a generic 400 even
        # when one of them is NOT_FOUND. Re-reading the payload keeps the
        # classification the caller depends on.
        message = _summarise(_errors_in(exc.data)) or (exc.message or str(exc))
        if _mentions_not_found(exc.data):
            LOGGER.debug("GraphQL %s: not found, reported among several errors", description)
            raise RepositoryNotFoundError(f"{description}: {message}") from exc
        raise GraphQLQueryError(f"{description} failed: {message}") from exc

    # Second layer: a response that carried errors without the transport
    # raising. Not expected, but the cost of checking is one dictionary lookup
    # and the cost of not checking is reading a null repository as though it
    # were an answer.
    errors = _errors_in(payload)
    if errors:
        message = _summarise(errors)
        LOGGER.debug("GraphQL %s returned %d error(s): %s", description, len(errors), message)
        if _mentions_not_found(payload):
            raise RepositoryNotFoundError(f"{description}: {message}")
        raise GraphQLQueryError(f"{description} failed: {message}")

    data = payload.get("data")
    if not isinstance(data, dict):
        raise GraphQLQueryError(f"{description}: response contained no data object")

    LOGGER.debug("GraphQL %s succeeded", description)
    return data
