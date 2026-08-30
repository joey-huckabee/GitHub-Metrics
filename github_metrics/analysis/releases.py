"""Scoring the release count.

A pure function of one integer, like `closed_issues`. Collection lives in
`github_metrics.collect.releases`.

What is being scored
--------------------
**Distinct versions**, which is the tag count - not `releases + tags`. Every
published GitHub Release requires a tag, so summing them counts each release
twice, unevenly. See `docs/METRICS.md`.

The bands
---------
| Distinct versions | Weight |
|-------------------|--------|
| 0                 | 0.0    |
| 1 - 4             | 0.1    |
| 5 - 9             | 0.2    |
| 10 - 19           | 0.3    |
| 20 - 39           | 0.4    |
| 40 - 49           | 0.5    |
| 50 - 59           | 0.6    |
| 60 - 69           | 0.7    |
| 70 - 79           | 0.8    |
| 80 or more        | 1.0    |

Two things to know about this table, both carried over from the original
deliberately rather than by oversight:

- **Zero scores 0.0**, unlike closed issues where zero scores 0.1. A project
  that has never cut a version is treated as having no evidence at all, where
  a project with no closed issues still gets a floor.
- **0.9 is skipped.** The step from 0.8 to 1.0 is double every other step.

It saturates early
------------------
The top band starts at 80. Every established project clears it - measured
across five, the smallest had 108 distinct versions and the largest 943. In
practice this weight is 1.0 for anything mature, and the table only
discriminates among projects that have shipped fewer than 80 versions. What
that means for `prevalence_score`, which combines this with the closed-issue
weight, is recorded in `docs/METRICS.md`.
"""

from __future__ import annotations

import logging
from typing import Final

LOGGER = logging.getLogger(__name__)

RELEASE_BANDS: Final[tuple[tuple[int, float], ...]] = (
    (1, 0.0),
    (5, 0.1),
    (10, 0.2),
    (20, 0.3),
    (40, 0.4),
    (50, 0.5),
    (60, 0.6),
    (70, 0.7),
    (80, 0.8),
)
"""Ordered `(exclusive upper bound, weight)` pairs.

The first band whose bound exceeds the count wins. The leading `(1, 0.0)` is
how "zero scores zero" is expressed as data rather than as a special case
ahead of the loop - a count of 0 is the only value below 1.
"""

MAX_RELEASE_WEIGHT: Final = 1.0
"""Weight for a count at or above the last band's bound."""

SATURATION_COUNT: Final = 80
"""Distinct versions at which the weight reaches its maximum.

Named because it is the number that decides whether this metric discriminates
between projects or reports a constant for all of them.
"""


def score_releases(repository_releases_total_count: int) -> float:
    """Score a distinct-version count as a 0.0-1.0 weight.

    Args:
        repository_releases_total_count: Distinct versions, which is the tag
            count. A negative value is treated as zero and logged, since a
            count cannot be negative and scoring it silently would hide the
            caller's bug.

    Returns:
        The weight for this count, from 0.0 to `MAX_RELEASE_WEIGHT`.

    Examples:
        >>> score_releases(0)
        0.0
        >>> score_releases(79)
        0.8
        >>> score_releases(80)
        1.0
    """
    count = repository_releases_total_count

    if count < 0:
        LOGGER.warning(
            "Release count %d is negative, which cannot happen; scoring it as 0",
            count,
        )
        count = 0

    for upper_bound, weight in RELEASE_BANDS:
        if count < upper_bound:
            LOGGER.debug(
                "Distinct versions %d falls in band [<%d) -> weight %s",
                count,
                upper_bound,
                weight,
            )
            return weight

    LOGGER.debug(
        "Distinct versions %d is at or above the top band (>=%d) -> weight %s",
        count,
        SATURATION_COUNT,
        MAX_RELEASE_WEIGHT,
    )
    return MAX_RELEASE_WEIGHT


def describe_bands() -> str:
    """Render the band table as text, for logs and diagnostics.

    Returns:
        One line per band, in evaluation order.
    """
    lines = [f"  <{bound:<5} -> {weight}" for bound, weight in RELEASE_BANDS]
    lines.append(f"  >={SATURATION_COUNT:<4} -> {MAX_RELEASE_WEIGHT}")
    return "release bands (on distinct versions):\n" + "\n".join(lines)
