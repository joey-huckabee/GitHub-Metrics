"""Scoring how long a repository has existed.

`maturity_score` is 15 points multiplied by a 0.0-1.0 weight derived from
`age_days`. Older is better, up to a ceiling of four years.

The bands
---------
| Age | Weight | Points |
|-----|--------|--------|
| under 3 months (0.25 y) | 0.0 | 0.0 |
| 3 - 6 months | 0.2 | 3.0 |
| 6 months - 1 year | 0.4 | 6.0 |
| 1 - 2 years | 0.6 | 9.0 |
| 2 - 3 years | 0.8 | 12.0 |
| 3 - 4 years | 0.9 | 13.5 |
| 4 years or more | 1.0 | 15.0 |

The reference row carries an `age_days` of 736.5466017006597 - a little over
two years - and a `maturity_score` of 12.0, which the 2-to-3-year band
reproduces.

Corrections to the version this replaces
----------------------------------------
**A units mismatch in the first branch.** The chain opened with
`if age < 0.25`, comparing the age in **days** against a threshold meant to be
**years**; every later branch compared `age_in_years`. So the "too young to
score" band covered ages below 0.25 *days* - six hours - rather than below 0.25
*years*.

The effect was that every repository between six hours and three months old
scored 0.2 instead of 0.0. A brand-new repository created yesterday was
credited with three points of maturity. Fixed here, and the fix changes output
for exactly that range.

**A terminal boundary that decided nothing.** The chain ended
`age_in_years < 5 -> 1.0` followed by `age_in_years >= 5 -> 1.0`. Both produce
1.0, so the five-year edge could not change an answer; the progression had
already reached its maximum at four years. Unlike the equivalent case in
`last_update`, no weight had gone missing - this is a plateau, and it is now
written as one.

**A negative age scored zero, silently.** An age cannot be negative, but the
old first branch accepted one as "too young". It is now reported.
"""

from __future__ import annotations

import logging
from typing import Final

LOGGER = logging.getLogger(__name__)

DAYS_PER_YEAR: Final = 365.0
"""Leap years are ignored, as in the version this replaces.

The bands are measured in months and years, so a day either way cannot move a
repository between them except within a few hours of a boundary it was going to
cross anyway.
"""

MATURITY_BANDS: Final[tuple[tuple[float, float], ...]] = (
    (0.25, 0.0),  # under three months
    (0.5, 0.2),
    (1.0, 0.4),
    (2.0, 0.6),
    (3.0, 0.8),
    (4.0, 0.9),
)
"""Ordered `(exclusive upper bound in years, weight)` pairs.

The bounds are in **years**, and the input is in **days**, which is exactly the
mismatch that produced the defect this module fixes. The conversion happens
once, at the top of the function, and every comparison is against the converted
value - so there is no second unit for a later edit to get wrong.
"""

MATURE_WEIGHT: Final = 1.0
"""Weight for a repository at or beyond the last band."""

MATURITY_POINTS: Final = 15.0
"""Points a repository earns at full weight."""


def maturity_weight(age_days: float) -> float:
    """Score a repository's age as a 0.0-1.0 weight.

    Args:
        age_days: Days since the repository was created. A negative value
            cannot arise from a correct measurement and is reported and treated
            as zero.

    Returns:
        The weight, from 0.0 to `MATURE_WEIGHT`.

    Examples:
        >>> maturity_weight(30.0)
        0.0
        >>> maturity_weight(736.5466017006597)
        0.8
        >>> maturity_weight(2000.0)
        1.0
    """
    days = age_days

    if days < 0:
        LOGGER.warning(
            "Repository age is %s days, which is negative; scoring it as 0 days",
            days,
        )
        days = 0.0

    years = days / DAYS_PER_YEAR

    for upper_bound, weight in MATURITY_BANDS:
        if years < upper_bound:
            LOGGER.debug(
                "Age %.2f days (%.3f years) falls in band [<%s years] -> weight %s",
                days,
                years,
                upper_bound,
                weight,
            )
            return weight

    LOGGER.debug(
        "Age %.2f days (%.3f years) is at or beyond the last band (>=%s years) -> weight %s",
        days,
        years,
        MATURITY_BANDS[-1][0],
        MATURE_WEIGHT,
    )
    return MATURE_WEIGHT


def score_maturity(age_days: float) -> float:
    """Score the `maturity_score` column.

    Args:
        age_days: Days since the repository was created.

    Returns:
        The score, from 0.0 to `MATURITY_POINTS`.

    Examples:
        >>> score_maturity(736.5466017006597)
        12.0
    """
    score = MATURITY_POINTS * maturity_weight(age_days)
    LOGGER.debug(
        "Maturity score %s/%s from an age of %.2f days",
        score,
        MATURITY_POINTS,
        max(age_days, 0.0),
    )
    return score


def describe_bands() -> str:
    """Render the band table as text, for logs and diagnostics."""
    lines = [
        f"  <{bound:<4} years ({bound * DAYS_PER_YEAR:>7.1f} days) -> {weight}"
        for bound, weight in MATURITY_BANDS
    ]
    last = MATURITY_BANDS[-1][0]
    lines.append(f"  >={last:<3} years ({last * DAYS_PER_YEAR:>7.1f} days) -> {MATURE_WEIGHT}")
    return "maturity bands (on age in days):\n" + "\n".join(lines)
