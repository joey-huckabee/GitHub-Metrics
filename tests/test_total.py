"""Tests for :mod:`github_metrics.analysis.total`."""

from __future__ import annotations

import logging

import pytest

from github_metrics.analysis.total import COMPONENT_POINTS, MAX_TOTAL_SCORE, score_total

LOGGER_NAME = "github_metrics.analysis.total"

PERFECT = (20.0, 10.0, 15.0, 15.0, 15.0, 10.0)
REFERENCE = (20.0, 10.0, 15.0, 12.0, 15.0, 0.0)


@pytest.mark.requirement("L3-SCR-021")
def test_the_ceiling_is_eighty_five() -> None:
    assert MAX_TOTAL_SCORE == 85.0


@pytest.mark.requirement("L3-SCR-021")
def test_the_ceiling_is_derived_from_the_components_not_typed() -> None:
    # If a component's weight is ever changed, the ceiling moves with it and
    # the assertion above is what says so. A clamp would have stayed at 85.0
    # and reported nothing.
    assert sum(points for _, points in COMPONENT_POINTS) == MAX_TOTAL_SCORE
    assert [points for _, points in COMPONENT_POINTS] == [20.0, 10.0, 15.0, 15.0, 15.0, 10.0]


@pytest.mark.requirement("L3-SCR-021")
def test_the_five_scored_components_sum_to_seventy_five_without_the_bonus() -> None:
    scored = [points for name, points in COMPONENT_POINTS if name != "trusted_org_bonus"]

    assert sum(scored) == 75.0


@pytest.mark.requirement("L3-SCR-021")
def test_a_perfect_repository_reaches_the_ceiling() -> None:
    assert score_total(*PERFECT) == MAX_TOTAL_SCORE


@pytest.mark.requirement("L3-SCR-021")
def test_the_reference_row_is_reproduced() -> None:
    # The worked example in docs/METRICS.md: everything but maturity at full
    # marks, no trusted-organisation bonus.
    assert score_total(*REFERENCE) == 72.0


@pytest.mark.requirement("L3-SCR-021")
def test_a_project_with_nothing_scores_nothing() -> None:
    assert score_total(0.0, 0.0, 0.0, 0.0, 0.0, 0.0) == 0.0


@pytest.mark.requirement("L3-SCR-021")
def test_every_component_is_a_float() -> None:
    assert isinstance(score_total(*REFERENCE), float)
    assert all(isinstance(points, float) for _, points in COMPONENT_POINTS)


@pytest.mark.requirement("L3-SCR-021")
def test_exceeding_the_ceiling_is_reported_rather_than_clamped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A total over 85 means a component is over its share.

    Clamping would return 85.0 and say nothing, which is the failure mode this
    whole scoring model has been corrected for three times already.
    """
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        total = score_total(25.0, 10.0, 15.0, 15.0, 15.0, 10.0)

    assert total == 90.0
    assert "exceeds the maximum" in caplog.text
    assert "prevalence_score=25.0" in caplog.text
    # Only the component that overshot is named.
    assert "stars_score" not in caplog.text


@pytest.mark.requirement("L3-SCR-021")
def test_a_normal_total_says_nothing_at_info(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="github_metrics"):
        score_total(*REFERENCE)

    assert caplog.records == []
