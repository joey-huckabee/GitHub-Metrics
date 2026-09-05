"""Tests for the API client, and mostly for where it reads a budget from.

The obvious source for both budgets is the REST `/rate_limit` endpoint, and it
is the wrong one: measured against a live token it reported 5000 remaining for
both while the token had 4,988 GraphQL points and 4,984 REST requests left. It
does not track spend, and it fails in the worst direction — a pre-flight
reading it accepts a run whose budget is already gone.

So the client reads GraphQL's budget from GraphQL and REST's from the response
headers, and these tests pin that. They are cheap tests for an expensive
mistake: nothing in the output would look wrong if this regressed, the
pre-flight would simply stop protecting anything.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from github_metrics.client import GRAPHQL_BUDGET_QUERY, PER_PAGE, GitHubClient
from github_metrics.config import Settings

CLIENT_LOGGER = "github_metrics.client"


class _Requester:
    """Stands in for PyGithub's requester."""

    def __init__(
        self,
        graphql_payload: Any = None,
        rest: tuple[dict[str, Any], Any] | None = None,
    ) -> None:
        self.graphql_payload = graphql_payload
        self.rest: tuple[dict[str, Any], Any] = rest if rest is not None else ({}, [])
        self.queries: list[tuple[str, dict[str, Any]]] = []
        self.requests: list[tuple[str, str, dict[str, Any]]] = []

    def graphql_query(self, query: str, variables: dict[str, Any]) -> tuple[dict[str, Any], Any]:
        """Record and answer a GraphQL query."""
        self.queries.append((query, variables))
        return {}, self.graphql_payload

    # pylint: disable=invalid-name  # PyGithub's spelling; the stub must match it.
    def requestJsonAndCheck(  # noqa: N802
        self, verb: str, url: str, parameters: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any], Any]:
        """Record and answer a REST request."""
        self.requests.append((verb, url, parameters or {}))
        return self.rest


class _Github:
    """Stands in for PyGithub's `Github`."""

    def __init__(self, requester: _Requester, rate_limiting: tuple[int, int]) -> None:
        self.requester = requester
        self.rate_limiting = rate_limiting


def client_with(
    monkeypatch: pytest.MonkeyPatch,
    graphql_payload: Any = None,
    rest: tuple[dict[str, Any], Any] | None = None,
    rate_limiting: tuple[int, int] = (4984, 5000),
) -> tuple[GitHubClient, _Requester]:
    """Build a client whose transport is a stub."""
    requester = _Requester(graphql_payload, rest)
    monkeypatch.setattr(
        "github_metrics.client.Github",
        lambda **_kwargs: _Github(requester, rate_limiting),
    )
    return GitHubClient(Settings(github_token="ghp_x")), requester


# ---------------------------------------------------------------------------
# The GraphQL budget, read from GraphQL
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-STA-008")
def test_the_graphql_budget_is_read_from_graphql(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not from `/rate_limit`, which reports a number that does not move."""
    client, requester = client_with(
        monkeypatch, graphql_payload={"data": {"rateLimit": {"remaining": 4988}}}
    )

    assert client.graphql_points_remaining() == 4988
    assert requester.queries[0][0] == GRAPHQL_BUDGET_QUERY
    # No REST call: asking the right service is also the free one.
    assert not requester.requests


@pytest.mark.requirement("L3-STA-008")
def test_an_unreadable_graphql_budget_reads_as_spent(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Zero is the safe failure: it refuses a run rather than letting one start
    on a number nothing confirmed."""
    client, _ = client_with(monkeypatch, graphql_payload={"data": {"rateLimit": None}})

    with caplog.at_level(logging.WARNING, logger=CLIENT_LOGGER):
        found = client.graphql_points_remaining()

    assert found == 0
    assert "could not be read" in caplog.text


@pytest.mark.requirement("L3-STA-008")
def test_a_response_with_no_data_at_all_reads_as_spent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = client_with(monkeypatch, graphql_payload={})

    assert client.graphql_points_remaining() == 0


# ---------------------------------------------------------------------------
# The REST budget, read from the response header
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-STA-008")
def test_the_rest_budget_comes_from_the_response_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`X-RateLimit-Remaining` tracks spend exactly; `/rate_limit` does not."""
    client, requester = client_with(monkeypatch, rate_limiting=(4984, 5000))

    assert client.rate_limit_remaining() == 4984
    # Free: the header arrives on responses the run was making anyway.
    assert not requester.requests


# ---------------------------------------------------------------------------
# Paginated contributor requests
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-STA-008")
def test_a_contributors_page_can_ask_for_anonymous_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The census needs `anon=1` and `per_page=1`; PyGithub's paginated list
    hides the `Link` header that makes the count one request."""
    client, requester = client_with(monkeypatch, rest=({"link": "x"}, [{}]))

    headers, payload = client.contributors_page(
        "pypa/virtualenv", page=1, per_page=1, anonymous=True
    )

    verb, url, parameters = requester.requests[0]
    assert verb == "GET"
    assert url.endswith("/repos/pypa/virtualenv/contributors")
    assert parameters == {"page": 1, "per_page": 1, "anon": "1"}
    assert headers == {"link": "x"}
    assert payload == [{}]


@pytest.mark.requirement("L3-STA-008")
def test_anonymous_contributors_are_not_requested_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An anonymous entry has no login, no id and no location, so the
    collection path deliberately never asks for them."""
    client, requester = client_with(monkeypatch)

    client.contributors_page("pypa/virtualenv")

    assert "anon" not in requester.requests[0][2]


@pytest.mark.requirement("L3-STA-008")
def test_pages_are_requested_at_the_endpoint_maximum_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, requester = client_with(monkeypatch)

    client.contributors_page("pypa/virtualenv")

    assert requester.requests[0][2]["per_page"] == PER_PAGE
