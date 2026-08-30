"""Scoring the closed-issue count.

A pure function of one integer. It performs no I/O, which is what lets the
bands be tested exhaustively — including every boundary — without a network, a
token, or a mock. Collection lives in `github_metrics.collect.closed_issues`.

The bands
---------
Closed issues stand in for sustained maintenance: a project that closes issues
is a project someone is answering. The band edges are not evenly spaced,
because the informative range is at the low end. The difference between 10 and
100 closed issues says a great deal about whether a project is maintained; the
difference between 3,000 and 4,000 says almost nothing, because both are
projects with years of answered issues behind them.

| Closed issues | Weight |
|---------------|--------|
| 0 – 19        | 0.1    |
| 20 – 49       | 0.2    |
| 50 – 99       | 0.3    |
| 100 – 149     | 0.4    |
| 150 – 299     | 0.6    |
| 300 – 399     | 0.8    |
| 400 – 499     | 0.9    |
| 500 or more   | 1.0    |

The weight is a 0.0–1.0 multiplier, not a score in the output CSV's units. How
it combines into `prevalence_score` is settled separately; see
`docs/METRICS.md`.

Corrections to the original implementation
------------------------------------------
Two defects in the version this replaces are fixed here, both of them silent:

1. **A count of exactly 500 fell through every branch.** The chain ended
   `< 500 -> 0.9` and `> 500 -> 1.0`, leaving 500 itself matching neither, so
   it returned the initial `0`. The band table below has no fallthrough by
   construction: the last band is an exclusive bound of 500, and anything not
   below it takes the maximum weight, which is `>= 500 -> 1.0`.
2. **The count itself was wrong.** It came from
   `repo.get_issues(state=...).totalCount`, which counts pull requests as
   issues and, since GitHub moved the issues endpoint to cursor pagination,
   now returns 1 for every repository regardless of its contents.

Both produce a plausible-looking number rather than an error, which is why the
bands are now data rather than control flow, and why every boundary is tested
individually.
"""

from __future__ import annotations

import logging
from typing import Final

LOGGER = logging.getLogger(__name__)

CLOSED_ISSUE_BANDS: Final[tuple[tuple[int, float], ...]] = (
    (20, 0.1),
    (50, 0.2),
    (100, 0.3),
    (150, 0.4),
    (300, 0.6),
    (400, 0.8),
    (500, 0.9),
)
"""Ordered `(exclusive upper bound, weight)` pairs.

The first band whose bound exceeds the count wins. Expressed as data rather
than as an if/elif chain so that the table can be read, tested and documented
as one object, and so that no input can fall between two branches.
"""

MAX_CLOSED_ISSUE_WEIGHT: Final = 1.0
"""Weight for a count at or above the last band's bound."""

MIN_CLOSED_ISSUE_WEIGHT: Final = 0.1
"""Weight for a repository with no closed issues at all.

Deliberately not zero, matching the original: the lowest band starts at 0 and
carries 0.1, so having no closed issues costs most of the weight without
zeroing the component outright.
"""


def score_closed_issues(closed_issues_total_count: int) -> float:
    """Score a closed-issue count as a 0.0-1.0 weight.

    Args:
        closed_issues_total_count: Closed issues, excluding pull requests. A
            negative value is treated as zero and logged, since a count cannot
            be negative and silently scoring it would hide the caller's bug.

    Returns:
        The weight for this count, from `MIN_CLOSED_ISSUE_WEIGHT` to
        `MAX_CLOSED_ISSUE_WEIGHT`.

    Examples:
        >>> score_closed_issues(0)
        0.1
        >>> score_closed_issues(499)
        0.9
        >>> score_closed_issues(500)
        1.0
    """
    count = closed_issues_total_count

    if count < 0:
        LOGGER.warning(
            "Closed-issue count %d is negative, which cannot happen; scoring it as 0",
            count,
        )
        count = 0

    for upper_bound, weight in CLOSED_ISSUE_BANDS:
        if count < upper_bound:
            LOGGER.debug(
                "Closed issues %d falls in band [<%d) -> weight %s",
                count,
                upper_bound,
                weight,
            )
            return weight

    LOGGER.debug(
        "Closed issues %d is at or above the top band (>=%d) -> weight %s",
        count,
        CLOSED_ISSUE_BANDS[-1][0],
        MAX_CLOSED_ISSUE_WEIGHT,
    )
    return MAX_CLOSED_ISSUE_WEIGHT


def describe_bands() -> str:
    """Render the band table as text, for logs and diagnostics.

    Returns:
        One line per band, in evaluation order.
    """
    lines = [f"  <{bound:<5} -> {weight}" for bound, weight in CLOSED_ISSUE_BANDS]
    lines.append(f"  >={CLOSED_ISSUE_BANDS[-1][0]:<4} -> {MAX_CLOSED_ISSUE_WEIGHT}")
    return "closed-issue bands:\n" + "\n".join(lines)
