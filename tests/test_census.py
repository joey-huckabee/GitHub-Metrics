"""Tests for the contributor identity census.

The census exists so `contributors.coverage_percent` has a real denominator.
Without it the fraction is `collected / collected` — 100% for a repository
whose real coverage is 11.9%, a number that overstates its own completeness.

What is checked here is mostly the `Link` header, because that header is the
whole reason the census is one request rather than thirty-four, and because
reading it slightly wrong fails *silently*: the code falls back to counting the
entries on the page, which for `per_page=1` is a plausible-looking `1`.
"""

from __future__ import annotations

import logging
from typing import Any, cast

import pytest
from github.GithubException import GithubException

from github_metrics.client import GitHubClient
from github_metrics.collect.census import count_identities
from github_metrics.errors import ContributorCollectionError

CENSUS_LOGGER = "github_metrics.collect.census"

# The header GitHub actually returns, verbatim. Note that the page number is
# *not* at the end of the URL - `&per_page=1` follows it - which is exactly
# what a naive pattern gets wrong.
REAL_HEADER = (
    "<https://api.github.com/repositories/1024554267/contributors"
    '?anon=1&page=2&per_page=1>; rel="next", '
    "<https://api.github.com/repositories/1024554267/contributors"
    '?anon=1&page=3310&per_page=1>; rel="last"'
)


class _StubClient:
    """Answers one contributors page with whatever header a test needs."""

    def __init__(
        self,
        link: str | None = None,
        payload: Any = None,
        *,
        raises: Exception | None = None,
    ) -> None:
        self.link = link
        self.payload = payload if payload is not None else [{}]
        self.raises = raises
        self.calls: list[dict[str, Any]] = []

    def contributors_page(self, slug: str, **kwargs: Any) -> tuple[dict[str, Any], Any]:
        """Mimic `GitHubClient.contributors_page`."""
        self.calls.append({"slug": slug, **kwargs})
        if self.raises is not None:
            raise self.raises
        return ({"link": self.link} if self.link else {}), self.payload


def count(stub: _StubClient) -> int | None:
    """Run the census against a stub."""
    return count_identities(cast(GitHubClient, stub), "NousResearch", "hermes-agent")


@pytest.mark.requirement("L3-STA-007")
def test_the_last_page_number_is_the_identity_count() -> None:
    """`per_page=1` makes the last page number the total, in one request."""
    stub = _StubClient(REAL_HEADER)

    assert count(stub) == 3310
    assert len(stub.calls) == 1


@pytest.mark.requirement("L3-STA-007")
def test_the_page_number_is_read_even_though_it_does_not_end_the_url() -> None:
    """The failure this pins was silent.

    GitHub writes `...&page=3310&per_page=1`, so a pattern expecting
    `page=(\\d+)>` matches nothing, falls through to counting the entries on the
    page, and reports `1` — a plausible number for a repository with 3,310
    contributors.
    """
    assert count(_StubClient(REAL_HEADER)) != 1


@pytest.mark.requirement("L3-STA-007")
def test_the_census_asks_for_anonymous_contributors_one_per_page() -> None:
    """Both parameters are load-bearing: `anon` for the population, `per_page`
    for the arithmetic."""
    stub = _StubClient(REAL_HEADER)

    count(stub)

    assert stub.calls[0]["anonymous"] is True
    assert stub.calls[0]["per_page"] == 1


@pytest.mark.requirement("L3-STA-007")
def test_the_last_link_is_read_rather_than_the_first_one_present() -> None:
    """The header also carries `next`, `first` and `prev`.

    Matching the wrong entry would report the current position as though it
    were the total.
    """
    header = (
        '<https://api.github.com/x?page=1&per_page=1>; rel="first", '
        '<https://api.github.com/x?page=2&per_page=1>; rel="next", '
        '<https://api.github.com/x?page=900&per_page=1>; rel="last"'
    )

    assert count(_StubClient(header)) == 900


@pytest.mark.requirement("L3-STA-007")
def test_a_single_page_result_counts_the_entries_it_got() -> None:
    """No `rel="last"` means there is only one page."""
    assert count(_StubClient(None, payload=[{}])) == 1


@pytest.mark.requirement("L3-STA-007")
def test_a_repository_with_no_contributors_counts_zero() -> None:
    """Zero here is a measurement: the list was read and was empty."""
    assert count(_StubClient(None, payload=[])) == 0


@pytest.mark.requirement("L3-STA-007")
def test_an_unreadable_header_and_payload_is_unknown_rather_than_zero(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An invented denominator would make coverage look measured."""
    with caplog.at_level(logging.WARNING, logger=CENSUS_LOGGER):
        found = count(_StubClient(None, payload={"unexpected": "shape"}))

    assert found is None
    assert "could not be read" in caplog.text


@pytest.mark.requirement("L3-STA-007")
def test_a_failed_request_raises_the_contributor_error() -> None:
    """Raised as `ContributorCollectionError` so the runner degrades the
    repository rather than ending the run — the same contract every other
    contributor-side failure follows."""
    stub = _StubClient(raises=GithubException(500, {"message": "boom"}, {}))

    with pytest.raises(ContributorCollectionError) as caught:
        count(stub)

    assert "could not count contributor identities" in str(caught.value)


@pytest.mark.requirement("L3-STA-007")
def test_the_count_is_logged_so_a_surprising_coverage_can_be_traced(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG, logger=CENSUS_LOGGER):
        count(_StubClient(REAL_HEADER))

    assert "3310" in caplog.text
