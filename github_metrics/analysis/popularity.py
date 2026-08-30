"""Scoring stars and forks.

Two counts, two budgets, and two band tables that agree below 90 and diverge
above it. They live in one module because the tables are read against each
other: the fork thresholds only make sense next to the star thresholds they
were derived from.

| | Stars | Forks |
|---|---|---|
| Points | 10 | 15 |
| Bands below 90 | identical | identical |
| Full marks at | 300 | 150 |

The reference row carries 64,574 stars scoring 10.0 and 6,900 forks scoring
15.0 - both far above their ceilings, so both reproduce at full marks.

Why the fork ceiling is lower
-----------------------------
Forks are scarcer than stars. Measured across twelve well-known Python
repositories the median fork-to-star ratio was **0.186**, ranging from 0.05 to
0.47. At that median, the star table's ceiling of 300 corresponds to about
**56 forks** - so a fork ceiling of 150 is already some three times harder to
reach than the star ceiling, and 110 would have been harder still.

That is a deliberate asymmetry rather than an oversight: a fork is a stronger
signal than a star, because it takes more than a click.

The restored 0.9 band
---------------------
The version this replaces ended `< 110 -> 0.8` and then jumped straight to its
terminal branch, with no 0.9 anywhere - and that terminal branch was itself
broken (see below). A 0.9 band is restored at **150**, chosen because:

- it preserves every threshold the original had, adding one rather than moving
  any;
- 150 is already a boundary in the star table, so the two tables share one
  vocabulary of thresholds (5, 10, 20, 30, 40, 50, 70, 90, 110, 150, 300)
  instead of inventing a number that appears nowhere else;
- at the median fork-to-star ratio, 150 forks corresponds to roughly 800 stars,
  which is a genuinely well-used project rather than a merely visible one.

Corrections to the version this replaces
----------------------------------------
**`score_forks` had no `return` statement.** It computed a weight into a local
and then fell off the end, so it returned `None` for every input. The caller's
`15 * score_forks(forks)` therefore raised
`TypeError: unsupported operand type(s) for *: 'int' and 'NoneType'`. The fork
score could never have been produced.

**Its terminal branch assigned 0.1, not 1.0.** Had the missing `return` been
added alone, a repository with 110 or more forks would have scored *below* one
with 109 - 0.1 against 0.8 - so more forks would have meant a lower score. Both
are fixed here, and a test asserts the score never decreases as forks rise.

`score_stars` needed neither fix. Its chain was complete, total, and monotone;
only the negative-count guard is new.
"""

from __future__ import annotations

import logging
from typing import Final

LOGGER = logging.getLogger(__name__)

STAR_BANDS: Final[tuple[tuple[int, float], ...]] = (
    (5, 0.0),
    (10, 0.1),
    (20, 0.2),
    (30, 0.3),
    (40, 0.4),
    (50, 0.5),
    (70, 0.6),
    (90, 0.7),
    (150, 0.8),
    (300, 0.9),
)
"""Ordered `(exclusive upper bound, weight)` pairs for stars."""

FORK_BANDS: Final[tuple[tuple[int, float], ...]] = (
    (5, 0.0),
    (10, 0.1),
    (20, 0.2),
    (30, 0.3),
    (40, 0.4),
    (50, 0.5),
    (70, 0.6),
    (90, 0.7),
    (110, 0.8),
    (150, 0.9),
)
"""Ordered `(exclusive upper bound, weight)` pairs for forks.

Identical to `STAR_BANDS` up to 90, then tighter: full marks at 150 rather than
300.
"""

MAX_WEIGHT: Final = 1.0
"""Weight for a count at or above the last band."""

STARS_POINTS: Final = 10.0
"""Points a repository earns at full star weight."""

FORKS_POINTS: Final = 15.0
"""Points a repository earns at full fork weight."""


def _weigh(count: int, bands: tuple[tuple[int, float], ...], label: str) -> float:
    """Find the weight for a count in a band table.

    Args:
        count: The raw count.
        bands: Ordered `(exclusive upper bound, weight)` pairs.
        label: What is being weighed, for the log messages.

    Returns:
        The weight for this count.
    """
    value = count

    if value < 0:
        LOGGER.warning("%s count is %d, which is negative; scoring it as 0", label, value)
        value = 0

    for upper_bound, weight in bands:
        if value < upper_bound:
            LOGGER.debug(
                "%s %d falls in band [<%d) -> weight %s", label, value, upper_bound, weight
            )
            return weight

    LOGGER.debug(
        "%s %d is at or above the top band (>=%d) -> weight %s",
        label,
        value,
        bands[-1][0],
        MAX_WEIGHT,
    )
    return MAX_WEIGHT


def stars_weight(repository_stars_total_count: int) -> float:
    """Score a star count as a 0.0-1.0 weight.

    Args:
        repository_stars_total_count: Stars. A negative value is reported and
            treated as zero.

    Returns:
        The weight, from 0.0 to `MAX_WEIGHT`.

    Examples:
        >>> stars_weight(4)
        0.0
        >>> stars_weight(64574)
        1.0
    """
    return _weigh(repository_stars_total_count, STAR_BANDS, "Stars")


def forks_weight(repository_forks_total_count: int) -> float:
    """Score a fork count as a 0.0-1.0 weight.

    Args:
        repository_forks_total_count: Forks. A negative value is reported and
            treated as zero.

    Returns:
        The weight, from 0.0 to `MAX_WEIGHT`.

    Examples:
        >>> forks_weight(4)
        0.0
        >>> forks_weight(6900)
        1.0
    """
    return _weigh(repository_forks_total_count, FORK_BANDS, "Forks")


def score_stars(repository_stars_total_count: int) -> float:
    """Score the `stars_score` column.

    Args:
        repository_stars_total_count: Stars.

    Returns:
        The score, from 0.0 to `STARS_POINTS`.

    Examples:
        >>> score_stars(64574)
        10.0
    """
    score = STARS_POINTS * stars_weight(repository_stars_total_count)
    LOGGER.info(
        "Stars score %s/%s from %d stars",
        score,
        STARS_POINTS,
        max(repository_stars_total_count, 0),
    )
    return score


def score_forks(repository_forks_total_count: int) -> float:
    """Score the `forks_score` column.

    Args:
        repository_forks_total_count: Forks.

    Returns:
        The score, from 0.0 to `FORKS_POINTS`.

    Examples:
        >>> score_forks(6900)
        15.0
    """
    score = FORKS_POINTS * forks_weight(repository_forks_total_count)
    LOGGER.info(
        "Forks score %s/%s from %d forks",
        score,
        FORKS_POINTS,
        max(repository_forks_total_count, 0),
    )
    return score


def describe_bands() -> str:
    """Render both band tables as text, side by side."""
    lines = ["star and fork bands (weight for a count below each bound):"]
    lines.append(f"  {'bound':>6}  {'stars':>6}  {'forks':>6}")
    bounds = sorted({bound for bound, _ in STAR_BANDS} | {bound for bound, _ in FORK_BANDS})
    star_map = dict(STAR_BANDS)
    fork_map = dict(FORK_BANDS)
    for bound in bounds:
        star = star_map.get(bound, "")
        fork = fork_map.get(bound, "")
        lines.append(f"  <{bound:<5}  {star!s:>6}  {fork!s:>6}")
    lines.append(f"  {'above':>6}  {MAX_WEIGHT:>6}  {MAX_WEIGHT:>6}")
    return "\n".join(lines)
