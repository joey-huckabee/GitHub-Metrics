"""Tests for :mod:`github_metrics.collect.contributors`.

The expensive mistake this module exists to avoid is reading account details
through REST, which costs one request per contributor. The check that it has
not crept back in is the shape of the GraphQL document: one aliased
single-object selection per account, and no `nodes`.
"""

from __future__ import annotations

import logging
from typing import Any, cast

import pytest
from github.GithubException import GithubException

from github_metrics.client import GitHubClient
from github_metrics.collect.contributors import (
    DEFAULT_CONTRIBUTOR_LIMIT,
    ContributorAccount,
    _details_query,
    get_account_details,
    get_contributors,
)
from github_metrics.errors import ContributorCollectionError
from github_metrics.model.contributor import Address, Coordinates

COLLECT_LOGGER = "github_metrics.collect.contributors"


class _Account:
    """One entry of the REST contributors list."""

    def __init__(self, login: str, identifier: int, contributions: int) -> None:
        self.login = login
        self.id = identifier
        self.contributions = contributions


class _Repository:
    """Just enough of a PyGithub repository to list contributors."""

    def __init__(self, accounts: list[_Account]) -> None:
        self._accounts = accounts

    def get_contributors(self) -> list[_Account]:
        """Mimic the paginated list, which the caller slices."""
        return self._accounts


