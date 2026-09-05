"""Tests for a GraphQL response that is part answer and part `NOT_FOUND`.

These exist because a live scan found what the suite could not. GitHub answers
an aliased contributor-detail document containing a bot login with **HTTP 200,
the other forty-nine accounts resolved, `null` for the bot, and a `NOT_FOUND`
entry in the `errors` array**. PyGithub maps a lone `NOT_FOUND` to
`UnknownObjectException`; `execute` read that as "the repository does not
exist"; and the `RepositoryNotFoundError` it raised is not a
`ContributorCollectionError`, so it escaped the runner's per-repository
handling and **aborted the whole run** - no CSV, no documents, exit 1.

One `dependabot[bot]` in one repository was enough to do that, which makes this
the difference between a tool that works on real inventories and one that does
not. The payloads below are the shape the live API actually returned.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from github.GithubException import GithubException, UnknownObjectException

from github_metrics.client import GitHubClient
from github_metrics.collect.contributors import get_contributors
from github_metrics.collect.graphql import execute
from github_metrics.errors import (
    ContributorCollectionError,
    GraphQLQueryError,
    RepositoryNotFoundError,
)

# What GitHub returned for a chunk whose seventh login was `hermes-seaeye[bot]`.
# Trimmed to three aliases; the shape is verbatim.
BOT_IN_THE_CHUNK: dict[str, Any] = {
    "data": {
        "u0": {"databaseId": 127238744, "name": "Teknium", "company": None, "location": None},
        "u1": None,
        "u2": {
            "databaseId": 82637225,
            "name": "kshitij",
            "company": "NousResearch",
            "location": "Delhi",
        },
    },
    "errors": [
        {
            "type": "NOT_FOUND",
            "path": ["u1"],
            "message": "Could not resolve to a User with the login of 'hermes-seaeye[bot]'.",
        }
    ],
}

REPOSITORY_GONE: dict[str, Any] = {
    "data": {"repository": None},
    "errors": [{"type": "NOT_FOUND", "message": "Could not resolve to a Repository"}],
}


class _StubClient:
    """Raises what PyGithub would raise, or returns a payload directly."""

    def __init__(self, *, raises: Exception | None = None, payload: Any = None) -> None:
        self.raises = raises
        self.payload = payload

    def graphql(
        self, query: str, variables: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Mimic `GitHubClient.graphql`."""
        del query, variables
        if self.raises is not None:
            raise self.raises
        return {}, cast(dict[str, Any], self.payload)


def run(stub: _StubClient, **kwargs: Any) -> dict[str, Any]:
    """Execute against a stub client."""
    return execute(cast(GitHubClient, stub), "query { x }", {}, **kwargs)


# ---------------------------------------------------------------------------
# The tolerant path
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-MET-021")
def test_a_bot_alias_does_not_lose_the_other_accounts_in_its_chunk() -> None:
    """The regression. Forty-nine good answers were being thrown away."""
    stub = _StubClient(
        raises=UnknownObjectException(404, BOT_IN_THE_CHUNK, {}, "Could not resolve to a User")
    )

    data = run(stub, tolerate_missing=True)

    assert data["u0"]["name"] == "Teknium"
    assert data["u2"]["location"] == "Delhi"
    # The one that did not resolve is present and null, which is what the
    # caller reads as "no detail for this account".
    assert data["u1"] is None


@pytest.mark.requirement("L3-MET-021")
def test_the_tolerant_path_also_works_when_the_transport_does_not_raise() -> None:
    """A response carrying errors without PyGithub raising is the same case."""
    data = run(_StubClient(payload=BOT_IN_THE_CHUNK), tolerate_missing=True)

    assert data["u0"]["name"] == "Teknium"
    assert data["u1"] is None


@pytest.mark.requirement("L3-MET-021")
def test_several_missing_aliases_are_tolerated_together() -> None:
    """A chunk can contain more than one bot, and often does."""
    payload = {
        "data": {"u0": None, "u1": {"databaseId": 1, "name": "Real"}, "u2": None},
        "errors": [
            {"type": "NOT_FOUND", "path": ["u0"], "message": "Could not resolve to a User"},
            {"type": "NOT_FOUND", "path": ["u2"], "message": "Could not resolve to a User"},
        ],
    }
    stub = _StubClient(raises=GithubException(400, payload, {}))

    data = run(stub, tolerate_missing=True)

    assert data["u1"]["name"] == "Real"
    assert data["u0"] is None and data["u2"] is None


# ---------------------------------------------------------------------------
# What the tolerant path must NOT swallow
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-MET-021")
def test_a_missing_repository_is_still_a_missing_repository() -> None:
    """The repository query does not opt in, and must not start tolerating."""
    stub = _StubClient(raises=UnknownObjectException(404, REPOSITORY_GONE, {}, "Could not resolve"))

    with pytest.raises(RepositoryNotFoundError):
        run(stub)


