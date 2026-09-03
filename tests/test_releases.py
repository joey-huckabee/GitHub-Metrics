"""Tests for release and tag collection."""

from __future__ import annotations

import logging
from typing import Any, cast

import pytest

from github_metrics.analysis.releases import (
    MAX_RELEASE_WEIGHT,
    SATURATION_COUNT,
)
from github_metrics.analysis.releases import describe_bands as describe_release_bands
from github_metrics.analysis.releases import score_releases
from github_metrics.client import GitHubClient
from github_metrics.collect.releases import ReleaseCounts, get_release_counts
from github_metrics.errors import RepositoryNotFoundError

COLLECT_LOGGER = "github_metrics.collect.releases"


class _StubClient:
    """Returns a canned GraphQL payload and records the query."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.queries: list[tuple[str, dict[str, Any]]] = []

    def graphql(
        self, query: str, variables: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Mimic `GitHubClient.graphql`."""
        self.queries.append((query, variables))
        return {}, self.payload


def payload(*, releases: int, tags: int) -> dict[str, Any]:
    """A successful repository response."""
    return {
        "data": {
            "repository": {
                "releases": {"totalCount": releases},
                "tags": {"totalCount": tags},
            }
        }
    }


def collect(stub: _StubClient, owner: str = "cline", repoid: str = "cline") -> ReleaseCounts:
    """Run collection against a stub client."""
    return get_release_counts(cast(GitHubClient, stub), owner, repoid)


# ---------------------------------------------------------------------------
# Counting
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-MET-005")
def test_releases_and_tags_are_collected_separately() -> None:
    counts = collect(_StubClient(payload(releases=398, tags=717)))

    assert counts.releases == 398
    assert counts.tags == 717


@pytest.mark.requirement("L3-MET-005")
def test_the_query_asks_for_totals_only_so_the_cost_stays_at_one_point() -> None:
    stub = _StubClient(payload(releases=1, tags=1))

    collect(stub)

    query, variables = stub.queries[0]
    assert "totalCount" in query
    assert "nodes" not in query
    assert 'refPrefix: "refs/tags/"' in query
    assert variables == {"owner": "cline", "name": "cline"}
    assert len(stub.queries) == 1


@pytest.mark.requirement("L3-MET-006")
@pytest.mark.parametrize(
    ("releases", "tags", "expected"),
    [
        (398, 717, 717),  # cline/cline
        (98, 285, 285),  # pypa/virtualenv
        (58, 108, 108),  # urllib3/urllib3
        (0, 151, 151),  # bokeh/bokeh - tags only
        (0, 943, 943),  # torvalds/linux - tags only
        (0, 0, 0),  # nothing shipped
        (5, 5, 5),  # every tag released
    ],
)
def test_distinct_versions_counts_each_version_once(
    releases: int, tags: int, expected: int
) -> None:
    counts = ReleaseCounts(releases=releases, tags=tags)

    # Every published release has a tag, so the tag count is already the union.
    assert counts.distinct_versions == expected
    assert counts.distinct_versions <= counts.legacy_sum


@pytest.mark.requirement("L3-MET-006")
def test_the_original_sum_double_counts_every_release() -> None:
    counts = ReleaseCounts(releases=398, tags=717)

    # 1115 against 717 distinct versions: every one of the 398 releases is
    # counted a second time through its tag.
    assert counts.legacy_sum == 1115
    assert counts.legacy_sum - counts.distinct_versions == counts.releases


@pytest.mark.requirement("L3-MET-006")
def test_the_inflation_is_uneven_across_publishing_styles() -> None:
    # This is why the sum is not merely a scaled version of the right answer.
    # A project that publishes releases is inflated; one that only tags is not.
    releases_heavy = ReleaseCounts(releases=398, tags=717)
    tags_only = ReleaseCounts(releases=0, tags=943)

    assert releases_heavy.legacy_sum / releases_heavy.distinct_versions == pytest.approx(
        1.555, abs=0.01
    )
    assert tags_only.legacy_sum / tags_only.distinct_versions == 1.0


@pytest.mark.requirement("L3-MET-006")
def test_more_releases_than_tags_never_under_counts() -> None:
    # Only reachable for a token that can see draft releases naming tags that
    # do not exist yet. Taking the larger keeps the count a lower bound on the
    # union rather than silently dropping the drafts.
    counts = ReleaseCounts(releases=7, tags=3)

    assert counts.distinct_versions == 7


@pytest.mark.requirement("L3-MET-005")
def test_tags_without_releases_never_goes_negative() -> None:
    assert ReleaseCounts(releases=9, tags=3).tags_without_releases == 0
    assert ReleaseCounts(releases=3, tags=9).tags_without_releases == 6


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-MET-007")
def test_the_counts_and_their_relationship_are_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG, logger=COLLECT_LOGGER):
        collect(_StubClient(payload(releases=398, tags=717)))

    assert "398 releases, 717 tags, 717 distinct versions" in caplog.text
    # The relationship is the useful part: 398 alone does not say whether that
    # is most of the tags or a handful.
    assert "319 tags without one" in caplog.text


