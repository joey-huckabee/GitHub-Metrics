"""Tests for what happens when no answer arrives at all.

PyGithub speaks HTTP through `requests` and does not wrap what it raises, so a
dropped connection, a DNS failure or a read timeout surfaces as a
`requests.RequestException` - which is **not** a `GithubException`. Every
`except GithubException` in the package therefore let it straight through, past
the per-repository handling, out of the worker and out of the run: one
interruption produced a traceback, exit 1 and no CSV at all, losing the
repositories already collected along with the rest.

Retries did not cover it. PyGithub's default `GithubRetry` is `total=10` with
`backoff_factor=0`, so ten attempts happen essentially at once: they absorb a
single dropped packet and nothing longer.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, cast

import pytest
import requests

from github_metrics.client import GitHubClient
from github_metrics.collect.anonymous import collect_anonymous
from github_metrics.collect.census import count_identities
from github_metrics.collect.contributors import get_contributor_accounts
from github_metrics.collect.credentials import verify_credentials
from github_metrics.collect.graphql import execute
from github_metrics.config import Settings
from github_metrics.errors import ContributorCollectionError, TransportError

PACKAGE = Path(__file__).resolve().parent.parent / "github_metrics"

DROPPED = requests.exceptions.ConnectionError(
    "HTTPSConnectionPool(host='api.github.com', port=443): Max retries exceeded"
)


class _Unreachable:
    """A client for which every call finds the network gone."""

    @staticmethod
    def graphql(query: str, variables: dict[str, Any]) -> tuple[Any, Any]:
        """Fail the way `requests` fails once its retries are spent."""
        del query, variables
        raise DROPPED

    @staticmethod
    def contributors_page(slug: str, **kwargs: Any) -> tuple[Any, Any]:
        """As above, for the REST half."""
        del slug, kwargs
        raise DROPPED

    @staticmethod
    def repository(full_name: str) -> Any:
        """As above, for the repository lookup the contributor list needs."""
        del full_name
        raise DROPPED

    @staticmethod
    def rate_limit_snapshot() -> tuple[Any, Any]:
        """As above, for the request that verifies a token."""
        raise DROPPED

    @staticmethod
    def close() -> None:
        """Closing an unreachable client does nothing."""


def client() -> GitHubClient:
    """The unreachable client, typed as the real one."""
    return cast(GitHubClient, _Unreachable())


@pytest.mark.requirement("L3-COL-004")
def test_a_dropped_connection_is_a_transport_error_not_a_query_failure() -> None:
    """Nothing was refused, so there is nothing to classify.

    A query failure says the API answered and said no; this says the API was
    never reached. The operator's next step is the network, not the inventory.
    """
    with pytest.raises(TransportError) as caught:
        execute(client(), "query { x }", {}, description="repository metadata")

    assert "repository metadata" in str(caught.value)
    assert "GM-COL-006" in str(caught.value)


@pytest.mark.requirement("L3-COL-004")
def test_a_dropped_connection_reading_contributors_degrades_the_repository() -> None:
    """The REST half translates it, like every other failure it has."""
    with pytest.raises(ContributorCollectionError):
        get_contributor_accounts(client(), "pypa", "virtualenv")


@pytest.mark.requirement("L3-COL-004")
def test_a_dropped_connection_counting_identities_degrades_the_repository() -> None:
    with pytest.raises(ContributorCollectionError):
        count_identities(client(), "pypa", "virtualenv")


@pytest.mark.requirement("L3-COL-004")
def test_a_dropped_connection_reading_anonymous_contributors_degrades_it_too() -> None:
    with pytest.raises(ContributorCollectionError):
        collect_anonymous(client(), "pypa", "virtualenv")


@pytest.mark.requirement("L3-COL-004")
def test_an_unreachable_api_is_not_reported_as_a_rejected_token() -> None:
    """Nothing was reached to reject it, so saying so sends the operator to
    rotate a token that is fine."""
    with pytest.raises(TransportError) as caught:
        verify_credentials(Settings(github_token="ghp_x"), client=client())

    assert "could not reach" in str(caught.value)


@pytest.mark.requirement("L3-COL-004")
def test_every_github_exception_handler_also_handles_the_transport() -> None:
    """The general form, and the check that would have caught this.

    Six `try` blocks caught `GithubException` and none of them caught what
    `requests` raises. They are not related by inheritance, they are not near
    each other in the source, and nothing but this connects them.
    """

    def names(node: ast.expr | None) -> set[str]:
        found: set[str] = set()
        for child in ast.walk(node) if node is not None else ():
            if isinstance(child, ast.Name):
                found.add(child.id)
            elif isinstance(child, ast.Attribute):
                found.add(child.attr)
        return found

    unguarded: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            caught = [names(handler.type) for handler in node.handlers]
            if not any("GithubException" in group for group in caught):
                continue
            if not any("TRANSPORT_ERRORS" in group for group in caught):
                unguarded.append(f"{path.name}:{node.lineno}")

    assert not unguarded