class _StubClient:
    """Answers the REST list and the aliased detail document."""

    def __init__(
        self,
        accounts: list[_Account] | None = None,
        details: dict[str, Any] | None = None,
        *,
        rest_fails: bool = False,
    ) -> None:
        self.accounts = accounts if accounts is not None else [_Account("alice", 1, 120)]
        self.details = details
        self.rest_fails = rest_fails
        self.queries: list[str] = []

    def repository(self, full_name: str) -> _Repository:
        """Mimic the REST repository lookup."""
        del full_name
        if self.rest_fails:
            raise GithubException(404, {"message": "Not Found"}, {})
        return _Repository(self.accounts)

    def graphql(
        self, query: str, variables: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Mimic the aliased detail document."""
        self.queries.append(query)
        if self.details is not None:
            return {}, {"data": self.details}
        return {}, {
            "data": {
                f"u{index}": {
                    "databaseId": 1000 + index,
                    "name": f"Person {index}",
                    "company": "Acme",
                    "location": "Austin, TX",
                }
                for index in range(len(variables))
            }
        }


class _StubGeocoder:
    """Resolves anything to one fixed address, and counts the asking."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def locate(self, location: str) -> Address:
        """Resolve anything to one fixed address."""
        self.asked.append(location)
        return Address(
            query=location,
            formatted_address="Austin, Travis County, Texas, United States",
            country="United States",
            country_code="us",
            city="Austin",
            internal_location=Coordinates(latitude=30.2711, longitude=-97.7437),
        )


def collect(stub: _StubClient, **kwargs: Any) -> Any:
    """Collect against a stub client."""
    return get_contributors(cast(GitHubClient, stub), "pypa", "virtualenv", **kwargs)


# ---------------------------------------------------------------------------
# The query, which is where the cost lives
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-MET-017")
def test_the_detail_query_asks_for_every_account_at_once() -> None:
    """One document, not one request per contributor.

    Reading these through REST would complete each account lazily - 26
    requests per repository at the default limit - and a 200-repository
    inventory would exhaust the REST budget before it finished.
    """
    query = _details_query(3)

    assert query.count("user(login: $login") == 3
    assert "$login0: String!" in query
    assert "$login2: String!" in query


@pytest.mark.requirement("L3-MET-017")
def test_the_detail_query_selects_no_nodes() -> None:
    """`nodes` prices a query by how many objects it could return.

    Every alias selects a single object, so the document stays at the cheap
    end of the cost formula however long the contributor list is. This is the
    same condition `collect/repository.py` is held to.
    """
    assert "nodes" not in _details_query(DEFAULT_CONTRIBUTOR_LIMIT)


@pytest.mark.requirement("L3-MET-017")
def test_aliases_are_positional_rather_than_logins() -> None:
    """A GraphQL alias cannot contain a hyphen; a GitHub login can."""
    stub = _StubClient([_Account("some-user", 7, 3)])

    collect(stub)

    assert "some-user" not in stub.queries[0]
    assert "u0: user(" in stub.queries[0]


# ---------------------------------------------------------------------------
# What a record carries
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-MET-017")
def test_a_contributor_carries_its_identity_and_its_commits() -> None:
    people = collect(_StubClient([_Account("alice", 42, 120)]))

    assert len(people) == 1
    assert people[0].github_id == "42"
    assert people[0].name == "Person 0"
    assert people[0].organization == "Acme"
    assert people[0].contribution == 120


@pytest.mark.requirement("L3-MET-017")
def test_the_judgement_columns_are_not_asserted() -> None:
    """`foreign` and `adversarial` have no definition in this repository.

    `None` rather than `False`, because `False` is an assertion about a named
    person that nothing here has measured.
    """
    people = collect(_StubClient())

    assert people[0].foreign is None
    assert people[0].adversarial is None


@pytest.mark.requirement("L3-MET-017")
def test_an_account_with_no_name_falls_back_to_its_login() -> None:
    stub = _StubClient(
        [_Account("alice", 1, 5)],
        details={"u0": {"databaseId": 1, "name": None, "company": None, "location": None}},
    )

    people = collect(stub)

    assert people[0].name == "alice"
    assert people[0].organization == ""
    assert people[0].location is None


@pytest.mark.requirement("L3-MET-017")
def test_an_account_that_vanished_is_still_recorded(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Deleted or suspended between the REST list and the GraphQL lookup.

    Its `contribution` is a real measurement of this repository, so dropping
    the record would quietly reduce `contribution_total`.
    """
    stub = _StubClient([_Account("ghost", 9, 77)], details={"u0": None})

    with caplog.at_level(logging.DEBUG, logger=COLLECT_LOGGER):
        people = collect(stub)

    assert len(people) == 1
    assert people[0].name == "ghost"
    assert people[0].contribution == 77
    assert "ghost" in caplog.text


# ---------------------------------------------------------------------------
# Geocoding
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-MET-018")
def test_a_published_location_is_resolved() -> None:
    geocoder = _StubGeocoder()

    people = collect(_StubClient(), geocoder=cast(Any, geocoder))

    assert geocoder.asked == ["Austin, TX"]
    assert people[0].internal_address.city == "Austin"
    assert people[0].internal_address.internal_location.latitude == 30.2711


@pytest.mark.requirement("L3-MET-018")
def test_no_location_is_never_looked_up() -> None:
    """Nothing was published, so there is nothing to ask, and nothing is known."""
    geocoder = _StubGeocoder()
    stub = _StubClient(
        [_Account("alice", 1, 5)],
        details={"u0": {"databaseId": 1, "name": "Alice", "company": "", "location": None}},
    )

    people = collect(stub, geocoder=cast(Any, geocoder))

    assert not geocoder.asked
    assert people[0].internal_address == Address()
    assert people[0].internal_address.internal_location.latitude is None


# ---------------------------------------------------------------------------
# Failure
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-MET-017")
def test_an_unreadable_contributor_list_is_its_own_failure() -> None:
    """Separate from a repository that could not be read at all.

    This one was read; only its second half failed, so the row keeps its
    measurements while the document is withheld.
    """
    with pytest.raises(ContributorCollectionError) as caught:
        collect(_StubClient(rest_fails=True))

    assert caught.value.code == "GM-COL-005"
    assert "pypa/virtualenv" in str(caught.value)


@pytest.mark.requirement("L3-MET-017")
def test_no_accounts_asks_for_no_details() -> None:
    """An empty document would be a wasted point and a GraphQL syntax error."""
    stub = _StubClient([])

    assert not get_account_details(cast(GitHubClient, stub), [], slug="pypa/virtualenv")
    assert not stub.queries


@pytest.mark.requirement("L3-MET-017")
def test_the_list_is_truncated_at_the_limit(caplog: pytest.LogCaptureFixture) -> None:
    """`contribution_total` counts what was collected, so the cut is narrated."""
    accounts = [_Account(f"user{index}", index, 10) for index in range(5)]

    with caplog.at_level(logging.DEBUG, logger=COLLECT_LOGGER):
        people = collect(_StubClient(accounts), limit=5)

    assert len(people) == 5
    assert "truncated at the limit of 5" in caplog.text


@pytest.mark.requirement("L3-MET-017")
def test_an_account_record_is_immutable() -> None:
    account = ContributorAccount(login="alice", github_id="1", contribution=5)

    with pytest.raises(AttributeError):
        account.contribution = 6  # type: ignore[misc]