@pytest.mark.requirement("L3-MET-007")
def test_a_tags_only_project_is_called_out(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger=COLLECT_LOGGER):
        collect(_StubClient(payload(releases=0, tags=943)), "torvalds", "linux")

    assert "publishes no GitHub Releases" in caplog.text


@pytest.mark.requirement("L3-MET-007")
def test_a_project_with_nothing_shipped_is_called_out(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG, logger=COLLECT_LOGGER):
        collect(_StubClient(payload(releases=0, tags=0)))

    assert "no versioned artifact" in caplog.text


@pytest.mark.requirement("L3-MET-007")
def test_drafts_making_the_count_irreproducible_are_warned_about(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger=COLLECT_LOGGER):
        collect(_StubClient(payload(releases=7, tags=3)))

    assert "not reproducible by another user" in caplog.text


@pytest.mark.requirement("L3-MET-007")
def test_the_previous_definition_is_reported_so_a_changed_score_is_explainable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG, logger=COLLECT_LOGGER):
        collect(_StubClient(payload(releases=398, tags=717)))

    assert "would report 1115" in caplog.text
    assert "1.56x" in caplog.text


@pytest.mark.requirement("L3-MET-005")
def test_a_null_repository_fails_rather_than_raising_a_key_error() -> None:
    with pytest.raises(RepositoryNotFoundError):
        collect(_StubClient({"data": {"repository": None}}))


# ---------------------------------------------------------------------------
# Scoring bands
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-SCR-003")
@pytest.mark.parametrize(
    ("versions", "expected"),
    [
        (0, 0.0),
        (1, 0.1),
        (4, 0.1),
        (5, 0.2),
        (9, 0.2),
        (10, 0.3),
        (19, 0.3),
        (20, 0.4),
        (39, 0.4),
        (40, 0.5),
        (49, 0.5),
        (50, 0.6),
        (59, 0.6),
        (60, 0.7),
        (69, 0.7),
        (70, 0.8),
        (79, 0.8),
        (80, 1.0),
        (943, 1.0),
    ],
)
def test_every_release_band_boundary_scores_as_documented(versions: int, expected: float) -> None:
    assert score_releases(versions) == expected


@pytest.mark.requirement("L3-SCR-003")
def test_zero_versions_scores_zero_not_the_floor_closed_issues_uses() -> None:
    # Deliberately different from closed issues, where zero scores 0.1. A
    # project that has never cut a version is treated as having no evidence.
    assert score_releases(0) == 0.0


@pytest.mark.requirement("L3-SCR-003")
def test_no_version_count_is_left_unmapped() -> None:
    weights = {score_releases(n) for n in range(0, SATURATION_COUNT + 50)}

    assert weights == {0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0}


@pytest.mark.requirement("L3-SCR-003")
def test_the_table_never_produces_nine_tenths() -> None:
    # The step from 0.8 to 1.0 is double every other step. Asserted so the
    # asymmetry is a recorded decision rather than something a later reader
    # "fixes" without knowing it was intended.
    assert 0.9 not in {score_releases(n) for n in range(0, 500)}


@pytest.mark.requirement("L3-SCR-003")
def test_the_release_score_never_decreases_as_versions_rise() -> None:
    scores = [score_releases(n) for n in range(0, 200)]

    pairs = zip(scores[:-1], scores[1:], strict=True)
    assert all(earlier <= later for earlier, later in pairs)


@pytest.mark.requirement("L3-SCR-003")
def test_it_saturates_at_the_documented_count() -> None:
    assert score_releases(SATURATION_COUNT - 1) < MAX_RELEASE_WEIGHT
    assert score_releases(SATURATION_COUNT) == MAX_RELEASE_WEIGHT


@pytest.mark.requirement("L3-SCR-003")
def test_every_measured_repository_saturates() -> None:
    # The reason this matters is recorded in docs/METRICS.md: if both signals
    # feeding prevalence_score max out on real projects, that component reports
    # a constant and contributes nothing to a ranking.
    measured = {
        "cline/cline": 717,
        "pypa/virtualenv": 285,
        "urllib3/urllib3": 108,
        "bokeh/bokeh": 151,
        "torvalds/linux": 943,
    }

    assert all(score_releases(v) == MAX_RELEASE_WEIGHT for v in measured.values())


@pytest.mark.requirement("L3-SCR-004")
def test_a_negative_version_count_is_reported_and_treated_as_zero(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="github_metrics.analysis.releases"):
        weight = score_releases(-3)

    assert weight == 0.0
    assert "negative" in caplog.text


@pytest.mark.requirement("L3-SCR-004")
def test_release_scoring_logs_the_band_it_chose(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger="github_metrics.analysis.releases"):
        score_releases(45)

    assert "45" in caplog.text
    assert "0.5" in caplog.text


@pytest.mark.requirement("L3-SCR-003")
def test_the_release_band_table_renders_for_diagnostics() -> None:
    rendered = describe_release_bands()

    assert "<80" in rendered
    assert ">=80" in rendered
