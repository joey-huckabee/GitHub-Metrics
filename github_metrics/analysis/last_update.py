"""Scoring how recently a repository was updated.

`last_update_score` is 15 points multiplied by a 0.0-1.0 weight derived from
how many hours have passed since the repository was last updated. Unlike the
other tables, a **smaller** input scores higher: recency is the thing being
rewarded.

The bands
---------
| Hours since update | In years | Weight |
|--------------------|----------|--------|
| 0 - 876            | <= 0.1   | 1.0    |
| 877 - 2,190        | <= 0.25  | 0.8    |
| 2,191 - 4,380      | <= 0.5   | 0.6    |
| 4,381 - 8,760      | <= 1     | 0.4    |
| 8,761 - 26,280     | <= 3     | 0.2    |
| more than 26,280   | > 3      | 0.0    |

Roughly: touched in the last five weeks earns full marks, untouched for three
years earns nothing.

Corrections to the version this replaces
----------------------------------------
The bands are unchanged - every input scores exactly what it scored before,
which a test asserts by sweeping the whole range against a copy of the original
chain. Three things around them are fixed:

1. **A boundary that did nothing.** The chain ended
   `> 0.05 year -> 1` followed by `<= 0.05 year -> 1`, so both sides of that
   edge produced the same weight. Sweeping 0 to 40,000 hours found **no input**
   where removing the branch changed the answer. It is dropped rather than
   preserved, because a boundary that decides nothing reads as though it does.
2. **A negative input scored full marks.** Hours since the last update cannot
   be negative, but a clock skew between GitHub and the local machine can
   produce one, and `<= 0.05 year` accepted it silently as "just updated". It
   is now reported and treated as zero.
3. **The table is data rather than a chain**, matching the other scoring
   modules, so no input can fall between branches.
"""

from __future__ import annotations

import logging
from typing import Final

LOGGER = logging.getLogger(__name__)

HOURS_PER_YEAR: Final = 24 * 365
"""8,760. A calendar year is ignored here; the bands are far coarser than a day."""

LAST_UPDATE_BANDS: Final[tuple[tuple[int, float], ...]] = (
    (876, 1.0),  # 0.1 year, about five weeks
    (2_190, 0.8),  # 0.25 year
    (4_380, 0.6),  # 0.5 year
    (8_760, 0.4),  # 1 year
    (26_280, 0.2),  # 3 years
)
"""Ordered `(inclusive upper bound in hours, weight)` pairs.

The first band whose bound is not exceeded wins, which is the same rule the
other tables use - only the direction of merit is reversed, because a smaller
number of hours is better.

The bounds are written as exact integers rather than as `0.1 * HOURS_PER_YEAR`
because that product is not exactly 876 in binary floating point, and a band
edge that lands a fraction either side of an integer is the kind of thing that
makes one repository in a thousand score differently for no visible reason.
"""

STALE_WEIGHT: Final = 0.0
"""Weight for a repository untouched for longer than the last band."""

LAST_UPDATE_POINTS: Final = 15.0
"""Points a repository earns at full weight."""


def last_update_weight(last_update_hours: float) -> float:
    """Score recency as a 0.0-1.0 weight.

    Args:
        last_update_hours: Hours since the repository was last updated. A
            negative value cannot occur from a correct measurement but can
            arise from clock skew; it is reported and treated as zero rather
            than silently scoring as "just updated".

    Returns:
        The weight, from `STALE_WEIGHT` to 1.0.

    Examples:
        >>> last_update_weight(8.1)
        1.0
        >>> last_update_weight(9000.0)
        0.2
        >>> last_update_weight(30000.0)
        0.0
    """
    hours = last_update_hours

    if hours < 0:
        LOGGER.warning(
            "Hours since last update is %s, which is negative; this suggests clock "
            "skew between GitHub and this machine. Scoring it as 0 hours.",
            hours,
        )
        hours = 0.0

    for upper_bound, weight in LAST_UPDATE_BANDS:
        if hours <= upper_bound:
            LOGGER.debug(
                "Last update %.2f hours ago (%.2f years) falls in band [<=%d] -> weight %s",
                hours,
                hours / HOURS_PER_YEAR,
                upper_bound,
                weight,
            )
            return weight

    LOGGER.debug(
        "Last update %.2f hours ago (%.2f years) is beyond the last band (>%d) -> weight %s",
        hours,
        hours / HOURS_PER_YEAR,
        LAST_UPDATE_BANDS[-1][0],
        STALE_WEIGHT,
    )
    return STALE_WEIGHT


def score_last_update(last_update_hours: float) -> float:
    """Score the `last_update_score` column.

    Args:
        last_update_hours: Hours since the repository was last updated.

    Returns:
        The score, from 0.0 to `LAST_UPDATE_POINTS`.

    Examples:
        >>> score_last_update(8.1)
        15.0
        >>> score_last_update(30000.0)
        0.0
    """
    score = LAST_UPDATE_POINTS * last_update_weight(last_update_hours)
    LOGGER.info(
        "Last-update score %s/%s from %.2f hours since the last update",
        score,
        LAST_UPDATE_POINTS,
        max(last_update_hours, 0.0),
    )
    return score


def describe_bands() -> str:
    """Render the band table as text, for logs and diagnostics.

    Returns:
        One line per band, in evaluation order.
    """
    lines = [
        f"  <={bound:<6} hours ({bound / HOURS_PER_YEAR:>5.2f} years) -> {weight}"
        for bound, weight in LAST_UPDATE_BANDS
    ]
    last = LAST_UPDATE_BANDS[-1][0]
    lines.append(f"  > {last:<6} hours ({last / HOURS_PER_YEAR:>5.2f} years) -> {STALE_WEIGHT}")
    return "last-update bands (on hours since the last update):\n" + "\n".join(lines)
