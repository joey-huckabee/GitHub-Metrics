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

from github.GithubException import GithubException

from github_metrics.client import GitHubClient
from github_metrics.errors import (
    GitHubMetricsError,
    GraphQLQueryError,
    RepositoryNotFoundError,
)

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


def _only_not_found(data: Any) -> bool:
    """True if a response carries errors and every one of them is `NOT_FOUND`."""
    errors = _errors_in(data)
    return bool(errors) and all(error.get("type") == NOT_FOUND_TYPE for error in errors)


def _partial_data(payload: Any) -> dict[str, Any] | None:
    """Recover the `data` object from a response that also carried errors.

    GraphQL answers a partly-resolvable document with **both**: the fields it
    could resolve, and an error naming each one it could not. Returns `None`
    when there is no usable data object, which is a real failure rather than
    a partial success.
    """
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    return data if isinstance(data, dict) else None


def execute(
    client: GitHubClient,
    query: str,
    variables: dict[str, Any],
    *,
    description: str = "query",
    tolerate_missing: bool = False,
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
        tolerate_missing: Treat `NOT_FOUND` as an expected answer about one
            selection rather than as a failed query, and return the data the
            response did carry.

            Only correct for a document where a `NOT_FOUND` cannot mean the
            repository. The aliased contributor-detail document is the case
            it exists for: it selects nothing but `user(login:)`, and an
            alias that does not resolve to a `User` - a bot such as
            `dependabot[bot]`, or an account deleted since the REST list was
            read - is a fact about that account and says nothing about the
            other forty-nine in the same document.

    Returns:
        The `data` object from the response. With `tolerate_missing`, a
        selection that did not resolve is present and `null`.

    Raises:
        RepositoryNotFoundError: The query named a repository that does not
            exist, is private to this token, or was renamed.
        GraphQLQueryError: Any other error reported by the API, or a response
            carrying no `data` at all.
    """
    LOGGER.debug("GraphQL %s: variables=%r", description, variables)

    try:
        _, payload = client.graphql(query, variables)
    except GithubException as exc:
        # PyGithub maps a *lone* NOT_FOUND to UnknownObjectException and
        # collapses everything else to a generic 400, even when one of several
        # errors is NOT_FOUND. Both carry the original response, so the
        # classification is re-read from the payload rather than taken from the
        # exception type.
        recovered = _tolerated(exc.data, description, tolerate_missing=tolerate_missing)
        if recovered is not None:
            return recovered
        fallback = exc.message or str(exc)
        raise _classify(
            exc.data, description, tolerate_missing=tolerate_missing, fallback=fallback
        ) from exc

    # Second layer: a response that carried errors without the transport
    # raising. Not expected, but the cost of checking is one dictionary lookup
    # and the cost of not checking is reading a null repository as though it
    # were an answer.
    if _errors_in(payload):
        recovered = _tolerated(payload, description, tolerate_missing=tolerate_missing)
        if recovered is not None:
            return recovered
        raise _classify(payload, description, tolerate_missing=tolerate_missing)

    data = payload.get("data")
    if not isinstance(data, dict):
        raise GraphQLQueryError(f"{description}: response contained no data object")

    LOGGER.debug("GraphQL %s succeeded", description)
    return data


def _classify(
    payload: Any,
    description: str,
    *,
    tolerate_missing: bool,
    fallback: str = "",
) -> GitHubMetricsError:
    """Choose the error a failed response deserves.

    Split out of `execute` so the happy path reads in one piece; the branching
    here is genuinely about which failure this is, and it is the same decision
    whether the transport raised or answered.

    Args:
        payload: The response body, from an exception or a return value.
        description: Label for the message.
        tolerate_missing: Whether the caller said `NOT_FOUND` is expected here.
        fallback: Message to use when the payload carries none.

    Returns:
        `RepositoryNotFoundError` when a `NOT_FOUND` really can mean the
        repository, `GraphQLQueryError` otherwise. A document that opted into
        tolerance names no repository, so its `NOT_FOUND` never means one
        however it is mixed with other errors - classifying it that way would
        send an operator to fix an inventory that is correct.
    """
    message = _summarise(_errors_in(payload)) or fallback
    if _mentions_not_found(payload) and not tolerate_missing:
        LOGGER.debug("GraphQL %s: not found", description)
        return RepositoryNotFoundError(f"{description}: {message}")
    LOGGER.debug("GraphQL %s failed: %s", description, message)
    return GraphQLQueryError(f"{description} failed: {message}")


def _tolerated(
    payload: Any,
    description: str,
    *,
    tolerate_missing: bool,
) -> dict[str, Any] | None:
    """Return the usable data of a response whose only errors are `NOT_FOUND`.

    Args:
        payload: The response body, from a raised exception or a return value.
        description: Label for the log line.
        tolerate_missing: Whether the caller said `NOT_FOUND` is an expected
            answer for this document.

    Returns:
        The `data` object, or `None` when the response is not one this may
        tolerate - the caller did not opt in, some error was not `NOT_FOUND`,
        or there is no data object to salvage.
    """
    if not tolerate_missing or not _only_not_found(payload):
        return None
    data = _partial_data(payload)
    if data is None:
        return None
    missing = [str(error.get("path")) for error in _errors_in(payload)]
    LOGGER.debug(
        "GraphQL %s: %d selection(s) did not resolve and are null: %s",
        description,
        len(missing),
        ", ".join(missing),
    )
    return data
