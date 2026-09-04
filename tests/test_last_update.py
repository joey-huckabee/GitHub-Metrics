"""Tests for :mod:`github_metrics.analysis.last_update`."""

from __future__ import annotations

import itertools
import logging

import pytest

from github_metrics.analysis.last_update import (
    HOURS_PER_YEAR,
    LAST_UPDATE_BANDS,
    LAST_UPDATE_POINTS,
    STALE_WEIGHT,
    describe_bands,
    last_update_weight,
    score_last_update,
)

LOGGER_NAME = "github_metrics.analysis.last_update"


def original_chain(updates: float) -> float:
    """The version this replaces, transcribed for comparison.

    Kept verbatim - including the final branch that duplicates the one before
    it - so the equivalence test compares against what was actually there
    rather than against a tidied memory of it.
    """
    hours_per_year = float(24 * 365)
    update_weight = 0.0
    if updates > 3 * hours_per_year:
        update_weight = 0
    elif updates > 1 * hours_per_year:
        update_weight = 0.2
    elif updates > 0.5 * hours_per_year:
        update_weight = 0.4
    elif updates > 0.25 * hours_per_year:
        update_weight = 0.6
    elif updates > 0.1 * hours_per_year:
        update_weight = 0.8
    # ruff flags these two branches as combinable, which is the same
    # redundancy the replacement removes - it is preserved here on purpose,
    # because an equivalence test against a tidied copy proves nothing.
    elif updates > 0.05 * hours_per_year:  # noqa: SIM114
        update_weight = 1
    elif updates <= 0.05 * hours_per_year:
        update_weight = 1
    return update_weight


# ---------------------------------------------------------------------------
# The bands are unchanged
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-SCR-008")
def test_only_the_restored_band_differs_from_the_original() -> None:
    """Sweep the whole meaningful range against the original chain.

    Exactly one band was meant to change: the `0.05 year` band, whose weight
    had been lost and is restored to 0.9. Everything else must score what it
    scored before. Naming the differing inputs precisely - rather than
    asserting "no mismatches" or not checking at all - is what makes the change
    surgical rather than merely intended.
    """
    differing = [
        hours
        for hours in range(5 * HOURS_PER_YEAR)
        if last_update_weight(float(hours)) != original_chain(float(hours))
    ]

    # 439 to 876 inclusive: more than 0.05 of a year, up to 0.1 of a year.
    assert differing == list(range(439, 877))
    assert all(last_update_weight(float(h)) == 0.9 for h in differing)
    assert all(original_chain(float(h)) == 1.0 for h in differing)


@pytest.mark.requirement("L3-SCR-008")
@pytest.mark.parametrize(
    ("hours", "expected"),
    [
        (0, 1.0),
        (8.10177526, 1.0),  # the reference row
        (437, 1.0),
        (438, 1.0),  # 0.05 year, inclusive
        (439, 0.9),  # the restored band
        (875, 0.9),
        (876, 0.9),  # 0.1 year, inclusive
        (877, 0.8),
        (2_190, 0.8),  # 0.25 year, inclusive
        (2_191, 0.6),
        (4_380, 0.6),  # 0.5 year, inclusive
        (4_381, 0.4),
        (8_760, 0.4),  # 1 year, inclusive
        (8_761, 0.2),
        (26_280, 0.2),  # 3 years, inclusive
        (26_281, 0.0),
        (100_000, 0.0),
    ],
)
def test_every_band_boundary_scores_as_documented(hours: float, expected: float) -> None:
    assert last_update_weight(hours) == expected


@pytest.mark.requirement("L3-SCR-008")
def test_the_score_never_rises_as_a_repository_goes_stale() -> None:
    # Merit runs the opposite way to the other tables: more hours is worse.
    weights = [last_update_weight(float(h)) for h in range(0, 30_000, 7)]

    pairs = itertools.pairwise(weights)
    assert all(earlier >= later for earlier, later in pairs)


@pytest.mark.requirement("L3-SCR-008")
def test_no_input_is_left_unmapped() -> None:
    produced = {last_update_weight(float(h)) for h in range(0, 30_000, 3)}

    assert produced == {1.0, 0.9, 0.8, 0.6, 0.4, 0.2, 0.0}


# ---------------------------------------------------------------------------
# The boundary that decided nothing
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-SCR-009")
def test_the_restored_boundary_now_decides_something() -> None:
    """The `0.05 year` edge had the same weight on both sides and now does not.

    In the version this replaces both branches produced 1.0, so the boundary
    could not change an answer - it was a band that had lost its weight rather
    than a deliberate plateau. With 0.9 restored the edge separates two
    weights, which is what makes it a boundary.
    """
    edge = 0.05 * HOURS_PER_YEAR

    assert original_chain(edge - 1) == original_chain(edge + 1) == 1.0

    assert last_update_weight(edge - 1) == 1.0
    assert last_update_weight(edge + 1) == 0.9


@pytest.mark.requirement("L3-SCR-009")
def test_the_table_has_no_redundant_edges_left() -> None:
    # Every remaining boundary separates two different weights, which is what
    # makes it a boundary rather than a decoration.
    weights = [weight for _, weight in LAST_UPDATE_BANDS] + [STALE_WEIGHT]

    assert len(set(weights)) == len(weights)


@pytest.mark.requirement("L3-SCR-009")
def test_the_bounds_are_exact_integers() -> None:
    # 0.05 * 8760 is not exactly 438 in binary floating point, nor 0.1 * 8760
    # exactly 876. Writing the bounds as integers keeps a band edge from landing
    # a fraction either side of where it reads.
    assert [bound for bound, _ in LAST_UPDATE_BANDS] == [
        438,
        876,
        2_190,
        4_380,
        8_760,
        26_280,
    ]
    assert all(isinstance(bound, int) for bound, _ in LAST_UPDATE_BANDS)


# ---------------------------------------------------------------------------
# Clock skew
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-SCR-010")
def test_a_negative_input_is_reported_rather_than_scored_as_fresh(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The original accepted a negative value through `<= 0.05 year` and scored
    # it 1.0, so a clock skew read as "just updated".
    assert original_chain(-5.0) == 1

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        weight = last_update_weight(-5.0)

    assert weight == 1.0  # still the freshest band, but no longer silently
    assert "clock skew" in caplog.text


# ---------------------------------------------------------------------------
# Points
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-SCR-011")
def test_the_column_is_fifteen_points_times_the_weight() -> None:
    assert LAST_UPDATE_POINTS == 15.0

    for hours in (0.0, 900.0, 5_000.0, 30_000.0):
        assert score_last_update(hours) == LAST_UPDATE_POINTS * last_update_weight(hours)


@pytest.mark.requirement("L3-SCR-011")
def test_the_reference_row_is_reproduced() -> None:
    # last_update_hours 8.10177526 with last_update_score 15.
    assert score_last_update(8.10177526) == 15.0


@pytest.mark.requirement("L3-SCR-011")
def test_a_long_abandoned_repository_scores_nothing() -> None:
    assert score_last_update(4 * HOURS_PER_YEAR) == 0.0


@pytest.mark.requirement("L3-SCR-011")
def test_the_score_is_logged_with_its_budget(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        score_last_update(9_000.0)

    assert "3.0/15.0" in caplog.text


@pytest.mark.requirement("L3-SCR-008")
def test_the_band_table_renders_for_diagnostics() -> None:
    rendered = describe_bands()

    assert "876" in rendered
    assert "26280" in rendered
    assert "years" in rendered