@pytest.mark.requirement("L3-MET-021")
def test_an_error_that_is_not_not_found_still_raises_even_when_tolerating() -> None:
    """Tolerance is for one error type. A rate limit is not a missing account."""
    payload = {
        "data": {"u0": None},
        "errors": [{"type": "RATE_LIMITED", "message": "API rate limit exceeded"}],
    }

    with pytest.raises(GraphQLQueryError):
        run(_StubClient(raises=GithubException(403, payload, {})), tolerate_missing=True)


@pytest.mark.requirement("L3-MET-021")
def test_a_mixed_error_list_is_a_failure_and_never_a_missing_repository() -> None:
    """One NOT_FOUND beside a real failure is a real failure.

    And it must not be reported as a missing repository: this document names
    no repository, so that classification would send an operator to fix an
    inventory that is correct.
    """
    payload = {
        "data": {"u0": None, "u1": None},
        "errors": [
            {"type": "NOT_FOUND", "path": ["u0"], "message": "Could not resolve to a User"},
            {"type": "RATE_LIMITED", "message": "API rate limit exceeded"},
        ],
    }

    with pytest.raises(GraphQLQueryError):
        run(_StubClient(raises=GithubException(403, payload, {})), tolerate_missing=True)


@pytest.mark.requirement("L3-MET-021")
def test_a_response_with_no_data_object_is_not_salvaged() -> None:
    """Nothing to tolerate; this is a failed query however it is labelled."""
    payload = {"errors": [{"type": "NOT_FOUND", "message": "Could not resolve to a User"}]}

    with pytest.raises(RepositoryNotFoundError):
        run(_StubClient(raises=UnknownObjectException(404, payload, {}, "gone")))


# ---------------------------------------------------------------------------
# End to end: a bot must cost its own record's detail and nothing else
# ---------------------------------------------------------------------------


class _Account:
    def __init__(self, login: str, identifier: int, contributions: int) -> None:
        self.login = login
        self.id = identifier
        self.contributions = contributions


class _Repository:
    def __init__(self, accounts: list[_Account]) -> None:
        self._accounts = accounts

    def get_contributors(self) -> list[_Account]:
        """Mimic the paginated REST list."""
        return self._accounts


class _BotClient:
    """Lists three contributors, one of them a bot GraphQL cannot resolve."""

    @staticmethod
    def repository(full_name: str) -> _Repository:
        """Mimic the REST repository lookup."""
        del full_name
        return _Repository(
            [
                _Account("teknium1", 127238744, 500),
                _Account("hermes-seaeye[bot]", 999, 250),
                _Account("kshitijk4poor", 82637225, 100),
            ]
        )

    @staticmethod
    def graphql(query: str, variables: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """Answer as GitHub did: the bot null, and a NOT_FOUND beside it."""
        del query, variables
        raise UnknownObjectException(404, BOT_IN_THE_CHUNK, {}, "Could not resolve to a User")


@pytest.mark.requirement("L3-MET-021")
def test_a_bot_contributor_keeps_its_commits_and_costs_no_one_else_their_detail() -> None:
    collected = get_contributors(cast(GitHubClient, _BotClient()), "NousResearch", "hermes-agent")

    assert len(collected) == 3
    # The accounts either side of the bot keep the detail GitHub returned.
    assert collected[0].name == "Teknium"
    assert collected[2].organization == "NousResearch"
    # The bot is recorded rather than dropped: its commits are real commits in
    # this repository, and dropping it would quietly reduce contribution_total.
    assert collected[1].name == "hermes-seaeye[bot]"
    assert collected[1].contribution == 250
    assert collected[1].internal_address.query is None


class _BrokenDetailClient(_BotClient):
    """Lists contributors fine, then fails the detail query for a real reason."""

    @staticmethod
    def graphql(query: str, variables: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """Fail in a way tolerance must not swallow."""
        del query, variables
        raise GithubException(
            403,
            {"errors": [{"type": "RATE_LIMITED", "message": "API rate limit exceeded"}]},
            {},
        )


@pytest.mark.requirement("L3-MET-021")
def test_a_detail_failure_degrades_the_repository_instead_of_killing_the_run() -> None:
    """The contract is a row and no document, never an abandoned run.

    `execute` raises this package's own errors, none of which is a
    `ContributorCollectionError`, and the runner catches only that one for the
    contributor half. Without translation here, a rate limit on the detail
    query takes down every other repository in the inventory too.
    """
    with pytest.raises(ContributorCollectionError):
        get_contributors(cast(GitHubClient, _BrokenDetailClient()), "NousResearch", "hermes-agent")
