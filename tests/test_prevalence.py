"""Tests for :mod:`github_metrics.analysis.prevalence`."""

from __future__ import annotations

import logging

import pytest

from github_metrics.analysis.closed_issues import score_closed_issues
from github_metrics.analysis.prevalence import (
    MAX_PREVALENCE_SCORE,
    PREVALENCE_POINTS,
    score_prevalence,
)
from github_metrics.analysis.releases import score_releases

LOGGER_NAME = "github_metrics.analysis.prevalence"


def original_rule(closed_issues: int, distinct_versions: int) -> float:
    """The rule this replaces, for comparison in tests.

    Selected on `closed_issues == 0`, using releases only as a fallback.
    """
    if closed_issues == 0:
        return PREVALENCE_POINTS * score_releases(distinct_versions)
    return PREVALENCE_POINTS * score_closed_issues(closed_issues)


# ---------------------------------------------------------------------------
# The cliff
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-SCR-005")
def test_the_original_cliff_at_one_closed_issue_is_gone() -> None:
    # The rule this replaces scored 20.0 with no closed issues and 2.0 with
    # one, a tenfold drop for closing a single issue.
    assert original_rule(0, 825) == 20.0
    assert original_rule(1, 825) == 2.0

    assert score_prevalence(0, 825) == 20.0
    assert score_prevalence(1, 825) == 20.0


@pytest.mark.requirement("L3-SCR-005")
def test_the_score_never_decreases_as_either_input_rises() -> None:
    # The property the original violated. Closing an issue or cutting a
    # release must never lower a project's score.
    for versions in (0, 3, 40, 100):
        scores = [score_prevalence(closed, versions) for closed in range(0, 600)]
        pairs = zip(scores[:-1], scores[1:], strict=True)
        assert all(a <= b for a, b in pairs), f"not monotone in closed issues at {versions}"

    for closed in (0, 3, 100, 600):
        scores = [score_prevalence(closed, versions) for versions in range(0, 120)]
        pairs = zip(scores[:-1], scores[1:], strict=True)
        assert all(a <= b for a, b in pairs), f"not monotone in versions at {closed}"


# ---------------------------------------------------------------------------
# Combination
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-SCR-005")
@pytest.mark.parametrize(
    ("closed", "versions", "expected"),
    [
        (0, 0, 0.0),  # nothing at all: no evidence, no score
        (1, 0, 2.0),  # one closed issue is evidence
        (0, 1, 2.0),  # one version only -> 0.1
        (3, 0, 2.0),  # a few closed issues -> 0.1
        (600, 0, 20.0),  # issues alone carry it
        (0, 100, 20.0),  # versions alone carry it
        (600, 100, 20.0),  # both
    ],
)
def test_the_stronger_signal_wins(closed: int, versions: int, expected: float) -> None:
    assert score_prevalence(closed, versions) == expected


@pytest.mark.requirement("L3-SCR-005")
def test_the_result_is_twenty_times_the_stronger_weight() -> None:
    closed, versions = 150, 30

    expected = PREVALENCE_POINTS * max(score_closed_issues(closed), score_releases(versions))

    assert score_prevalence(closed, versions) == expected


@pytest.mark.requirement("L3-SCR-005")
def test_the_score_stays_within_its_points_budget() -> None:
    for closed in (0, 1, 499, 500, 10_000):
        for versions in (0, 1, 79, 80, 5_000):
            assert 0.0 <= score_prevalence(closed, versions) <= MAX_PREVALENCE_SCORE


# ---------------------------------------------------------------------------
# A disabled issue tracker
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-SCR-006")
def test_a_disabled_tracker_excludes_the_issue_signal_rather_than_scoring_it() -> None:
    # A repository can accumulate closed issues and later have its tracker
    # switched off. The issues remain, but the signal is no longer one this
    # score is willing to read, so the release weight stands alone.
    assert score_prevalence(600, 0, issues_enabled=True) == 20.0
    assert score_prevalence(600, 0, issues_enabled=False) == 0.0


