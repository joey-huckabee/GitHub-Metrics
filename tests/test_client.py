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
from github.GithubException import GithubException

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
        self.rate_limiting = (4984, 5000)
        self.graphql_remaining = 4990

    def graphql_query(self, query: str, variables: dict[str, Any]) -> tuple[dict[str, Any], Any]:
        """Record and answer a GraphQL query.

        A GraphQL response carries `x-ratelimit-*` too, reporting the *points*
        budget under the header names REST uses for its *requests* budget - and
        PyGithub writes both into one `rate_limiting`. The stub does the same,
        because that overwrite is the thing under test.
        """
        self.queries.append((query, variables))
        self.rate_limiting = (self.graphql_remaining, 5000)
        return {
            "x-ratelimit-remaining": str(self.graphql_remaining),
            "x-ratelimit-resource": "graphql",
        }, self.graphql_payload

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
        requester.rate_limiting = rate_limiting

    @property
    def rate_limiting(self) -> tuple[int, int]:
        """One number for both budgets, as PyGithub really keeps it."""
        return self.requester.rate_limiting


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


CORE_HEADERS = {"x-ratelimit-remaining": "4984", "x-ratelimit-resource": "core"}


@pytest.mark.requirement("L3-STA-008")
def test_the_rest_budget_comes_from_the_response_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`X-RateLimit-Remaining` tracks spend exactly; `/rate_limit`'s body does not."""
    client, requester = client_with(monkeypatch, rest=(CORE_HEADERS, []))
    client.contributors_page("pypa/virtualenv")
    before = len(requester.requests)

    assert client.rate_limit_remaining() == 4984
    # Free: the header arrived on a response the run was making anyway.
    assert len(requester.requests) == before


@pytest.mark.requirement("L3-STA-010")
def test_a_graphql_call_does_not_move_the_rest_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The defect this separation exists for.

    PyGithub keeps **one** `rate_limiting` for both budgets, set from whatever
    response came back last, and GitHub reports the GraphQL *points* budget
    under the same header names REST uses for its *requests* budget. So one
    GraphQL call replaced the REST figure with a number about the other
    budget - and `check_budget` reads the GraphQL budget first, so the REST
    half of the pre-flight was comparing the GraphQL figure against a lower
    threshold and could never fail.
    """
    client, requester = client_with(
        monkeypatch,
        graphql_payload={"data": {"rateLimit": {"remaining": 4990}}},
        rest=(CORE_HEADERS, []),
    )
    client.contributors_page("pypa/virtualenv")
    assert client.rate_limit_remaining() == 4984

    client.graphql_points_remaining()

    assert requester.rate_limiting == (4990, 5000), "PyGithub's copy really is overwritten"
    assert client.rate_limit_remaining() == 4984, "the REST figure must not follow it"


@pytest.mark.requirement("L3-STA-010")
def test_a_reading_about_another_resource_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`x-ratelimit-resource` is what tells the two budgets apart."""
    client, _ = client_with(
        monkeypatch,
        rest=({"x-ratelimit-remaining": "17", "x-ratelimit-resource": "search"}, []),
    )

    client.contributors_page("pypa/virtualenv")

    assert client.rate_limit_remaining() == 0, "no core reading was ever seen"


@pytest.mark.requirement("L3-STA-010")
def test_an_unread_rest_budget_is_fetched_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """The snapshot is free and is not itself counted, so asking costs nothing."""
    client, requester = client_with(monkeypatch, rest=(CORE_HEADERS, []))

    assert client.rate_limit_remaining() == 4984
    assert len(requester.requests) == 1

    assert client.rate_limit_remaining() == 4984
    assert len(requester.requests) == 1, "the reading is kept, not re-fetched"


@pytest.mark.requirement("L3-STA-010")
def test_an_unreadable_rest_budget_reads_as_spent(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Zero refuses a run rather than letting one start on a number nothing
    confirmed - the same failure direction the GraphQL budget takes."""
    client, _ = client_with(monkeypatch, rest=({}, []))

    with caplog.at_level(logging.WARNING, logger=CLIENT_LOGGER):
        assert client.rate_limit_remaining() == 0

    assert "REST rate limit could not be read" in caplog.text


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


# ---------------------------------------------------------------------------
# The budget that arrives with every answer
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-EXH-004")
def test_a_response_carrying_a_budget_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    """This is what replaced estimating what a repository costs.

    Every collection document selects `rateLimit`, which adds no connection
    and so no cost, so the true remaining budget arrives with the answer. The
    guard reads it here rather than spending a round trip per repository.
    """
    payload = {"data": {"repository": {"name": "x"}, "rateLimit": {"remaining": 4712}}}
    client, _ = client_with(monkeypatch, graphql_payload=payload)

    assert client.observed_budget() is None, "nothing has been asked yet"
    client.graphql("query { x }", {})

    observed = client.observed_budget()
    assert observed is not None
    assert observed[0] == 4712


@pytest.mark.requirement("L3-EXH-004")
def test_a_response_without_a_budget_leaves_the_last_reading_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A shape that carries no reading is ignored rather than guessed at."""
    client, requester = client_with(
        monkeypatch, graphql_payload={"data": {"rateLimit": {"remaining": 40}}}
    )
    client.graphql("query { x }", {})

    requester.graphql_payload = {"data": {"repository": {"name": "x"}}}
    client.graphql("query { x }", {})

    observed = client.observed_budget()
    assert observed is not None
    assert observed[0] == 40


@pytest.mark.requirement("L3-EXH-004")
def test_a_refused_query_still_reports_the_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failure is exactly when the remaining budget is worth knowing."""
    client, requester = client_with(monkeypatch)

    def refuse(query: str, variables: dict[str, Any]) -> tuple[dict[str, Any], Any]:
        del query, variables
        raise GithubException(
            403,
            {"data": {"rateLimit": {"remaining": 0}}, "errors": [{"type": "RATE_LIMITED"}]},
            {},
        )

    monkeypatch.setattr(requester, "graphql_query", refuse)

    with pytest.raises(GithubException):
        client.graphql("query { x }", {})

    observed = client.observed_budget()
    assert observed is not None
    assert observed[0] == 0
