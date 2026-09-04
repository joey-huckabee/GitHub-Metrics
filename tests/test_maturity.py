"""Tests for :mod:`github_metrics.analysis.maturity`."""

from __future__ import annotations

import itertools
import logging

import pytest

from github_metrics.analysis.maturity import (
    DAYS_PER_YEAR,
    MATURE_WEIGHT,
    MATURITY_BANDS,
    MATURITY_POINTS,
    describe_bands,
    maturity_weight,
    score_maturity,
)

LOGGER_NAME = "github_metrics.analysis.maturity"

REFERENCE_AGE_DAYS = 736.5466017006597


def original_chain(age: float) -> float:
    """The version this replaces, transcribed verbatim.

    The first branch compares `age` - in days - against a threshold the rest of
    the chain applies to years. That mismatch is the defect, and it is kept
    here so the tests compare against what was actually there.
    """
    age_in_years = age / 365
    age_weight = 0.0
    if age < 0.25:
        age_weight = 0
    elif age_in_years < 0.5:
        age_weight = 0.2
    elif age_in_years < 1:
        age_weight = 0.4
    elif age_in_years < 2:
        age_weight = 0.6
    elif age_in_years < 3:
        age_weight = 0.8
    elif age_in_years < 4:
        age_weight = 0.9
    elif age_in_years < 5:  # noqa: SIM114 - duplicated branch preserved on purpose
        age_weight = 1.0
    elif age_in_years >= 5:
        age_weight = 1.0
    return age_weight


# ---------------------------------------------------------------------------
# The units defect
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-SCR-012")
def test_a_young_repository_no_longer_scores_as_though_it_were_months_old() -> None:
    """The defect: `age < 0.25` compared days against a year threshold.

    Only repositories under six hours old landed in the "too young" band, so
    everything from six hours to three months was credited 0.2 - three points
    of maturity for a repository created yesterday.
    """
    one_day = 1.0
    one_month = 30.0
    just_under_three_months = 0.25 * DAYS_PER_YEAR - 1

    for age in (one_day, one_month, just_under_three_months):
        assert original_chain(age) == 0.2
        assert maturity_weight(age) == 0.0


@pytest.mark.requirement("L3-SCR-012")
def test_the_correction_applies_to_exactly_the_affected_range() -> None:
    # Six hours to three months. Outside it, nothing changes.
    differing = [
        days
        for days in range(6 * int(DAYS_PER_YEAR))
        if maturity_weight(float(days)) != original_chain(float(days))
    ]

    assert differing == list(range(1, 92))
    assert all(maturity_weight(float(d)) == 0.0 for d in differing)
    assert all(original_chain(float(d)) == 0.2 for d in differing)


@pytest.mark.requirement("L3-SCR-012")
def test_the_conversion_happens_once() -> None:
    # Every comparison is against years; there is no second unit in play. The
    # three-month boundary is where it should be.
    assert maturity_weight(0.25 * DAYS_PER_YEAR - 0.001) == 0.0
    assert maturity_weight(0.25 * DAYS_PER_YEAR) == 0.2


# ---------------------------------------------------------------------------
# The bands
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-SCR-013")
@pytest.mark.parametrize(
    ("years", "expected"),
    [
        (0.0, 0.0),
        (0.24, 0.0),
        (0.25, 0.2),
        (0.49, 0.2),
        (0.5, 0.4),
        (0.99, 0.4),
        (1.0, 0.6),
        (1.99, 0.6),
        (2.0, 0.8),
        (2.99, 0.8),
        (3.0, 0.9),
        (3.99, 0.9),
        (4.0, 1.0),
        (10.0, 1.0),
    ],
)
def test_every_band_boundary_scores_as_documented(years: float, expected: float) -> None:
    assert maturity_weight(years * DAYS_PER_YEAR) == expected


@pytest.mark.requirement("L3-SCR-013")
def test_the_score_never_decreases_as_a_repository_ages() -> None:
    weights = [maturity_weight(float(d)) for d in range(0, 3000, 3)]

    pairs = itertools.pairwise(weights)
    assert all(earlier <= later for earlier, later in pairs)


@pytest.mark.requirement("L3-SCR-013")
def test_no_age_is_left_unmapped() -> None:
    produced = {maturity_weight(float(d)) for d in range(3000)}

    assert produced == {0.0, 0.2, 0.4, 0.6, 0.8, 0.9, 1.0}


@pytest.mark.requirement("L3-SCR-013")
def test_the_five_year_boundary_decided_nothing_and_is_gone() -> None:
    # The chain ended `< 5 -> 1.0` then `>= 5 -> 1.0`. Unlike the equivalent
    # case in last_update, no weight had gone missing - the progression had
    # already reached its maximum at four years, so this is a plateau and is
    # now written as one.
    assert original_chain(4.5 * DAYS_PER_YEAR) == original_chain(5.5 * DAYS_PER_YEAR) == 1.0

    assert MATURITY_BANDS[-1][0] == 4.0
    assert maturity_weight(4.5 * DAYS_PER_YEAR) == maturity_weight(5.5 * DAYS_PER_YEAR) == 1.0


@pytest.mark.requirement("L3-SCR-013")
def test_every_remaining_boundary_separates_two_weights() -> None:
    weights = [weight for _, weight in MATURITY_BANDS] + [MATURE_WEIGHT]

    assert len(set(weights)) == len(weights)


# ---------------------------------------------------------------------------
# Negative age
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-SCR-014")
def test_a_negative_age_is_reported_rather_than_silently_scored(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The old first branch accepted a negative age as "too young" and returned
    # 0 without comment.
    assert original_chain(-100.0) == 0

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        weight = maturity_weight(-100.0)

    assert weight == 0.0
    assert "negative" in caplog.text


# ---------------------------------------------------------------------------
# Points
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-SCR-015")
def test_the_column_is_fifteen_points_times_the_weight() -> None:
    assert MATURITY_POINTS == 15.0

    for days in (0.0, 200.0, 800.0, 2000.0):
        assert score_maturity(days) == MATURITY_POINTS * maturity_weight(days)


@pytest.mark.requirement("L3-SCR-015")
def test_the_reference_row_is_reproduced() -> None:
    # age_days 736.5466017006597, a little over two years, scoring 12.0.
    assert score_maturity(REFERENCE_AGE_DAYS) == 12.0


@pytest.mark.requirement("L3-SCR-015")
def test_the_band_table_renders_for_diagnostics() -> None:
    rendered = describe_bands()

    assert "0.25" in rendered
    assert "years" in rendered
