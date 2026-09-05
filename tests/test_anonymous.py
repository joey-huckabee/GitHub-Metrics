"""Tests for recovering accounts GitHub declined to link.

The claim this module makes is narrow and load-bearing: it **invents nobody**.
An account is recovered only from GitHub's own no-reply format, which embeds
the id and login and round-trips against the API. Everything else in the
anonymous tail is counted and left alone.

Most of what is checked here is what must *not* be recovered, because a false
positive would put a real person's login against commits that are not theirs.
"""

from __future__ import annotations

import logging
from typing import Any, cast

import pytest
from github.GithubException import GithubException

from github_metrics.client import GitHubClient
from github_metrics.collect.anonymous import NOREPLY, collect_anonymous
from github_metrics.errors import ContributorCollectionError

ANON_LOGGER = "github_metrics.collect.anonymous"


def anonymous(email: str, commits: int = 1, name: str = "Someone") -> dict[str, Any]:
    """One anonymous entry, shaped as GitHub returns it."""
    return {"type": "Anonymous", "name": name, "email": email, "contributions": commits}


def linked(login: str, commits: int = 10) -> dict[str, Any]:
    """One ordinary entry, which the normal path already collected."""
    return {"type": "User", "login": login, "id": 1, "contributions": commits}


class _StubClient:
    """Serves prepared pages of the anonymous-inclusive contributor list."""

    def __init__(self, *pages: list[dict[str, Any]], raises: Exception | None = None) -> None:
        self.pages = list(pages)
        self.raises = raises
        self.requested: list[int] = []

    def contributors_page(self, slug: str, **kwargs: Any) -> tuple[dict[str, Any], Any]:
        """Mimic `GitHubClient.contributors_page`."""
        del slug
        if self.raises is not None:
            raise self.raises
        page = int(kwargs.get("page", 1))
        self.requested.append(page)
        if page > len(self.pages):
            return {}, []
        return {}, self.pages[page - 1]


def walk(stub: _StubClient, per_page: int = 100) -> Any:
    """Walk the tail against a stub."""
    return collect_anonymous(cast(GitHubClient, stub), "pypa", "virtualenv", per_page=per_page)


# ---------------------------------------------------------------------------
# What is recovered
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-STA-009")
def test_a_no_reply_address_yields_the_account_it_names() -> None:
    """GitHub's own format, verified to round-trip against the live API."""
    tally = walk(_StubClient([anonymous("69859316+dk96-os@users.noreply.github.com", 7)]))

    assert len(tally.recovered) == 1
    assert tally.recovered[0].login == "dk96-os"
    assert tally.recovered[0].github_id == "69859316"
    assert tally.recovered[0].contribution == 7


@pytest.mark.requirement("L3-STA-009")
def test_one_account_under_several_addresses_is_merged_and_summed() -> None:
    """Keeping only the last would understate a real contributor."""
    tally = walk(
        _StubClient(
            [
                anonymous("1+alice@users.noreply.github.com", 5),
                anonymous("1+alice@users.noreply.github.com", 3),
            ]
        )
    )

    assert len(tally.recovered) == 1
    assert tally.recovered[0].contribution == 8
    # Two identities, one account: the breakdown counts the former.
    assert tally.recovered_identities == 2
    assert tally.identities == 2


# ---------------------------------------------------------------------------
# What must not be recovered
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-STA-009")
@pytest.mark.parametrize(
    "email",
    [
        "elmir.jagudin@maxiv.lu.se",
        "ebraun@o2.pl",
        "chris.szafranek@zalando.de",
        # The older idless form. Without the id there is nothing separating a
        # real account from a plausible string, so it is deliberately refused.
        "alice@users.noreply.github.com",
        # Lookalikes.
        "1+alice@users.noreply.github.com.evil.test",
        "1+alice@notusers.noreply.github.com",
        "+alice@users.noreply.github.com",
        "",
    ],
)
def test_an_address_that_does_not_name_an_account_is_not_recovered(email: str) -> None:
    """A false positive would attribute commits to a real person who did not
    make them, which is worse than leaving the entry uncollected."""
    tally = walk(_StubClient([anonymous(email)]))

    assert not tally.recovered
    assert tally.unrecoverable_people == 1


@pytest.mark.requirement("L3-STA-009")
def test_the_pattern_itself_refuses_the_idless_form() -> None:
    assert NOREPLY.match("123+alice@users.noreply.github.com") is not None
    assert NOREPLY.match("alice@users.noreply.github.com") is None


# ---------------------------------------------------------------------------
# Counting
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-STA-009")
def test_the_tail_is_counted_in_people_and_commits() -> None:
    """Walking the pages is what makes the commits knowable at all."""
    tally = walk(
        _StubClient(
            [
                anonymous("1+alice@users.noreply.github.com", 10),
                anonymous("real@example.test", 4),
                anonymous("other@example.test", 6),
            ]
        )
    )

    assert tally.identities == 3
    assert tally.commits == 20
    assert tally.unrecoverable_people == 2
    assert tally.unrecoverable_commits == 10


@pytest.mark.requirement("L3-STA-009")
def test_linked_entries_are_skipped_because_they_are_already_collected() -> None:
    tally = walk(_StubClient([linked("bob"), anonymous("real@example.test", 2)]))

    assert tally.identities == 1
    assert tally.commits == 2


@pytest.mark.requirement("L3-STA-009")
def test_every_page_is_walked_until_a_short_one_ends_it() -> None:
    full = [anonymous(f"{index}@example.test") for index in range(2)]
    stub = _StubClient(full, full, [anonymous("last@example.test")])

    tally = walk(stub, per_page=2)

    assert tally.identities == 5
    assert stub.requested == [1, 2, 3]


@pytest.mark.requirement("L3-STA-009")
def test_a_repository_with_no_anonymous_tail_reports_nothing() -> None:
    tally = walk(_StubClient([linked("bob")]))

    assert tally.identities == 0
    assert not tally.recovered


@pytest.mark.requirement("L3-STA-009")
def test_a_failed_page_degrades_the_repository_rather_than_the_run() -> None:
    stub = _StubClient(raises=GithubException(500, {"message": "boom"}, {}))

    with pytest.raises(ContributorCollectionError) as caught:
        walk(stub)

    assert "could not read anonymous contributors" in str(caught.value)


@pytest.mark.requirement("L3-STA-009")
def test_what_was_recovered_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger=ANON_LOGGER):
        walk(_StubClient([anonymous("1+alice@users.noreply.github.com", 3)]))

    assert "recovered" in caplog.text
