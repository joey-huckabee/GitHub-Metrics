"""Tests for attributing every commit by walking the history.

This route exists for the question a sample cannot answer: *is there any
adversarial contributor here?* A single one-commit account is exactly what the
contributors endpoint omits, so completeness is the whole point and the tests
are mostly about not quietly losing a commit.

The costly part is deliberate. `nodes` is forbidden everywhere else in this
package precisely because it prices a query by the objects it could return;
here that pricing is what is being paid for, measured at one point per hundred
commits.
"""

from __future__ import annotations

import logging
from typing import Any, cast

import pytest

from github_metrics.client import GitHubClient
from github_metrics.collect.history import (
    HISTORY_QUERY,
    MAX_PAGES,
    PAGE_SIZE,
    attribute_from_history,
)

HISTORY_LOGGER = "github_metrics.collect.history"


def commit(login: str | None, identifier: int = 1) -> dict[str, Any]:
    """One history node, shaped as GitHub returns it."""
    if login is None:
        # An author whose email belongs to no account. Nothing can attribute
        # these, by any route.
        return {"author": {"user": None}}
    return {"author": {"user": {"databaseId": identifier, "login": login}}}


def page(nodes: list[dict[str, Any]], *, more: bool = False, cursor: str = "c") -> Any:
    """One page of history."""
    return {
        "repository": {
            "defaultBranchRef": {
                "target": {
                    "history": {
                        "pageInfo": {"hasNextPage": more, "endCursor": cursor},
                        "nodes": nodes,
                    }
                }
            }
        }
    }


class _StubClient:
    """Serves prepared pages of commit history."""

    def __init__(self, *pages: Any) -> None:
        self.pages = list(pages)
        self.cursors: list[str | None] = []

    def graphql(
        self, query: str, variables: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Mimic `GitHubClient.graphql`."""
        del query
        self.cursors.append(variables.get("cursor"))
        index = min(len(self.cursors) - 1, len(self.pages) - 1)
        return {}, {"data": self.pages[index]}


def walk(stub: _StubClient) -> Any:
    """Attribute against a stub."""
    return attribute_from_history(cast(GitHubClient, stub), "pypa", "virtualenv")


# ---------------------------------------------------------------------------
# The query itself
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-ATT-001")
def test_this_is_the_one_query_that_asks_for_nodes() -> None:
    """Forbidden everywhere else, and unavoidable here.

    There is no way to see individual commits without it, and the pricing the
    rule avoids is exactly what this route pays for.
    """
    assert "nodes" in HISTORY_QUERY
    assert f"first: {PAGE_SIZE}" in HISTORY_QUERY


@pytest.mark.requirement("L3-ATT-001")
def test_pages_are_the_endpoint_maximum_because_a_page_is_a_point() -> None:
    """One point buys one page, so a smaller page would cost strictly more."""
    assert PAGE_SIZE == 100


# ---------------------------------------------------------------------------
# Attribution
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-ATT-001")
def test_commits_are_counted_per_account() -> None:
    stub = _StubClient(page([commit("alice", 1), commit("bob", 2), commit("alice", 1)]))

    found = walk(stub)

    assert found.commits_walked == 3
    assert [(a.login, a.contribution) for a in found.accounts] == [("alice", 2), ("bob", 1)]


@pytest.mark.requirement("L3-ATT-001")
def test_accounts_come_back_ranked_by_commits() -> None:
    """The contributors endpoint ranks its own list, and a concentration figure
    computed over an unranked one would be wrong."""
    stub = _StubClient(page([commit("small"), *(commit("big", 2) for _ in range(5))]))

    assert [a.login for a in walk(stub).accounts] == ["big", "small"]


@pytest.mark.requirement("L3-ATT-001")
def test_a_commit_with_no_account_is_counted_rather_than_dropped() -> None:
    """These are the irreducible floor: no route attributes them, so silently
    dropping them would overstate coverage."""
    stub = _StubClient(page([commit("alice"), commit(None), commit(None)]))

    found = walk(stub)

    assert found.commits_walked == 3
    assert found.unattributed_commits == 2
    assert len(found.accounts) == 1


@pytest.mark.requirement("L3-ATT-002")
def test_a_bot_is_recognised_from_its_reserved_login_suffix() -> None:
    """`Commit.author.user` carries no account type, and without this a deep run
    would report zero bots while carrying several.

    The suffix is authoritative rather than a guess: an account name is
    `^[A-Za-z0-9-]+$`, so a bracket cannot appear in a login anyone chose.
    """
    stub = _StubClient(page([commit("dependabot[bot]"), commit("alice")]))

    by_login = {a.login: a for a in walk(stub).accounts}

    assert by_login["dependabot[bot]"].is_bot
    assert not by_login["alice"].is_bot


# ---------------------------------------------------------------------------
# Paging
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-ATT-001")
def test_paging_follows_the_cursor_until_the_history_ends() -> None:
    stub = _StubClient(
        page([commit("alice")], more=True, cursor="one"),
        page([commit("bob")], more=True, cursor="two"),
        page([commit("carol")], more=False),
    )

    found = walk(stub)

    assert found.commits_walked == 3
    assert found.pages == 3
    assert stub.cursors == [None, "one", "two"]


@pytest.mark.requirement("L3-ATT-001")
def test_pages_are_the_points_spent() -> None:
    """One page is one point, so the count is also the bill."""
    stub = _StubClient(page([commit("alice")], more=True), page([commit("bob")]))

    assert walk(stub).pages == 2


@pytest.mark.requirement("L3-ATT-002")
def test_an_endless_history_stops_rather_than_spending_the_whole_budget(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A repository past the cap would spend a run's entire hourly quota on
    itself, and doing that silently is worse than saying so."""
    stub = _StubClient(page([commit("alice")], more=True))

    with caplog.at_level(logging.WARNING, logger=HISTORY_LOGGER):
        found = walk(stub)

    assert found.pages == MAX_PAGES
    assert found.truncated
    assert "stopped attributing" in caplog.text


# ---------------------------------------------------------------------------
# Repositories with nothing to walk
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-ATT-001")
def test_a_repository_with_no_default_branch_attributes_nothing() -> None:
    """An empty repository is a real state rather than a failure."""
    stub = _StubClient({"repository": {"defaultBranchRef": None}})

    found = walk(stub)

    assert found.commits_walked == 0
    assert not found.accounts
    assert not found.truncated


@pytest.mark.requirement("L3-ATT-001")
def test_a_malformed_payload_is_treated_as_nothing_to_walk() -> None:
    stub = _StubClient({"repository": {}})

    assert walk(stub).commits_walked == 0
