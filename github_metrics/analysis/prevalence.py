"""Scoring prevalence: has this project shipped, and is it maintained?

`prevalence_score` is 20 points multiplied by a 0.0-1.0 weight drawn from two
signals - closed issues and distinct versions. Either one is evidence, so the
stronger of the two is used.

This is a gate, not a ranking
----------------------------
Both signals saturate on any established project: closed issues reach their
maximum weight at 500, distinct versions at 80. Measured across five real
repositories, the smallest figures were 1,241 closed issues and 108 versions -
both comfortably above the ceilings.

    cline/cline        3770 closed, 717 versions -> 20.0
    pypa/virtualenv    1429 closed, 285 versions -> 20.0
    urllib3/urllib3    1241 closed, 108 versions -> 20.0
    bokeh/bokeh        7511 closed, 151 versions -> 20.0
    torvalds/linux        0 closed, 943 versions -> 20.0

So for a portfolio of mature projects this component reports a **constant
20.0** and contributes nothing to the ordering. That is deliberate: it answers
"has this project shipped anything and been maintained at all", and the answer
for a mature project is yes. It does its work at the bottom of the range,
separating young, abandoned, or never-released projects from the rest.

If a future release wants prevalence to help rank mature projects, the change
is to the band *boundaries* - the 500 and the 80 - not to the weights. See
`docs/METRICS.md`.

When a signal is absent
-----------------------
The issue signal is weighed only when there is issue evidence. Two situations
produce none - a tracker that is switched off, and a tracker with nothing
closed in it - and both are treated the same way: the signal is excluded and
the release weight stands alone.

This is what keeps a repository that has shipped nothing and closed nothing at
**0.0**. The closed-issue band table floors at 0.1 for any count below 20,
zero included, so weighing an empty tracker would score such a project 2.0 and
put it above a project with no evidence at all - which is the wrong way round.

Why the stronger signal rather than a fallback
----------------------------------------------
The original rule selected on `closed_issues == 0`, which put a cliff at
exactly one closed issue: a project with none scored 20.0 through the release
branch, and the same project after closing a single issue scored 2.0. Taking
the stronger of the two removes the cliff, removes the branch, and makes the
score non-decreasing in both inputs - closing an issue or cutting a release
can never lower it.
"""

from __future__ import annotations

import logging
from typing import Final

from github_metrics.analysis.closed_issues import score_closed_issues
from github_metrics.analysis.releases import score_releases

LOGGER = logging.getLogger(__name__)

PREVALENCE_POINTS: Final = 20.0
"""Points a repository earns at full weight."""

MAX_PREVALENCE_SCORE: Final = PREVALENCE_POINTS
"""The most this component can contribute to `total_score`."""


def score_prevalence(
    closed_issues: int,
    distinct_versions: int,
    *,
    issues_enabled: bool = True,
) -> float:
    """Score prevalence from the stronger of two signals.

    Args:
        closed_issues: Closed issues, excluding pull requests.
        distinct_versions: Distinct versions, which is the tag count.
        issues_enabled: Whether the repository's issue tracker is on. When it
            is off the issue signal is **excluded** rather than scored at the
            closed-issue table's floor, because the signal is absent rather
            than low - the project may track its work somewhere this tool
            cannot see. A count of zero is excluded for the same reason.

    Returns:
        The score, from 0.0 to `MAX_PREVALENCE_SCORE`.

    Examples:
        >>> score_prevalence(3770, 717)
        20.0
        >>> score_prevalence(0, 0)          # nothing shipped, nothing closed
        0.0
        >>> score_prevalence(1, 0)          # one closed issue is evidence
        2.0
    """
    release_weight = score_releases(distinct_versions)

    # The issue signal counts only when there is issue evidence to weigh. Two
    # situations produce none, and they are the same situation as far as this
    # score is concerned: a tracker that is switched off, and a tracker with
    # nothing closed in it. Treating them alike is what keeps a repository that
    # has shipped and closed nothing at 0.0 rather than at the closed-issue
    # table's floor of 0.1, which that table applies to any count below 20.
    if not issues_enabled:
        weight = release_weight
        LOGGER.info(
            "Prevalence: issue tracker disabled, only the release signal counts (weight %s)",
            release_weight,
        )
    elif closed_issues <= 0:
        weight = release_weight
        LOGGER.info(
            "Prevalence: no closed issues, so only the release signal counts (weight %s)",
            release_weight,
        )
    else:
        issue_weight = score_closed_issues(closed_issues)
        weight = max(issue_weight, release_weight)
        stronger = "closed issues" if issue_weight >= release_weight else "versions"
        LOGGER.debug(
            "Prevalence: issue weight %s, release weight %s; %s is the stronger signal",
            issue_weight,
            release_weight,
            stronger,
        )

    score = PREVALENCE_POINTS * weight

    if weight >= 1.0:
        LOGGER.debug(
            "Prevalence saturated at %s of %s; this project is indistinguishable "
            "from any other that has cleared the same thresholds",
            score,
            MAX_PREVALENCE_SCORE,
        )
    elif weight == 0.0:
        LOGGER.info(
            "Prevalence scored 0.0: no closed issues and no versions, so there is "
            "no evidence this project has shipped or been maintained"
        )

    LOGGER.info(
        "Prevalence %s/%s (weight %s) from %d closed issues and %d versions",
        score,
        MAX_PREVALENCE_SCORE,
        weight,
        closed_issues,
        distinct_versions,
    )
    return score
