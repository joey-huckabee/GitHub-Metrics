"""Tests for :mod:`github_metrics.collect.runner` and the budget pre-flight."""

from __future__ import annotations

import logging
import threading
from typing import Any, cast

import pytest

from github_metrics.client import GitHubClient
from github_metrics.collect.budget import RESERVE_POINTS, Budget, check_budget
from github_metrics.collect.runner import collect_all
from github_metrics.errors import RateLimitExhaustedError, RepositoryNotFoundError
from github_metrics.sources import RepositoryRef

RUNNER_LOGGER = "github_metrics.collect.runner"

REFERENCES = [
    RepositoryRef(owner="pypa", repoid="virtualenv"),
    RepositoryRef(owner="ghost", repoid="missing"),
    RepositoryRef(owner="urllib3", repoid="urllib3"),
]


class _StubClient:
    """Answers a repository query, and can be told to fail for one slug."""

    def __init__(self, *, missing: set[str] | None = None, points: int = 5000) -> None:
        self.missing = missing or {"ghost/missing"}
        self.points = points
        self.calls: list[str] = []
        self.concurrent = 0
        self.peak = 0
        self._lock = threading.Lock()

    def graphql(
        self, query: str, variables: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Mimic `GitHubClient.graphql`, recording concurrency as it goes."""
        del query
        slug = f"{variables['owner']}/{variables['name']}"
        with self._lock:
            self.calls.append(slug)
            self.concurrent += 1
            self.peak = max(self.peak, self.concurrent)
        try:
            if slug in self.missing:
                return {}, {"data": {"repository": None}}
            return {}, _payload(variables["owner"], variables["name"])
        finally:
            with self._lock:
                self.concurrent -= 1

    def graphql_points_remaining(self) -> int:
        """Mimic the budget lookup."""
        return self.points


def _payload(owner: str, name: str) -> dict[str, Any]:
    """A successful repository response for any slug."""
    return {
        "data": {
            "repository": {
                "name": name,
                "owner": {"login": owner, "__typename": "Organization"},
                "stargazerCount": 5041,
                "forkCount": 1114,
                "createdAt": "2011-02-16T00:00:00Z",
                "updatedAt": "2026-08-30T00:00:00Z",
                "pushedAt": "2026-08-30T00:00:00Z",
                "hasIssuesEnabled": True,
                "closedIssues": {"totalCount": 1429},
                "openIssues": {"totalCount": 0},
                "releases": {"totalCount": 98},
                "tags": {"totalCount": 285},
            }
        }
    }


def run(stub: _StubClient, references: list[RepositoryRef] | None = None, **kwargs: Any) -> Any:
    """Collect against a stub client. An explicit empty list stays empty."""
    chosen = REFERENCES if references is None else references
    return collect_all(cast(GitHubClient, stub), chosen, **kwargs)


# ---------------------------------------------------------------------------
# Every reference produces an outcome
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-COL-001")
def test_a_failure_does_not_take_the_rest_of_the_run_with_it() -> None:
    """The output has one row per accepted input row.

    Letting the exception propagate would abandon the repositories after this
    one and discard the ones already paid for.
    """
    outcomes = run(_StubClient())

    assert len(outcomes) == 3
    assert [outcome.ok for outcome in outcomes] == [True, False, True]
    assert isinstance(outcomes[1].error, RepositoryNotFoundError)
    assert outcomes[1].metadata is None


@pytest.mark.requirement("L3-COL-001")
def test_an_outcome_keeps_the_reference_that_produced_it() -> None:
    outcomes = run(_StubClient())

    assert [outcome.reference.full_name for outcome in outcomes] == [
        "pypa/virtualenv",
        "ghost/missing",
        "urllib3/urllib3",
    ]


@pytest.mark.requirement("L3-COL-001")
def test_nothing_to_collect_is_not_an_error() -> None:
    stub = _StubClient()

    assert not run(stub, [])
    assert not stub.calls


@pytest.mark.requirement("L3-COL-001")
def test_the_failures_are_named_in_the_log(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger=RUNNER_LOGGER):
        run(_StubClient())

    assert "ghost/missing" in caplog.text
    assert "1 of 3 repositories could not be collected" in caplog.text


# ---------------------------------------------------------------------------
# Order, and the concurrency that could break it
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-COL-002")
def test_results_come_back_in_input_order_not_completion_order() -> None:
    """These files are diffed over time.

    `Executor.map` rather than `as_completed` is what makes two runs of one
    inventory produce byte-identical output.
    """
    many = [RepositoryRef(owner="owner", repoid=f"repo{index:02d}") for index in range(24)]

    outcomes = run(_StubClient(missing=set()), many, max_workers=8)

    assert [outcome.reference.repoid for outcome in outcomes] == [
        reference.repoid for reference in many
    ]


@pytest.mark.requirement("L3-COL-002")
def test_the_worker_count_is_respected() -> None:
    many = [RepositoryRef(owner="owner", repoid=f"repo{index:02d}") for index in range(24)]
    stub = _StubClient(missing=set())

    run(stub, many, max_workers=4)

    assert stub.peak <= 4


@pytest.mark.requirement("L3-COL-002")
def test_the_pool_never_exceeds_the_work_available() -> None:
    stub = _StubClient(missing=set())

    run(stub, REFERENCES[:1])

    assert stub.peak == 1


@pytest.mark.requirement("L3-COL-002")
def test_the_same_inventory_collects_identically_every_time() -> None:
    many = [RepositoryRef(owner="owner", repoid=f"repo{index:02d}") for index in range(16)]

    first = [outcome.reference.repoid for outcome in run(_StubClient(missing=set()), many)]
    second = [outcome.reference.repoid for outcome in run(_StubClient(missing=set()), many)]

    assert first == second


# ---------------------------------------------------------------------------
# The budget, checked before anything is spent
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-COL-003")
def test_a_run_that_fits_reports_what_it_will_cost() -> None:
    budget = check_budget(cast(GitHubClient, _StubClient(points=5000)), 400)

    assert budget == Budget(repositories=400, required=400 + RESERVE_POINTS, available=5000)
    assert budget.affordable is True
    assert budget.shortfall == 0


@pytest.mark.requirement("L3-COL-003")
def test_a_run_that_does_not_fit_is_refused_before_it_starts() -> None:
    """Refusing costs one free request and leaves the quota intact.

    Discovering exhaustion halfway produces a file that is part measurement
    and part absence, with nothing to tell the two apart.
    """
    with pytest.raises(RateLimitExhaustedError) as caught:
        check_budget(cast(GitHubClient, _StubClient(points=50)), 400)

    message = str(caught.value)
    assert "400 repositories need 410" in message
    assert "only 50 remain" in message
    assert "Short by 360" in message


@pytest.mark.requirement("L3-COL-003")
def test_the_reserve_keeps_a_token_from_reaching_exactly_zero() -> None:
    # A spent token makes the next command an operator tries look like a
    # broken tool rather than a finished budget.
    exact = _StubClient(points=100)

    check_budget(cast(GitHubClient, exact), 100 - RESERVE_POINTS)
    with pytest.raises(RateLimitExhaustedError):
        check_budget(cast(GitHubClient, exact), 100)


@pytest.mark.requirement("L3-COL-003")
def test_the_cost_is_one_point_per_repository() -> None:
    # Measured against the live API, and the reason a 400-row inventory fits.
    budget = check_budget(cast(GitHubClient, _StubClient()), 1)

    assert budget.required - RESERVE_POINTS == 1
