"""Tests for :mod:`github_metrics.analysis.popularity`."""

from __future__ import annotations

import logging

import pytest

from github_metrics.analysis.popularity import (
    FORK_BANDS,
    FORKS_POINTS,
    MAX_WEIGHT,
    STAR_BANDS,
    STARS_POINTS,
    describe_bands,
    forks_weight,
    score_forks,
    score_stars,
    stars_weight,
)

LOGGER_NAME = "github_metrics.analysis.popularity"


def original_forks(n: int):  # type: ignore[no-untyped-def]
    """The fork function as supplied, with no return statement.

    Transcribed exactly. It assigns a weight into a local and falls off the
    end, so it evaluates to `None` for every input.

    Every linter this project runs objects to it, which is the point: ruff and
    pylint both report `fork_weight` as an unused variable, because without a
    return that is exactly what it is. Both are silenced for this file - see
    the note in pyproject.toml - so the copy stays faithful. A tidied
    transcription would prove nothing about the code it stands in for.
    """
    # pylint: disable=unused-variable
    fork_weight: float = 0.0
    if n < 5:
        fork_weight = 0.0
    elif n < 10:
        fork_weight = 0.1
    elif n < 20:
        fork_weight = 0.2
    elif n < 30:
        fork_weight = 0.3
    elif n < 40:
        fork_weight = 0.4
    elif n < 50:
        fork_weight = 0.5
    elif n < 70:
        fork_weight = 0.6
    elif n < 90:
        fork_weight = 0.7
    elif n < 110:
        fork_weight = 0.8
    elif n >= 110:
        fork_weight = 0.1
    # no return


# ---------------------------------------------------------------------------
# The fork defects
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-SCR-016")
def test_the_original_fork_function_returned_none_for_every_input() -> None:
    """No return statement, so the fork score could never have been produced."""
    assert all(original_forks(n) is None for n in (0, 3, 50, 109, 110, 6900))

    with pytest.raises(TypeError):
        _ = FORKS_POINTS * original_forks(6900)


@pytest.mark.requirement("L3-SCR-016")
def test_more_forks_never_scores_lower_than_fewer() -> None:
    # The original's terminal branch assigned 0.1, so had the missing return
    # been added alone, 110 forks would have scored below 109 - more forks
    # meaning a lower score.
    weights = [forks_weight(n) for n in range(0, 400)]

    pairs = zip(weights[:-1], weights[1:], strict=True)
    assert all(earlier <= later for earlier, later in pairs)

    assert forks_weight(109) == 0.8
    assert forks_weight(110) == 0.9
    assert forks_weight(6900) == MAX_WEIGHT


@pytest.mark.requirement("L3-SCR-016")
def test_the_restored_band_is_nine_tenths_at_one_hundred_and_ten() -> None:
    # The original jumped from 0.8 straight to its terminal branch with no 0.9
    # anywhere. The band is restored, and full marks move to 150.
    assert dict(FORK_BANDS)[110] == 0.8
    assert dict(FORK_BANDS)[150] == 0.9
    assert forks_weight(150) == MAX_WEIGHT


# ---------------------------------------------------------------------------
# The two tables against each other
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-SCR-017")
def test_the_tables_agree_below_ninety() -> None:
    # The fork thresholds are read against the star thresholds they came from,
    # so the shared prefix is a property worth pinning.
    shared = [(bound, weight) for bound, weight in STAR_BANDS if bound <= 90]

    assert [(bound, weight) for bound, weight in FORK_BANDS if bound <= 90] == shared

    for count in range(0, 90):
        assert stars_weight(count) == forks_weight(count)


@pytest.mark.requirement("L3-SCR-017")
def test_forks_are_harder_to_max_than_stars_in_absolute_terms() -> None:
    # 150 forks against 300 stars. Forks are scarcer - a median fork-to-star
    # ratio of 0.186 across twelve sampled repositories - so the lower absolute
    # ceiling is still the stricter requirement.
    assert FORK_BANDS[-1][0] < STAR_BANDS[-1][0]

    assert stars_weight(200) == 0.9
    assert forks_weight(200) == MAX_WEIGHT


@pytest.mark.requirement("L3-SCR-017")
def test_every_threshold_is_shared_vocabulary() -> None:
    # 150 was chosen for the fork 0.9 band because the star table already used
    # it, rather than inventing a number appearing nowhere else.
    star_bounds = {bound for bound, _ in STAR_BANDS}
    fork_bounds = {bound for bound, _ in FORK_BANDS}

    assert fork_bounds - star_bounds == {110}


# ---------------------------------------------------------------------------
# Stars
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-SCR-018")
@pytest.mark.parametrize(
    ("stars", "expected"),
    [
        (0, 0.0),
        (4, 0.0),
        (5, 0.1),
        (9, 0.1),
        (10, 0.2),
        (19, 0.2),
        (20, 0.3),
        (29, 0.3),
        (30, 0.4),
        (39, 0.4),
        (40, 0.5),
        (49, 0.5),
        (50, 0.6),
        (69, 0.6),
        (70, 0.7),
        (89, 0.7),
        (90, 0.8),
        (149, 0.8),
        (150, 0.9),
        (299, 0.9),
        (300, 1.0),
        (64_574, 1.0),
    ],
)
def test_every_star_boundary_scores_as_documented(stars: int, expected: float) -> None:
    assert stars_weight(stars) == expected


@pytest.mark.requirement("L3-SCR-018")
def test_no_star_count_is_left_unmapped() -> None:
    produced = {stars_weight(n) for n in range(0, 400)}

    assert produced == {0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0}


@pytest.mark.requirement("L3-SCR-018")
def test_no_fork_count_is_left_unmapped() -> None:
    produced = {forks_weight(n) for n in range(0, 300)}

    assert produced == {0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0}


# ---------------------------------------------------------------------------
# Negative counts
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-SCR-019")
@pytest.mark.parametrize("weigh", [stars_weight, forks_weight])
def test_a_negative_count_is_reported_and_treated_as_zero(
    weigh: object, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        weight = weigh(-10)  # type: ignore[operator]

    assert weight == 0.0
    assert "negative" in caplog.text


# ---------------------------------------------------------------------------
# Points
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-SCR-020")
def test_the_budgets_are_ten_and_fifteen() -> None:
    assert STARS_POINTS == 10.0
    assert FORKS_POINTS == 15.0


@pytest.mark.requirement("L3-SCR-020")
def test_the_reference_row_is_reproduced() -> None:
    # 64,574 stars scoring 10.0 and 6,900 forks scoring 15.0.
    assert score_stars(64_574) == 10.0
    assert score_forks(6_900) == 15.0


@pytest.mark.requirement("L3-SCR-020")
def test_the_columns_are_the_budget_times_the_weight() -> None:
    for count in (0, 25, 95, 200, 5_000):
        assert score_stars(count) == STARS_POINTS * stars_weight(count)
        assert score_forks(count) == FORKS_POINTS * forks_weight(count)


@pytest.mark.requirement("L3-SCR-020")
def test_the_band_tables_render_side_by_side() -> None:
    rendered = describe_bands()

    assert "stars" in rendered
    assert "forks" in rendered
    assert "300" in rendered