@pytest.mark.requirement("L3-SCR-006")
def test_a_disabled_tracker_still_scores_on_releases() -> None:
    assert score_prevalence(0, 943, issues_enabled=False) == 20.0


@pytest.mark.requirement("L3-SCR-006")
def test_a_disabled_tracker_is_reported(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        score_prevalence(0, 10, issues_enabled=False)

    assert "issue tracker disabled" in caplog.text


# ---------------------------------------------------------------------------
# The gate, and what it costs
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-SCR-007")
def test_every_measured_repository_reaches_the_maximum() -> None:
    # This is the gate behaviour, asserted so it is a recorded property rather
    # than a surprise: for mature projects the component is a constant and
    # contributes nothing to an ordering.
    measured = {
        "cline/cline": (3770, 717),
        "pypa/virtualenv": (1429, 285),
        "urllib3/urllib3": (1241, 108),
        "bokeh/bokeh": (7511, 151),
        "torvalds/linux": (0, 943),
    }

    for closed, versions in measured.values():
        assert score_prevalence(closed, versions) == MAX_PREVALENCE_SCORE


@pytest.mark.requirement("L3-SCR-007")
def test_it_still_discriminates_below_the_ceilings() -> None:
    # Where the gate earns its keep: young and unshipped projects.
    nothing_at_all = score_prevalence(0, 0)
    just_started = score_prevalence(25, 2)
    getting_going = score_prevalence(160, 25)
    established = score_prevalence(600, 100)

    assert nothing_at_all == 0.0
    assert nothing_at_all < just_started < getting_going < established
    assert established == MAX_PREVALENCE_SCORE


@pytest.mark.requirement("L3-SCR-007")
def test_saturation_is_logged_so_a_constant_score_is_explainable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        score_prevalence(3770, 717)

    assert "saturated" in caplog.text


@pytest.mark.requirement("L3-SCR-007")
def test_no_evidence_at_all_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        score_prevalence(0, 0)

    assert "no evidence" in caplog.text


@pytest.mark.requirement("L3-SCR-006")
def test_a_project_with_nothing_at_all_scores_zero() -> None:
    """No closed issues and no versions is no evidence, and scores nothing.

    The closed-issue band table floors at 0.1 for any count below 20, zero
    included. Weighing an empty tracker would therefore score such a project
    2.0 and place it above one with no evidence at all, which is backwards.
    Excluding an absent signal - whether absent because the tracker is off or
    because nothing has been closed - keeps the floor at zero, matching the
    rule this replaces.
    """
    assert original_rule(0, 0) == 0.0
    assert score_prevalence(0, 0) == 0.0
    assert score_prevalence(0, 0, issues_enabled=False) == 0.0

    # One closed issue, or one version, is evidence.
    assert score_prevalence(1, 0) == 2.0
    assert score_prevalence(0, 1) == 2.0


@pytest.mark.requirement("L3-SCR-007")
def test_the_stronger_signal_is_named_in_the_log(caplog: pytest.LogCaptureFixture) -> None:
    # Both signals must be present for one of them to be "stronger"; with no
    # closed issues the issue signal is excluded rather than compared.
    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        score_prevalence(5, 943)

    assert "versions is the stronger signal" in caplog.text


@pytest.mark.requirement("L3-SCR-006")
def test_an_absent_issue_signal_is_reported_rather_than_compared(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        score_prevalence(0, 943)

    assert "no closed issues" in caplog.text
    assert "stronger signal" not in caplog.text


@pytest.mark.requirement("L3-SCR-007")
def test_the_reference_row_is_reproduced() -> None:
    # The reference row carries prevalence_score 20.0 with closed_issues 0 and
    # releases 825. It reached that through the fallback branch because the
    # closed-issue count was broken. The new rule produces the same number for
    # the same inputs, so archived rows stay comparable.
    assert score_prevalence(0, 825) == 20.0
