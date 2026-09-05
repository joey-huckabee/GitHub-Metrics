"""Turning what a run collected into a statement about how good it is.

This is the seam where collected outcomes become `statistics.json`, and it sits
in `analysis/` for the same reason `analysis/row.py` does: it reaches no
network and writes no file, so the structural rule holds on both sides.

Everything here is derived from data the run already has. The one exception is
`commits_total`, which needs a count only the API can give and is therefore
passed in rather than computed - see `collect.history`.

What the numbers are for
------------------------
Each figure exists to bound a number published elsewhere:

- **coverage** bounds `contribution_total`, which is a sum over what was
  collected rather than over what exists;
- **exclusions** say who is missing and why, in people *and* commits, because
  a reason can account for most of the people and little of the work;
- **bots** let an analysis remove automation from a total this tool
  deliberately leaves raw;
- **concentration** answers "where does this project's work come from" without
  naming anybody a maintainer;
- **geography** carries the unknown-location share, which is the error bar on
  every national claim a later stage might compute.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from github_metrics.model.contributor import Contributor
from github_metrics.model.software import SoftwareRow
from github_metrics.model.statistics import (
    AttributionMethod,
    BotStatistics,
    Concentration,
    CountryTotals,
    Exclusion,
    ExclusionReason,
    Geography,
    IdentityGaps,
    RepositoryStatistics,
)

LOGGER = logging.getLogger(__name__)

BUS_FACTOR_SHARE = 0.5
"""Fraction of attributed commits the bus factor counts contributors up to."""

MINIMUM_FOR_INEQUALITY = 2
"""Contributors needed before inequality means anything.

One contributor holds everything by definition, and a Gini of 1.0 there
would read as concentration rather than as a repository with one author.
"""


def build_repository_statistics(
    row: SoftwareRow,
    contributors: Sequence[Contributor],
    *,
    collected: bool,
    documented: bool,
    commits_total: int | None = None,
    gaps: IdentityGaps | None = None,
    attribution: AttributionMethod = AttributionMethod.CONTRIBUTOR_LIST,
) -> RepositoryStatistics:
    """Summarise one repository's collection.

    Args:
        row: The finished row this entry is about. Taken whole rather than as
            three identity arguments, because the statistics entry and the CSV
            row describe the same repository and must not disagree about which.
        contributors: The records collected, most commits first.
        collected: Whether metrics were read.
        documented: Whether a document was written.
        commits_total: Commits on the default branch, when known.
        gaps: What the contributor list did not yield. Omitted when nothing
            widened the count, in which case what was collected is all that is
            known and coverage reads as complete - which it is, of what was
            asked for.
        attribution: How the contributor set was determined.

    Returns:
        The statistics for this repository.
    """
    attributed = sum(entry.contribution or 0 for entry in contributors)
    collected_count = len(contributors)
    gaps = gaps or IdentityGaps()

    return RepositoryStatistics(
        owner=row.owner,
        name=row.name,
        url=row.url,
        collected=collected,
        documented=documented,
        attribution=attribution,
        commits_total=commits_total,
        commits_attributed=attributed,
        contributor_identities=(
            gaps.identities if gaps.identities is not None else collected_count
        ),
        contributors_collected=collected_count,
        gaps=gaps,
        exclusions=_exclusions(contributors, gaps),
        bots=_bots(contributors, attributed),
        concentration=_concentration(contributors, attributed),
        geography=_geography(contributors, attributed),
    )


def _exclusions(
    contributors: Sequence[Contributor],
    gaps: IdentityGaps,
) -> tuple[Exclusion, ...]:
    """Account for everyone who is not fully represented, and why.

    Two groups, deliberately in one vocabulary. The first is absent from the
    document; the second is present but carries no usable location, which is
    the thing that bounds a geographic claim.

    An empty bucket is omitted rather than reported as zero: a reason that did
    not apply says nothing, and listing every reason for every repository would
    bury the ones that did.
    """
    found: list[Exclusion] = []

    if gaps.recovered:
        # Counted as a rescue rather than a loss: these were beyond GitHub's
        # ceiling and are in the document anyway.
        found.append(Exclusion(ExclusionReason.ANONYMOUS_RECOVERED_NOREPLY, people=gaps.recovered))
    if gaps.unrecoverable is not None and gaps.unrecoverable.people:
        found.append(gaps.unrecoverable)
    if gaps.unresolvable:
        found.append(Exclusion(ExclusionReason.ACCOUNT_UNRESOLVABLE, people=gaps.unresolvable))

    # The location states. `internal_address` distinguishes all three by
    # design, and this is where that distinction is finally counted.
    no_location = [entry for entry in contributors if entry.internal_address.query is None]
    unresolved = [
        entry
        for entry in contributors
        if entry.internal_address.query is not None and not entry.internal_address.country_code
    ]

    for reason, group in (
        (ExclusionReason.NO_LOCATION_PUBLISHED, no_location),
        (ExclusionReason.LOCATION_UNRESOLVED, unresolved),
    ):
        if group:
            found.append(
                Exclusion(
                    reason,
                    people=len(group),
                    commits=sum(entry.contribution or 0 for entry in group),
                )
            )

    return tuple(found)


def _bots(contributors: Sequence[Contributor], attributed: int) -> BotStatistics:
    """Count the accounts GitHub reports as bots.

    Their commits stay in `contribution_total`; this publishes the adjusted
    figure beside it so an analysis can choose. See `docs/METRICS.md`.
    """
    bots = [entry for entry in contributors if entry.is_bot]
    commits = sum(entry.contribution or 0 for entry in bots)
    if bots:
        LOGGER.debug("%d bot account(s) holding %d commits", len(bots), commits)
    return BotStatistics(
        count=len(bots),
        commits=commits,
        logins=tuple(entry.name for entry in bots),
        attributed_excluding_bots=attributed - commits,
    )


def _concentration(contributors: Sequence[Contributor], attributed: int) -> Concentration:
    """Measure where the work sits.

    Ordered here rather than trusting the input order: GitHub ranks the
    contributor list by contribution, but a deep-attribution walk need not, and
    a top-N share computed over an unsorted list would be silently wrong.
    """
    if not contributors or attributed <= 0:
        return Concentration()

    counts = sorted((entry.contribution or 0 for entry in contributors), reverse=True)

    def share(count: int) -> float:
        return round(sum(counts[:count]) / attributed * 100, 2)

    running = 0
    bus_factor = len(counts)
    for index, value in enumerate(counts, start=1):
        running += value
        if running > attributed * BUS_FACTOR_SHARE:
            bus_factor = index
            break

    return Concentration(
        top_1_percent=share(1),
        top_5_percent=share(5),
        top_10_percent=share(10),
        bus_factor=bus_factor,
        gini=_gini(counts, attributed),
    )


def _gini(descending: Sequence[int], total: int) -> float:
    """Gini coefficient of a contribution distribution.

    Args:
        descending: Contribution counts, largest first.
        total: Their sum.

    Returns:
        0.0 where everyone contributed equally, approaching 1.0 where one
        person contributed everything.
    """
    size = len(descending)
    if size < MINIMUM_FOR_INEQUALITY or total <= 0:
        return 0.0
    # Ascending order is what the standard formula indexes over.
    ascending = list(reversed(descending))
    weighted = sum((index + 1) * value for index, value in enumerate(ascending))
    return round((2 * weighted) / (size * total) - (size + 1) / size, 4)


def _geography(contributors: Sequence[Contributor], attributed: int) -> Geography:
    """Break attributed commits down by resolved country.

    A contributor with no resolved country lands in the unknown share rather
    than being dropped, because dropping them would make the remaining
    percentages sum to 100 and imply a completeness the data has not got.
    """
    countries: dict[str, CountryTotals] = {}
    known = 0

    for entry in contributors:
        code = entry.internal_address.country_code
        commits = entry.contribution or 0
        if not code:
            continue
        current = countries.get(code, CountryTotals())
        countries[code] = CountryTotals(
            people=current.people + 1,
            commits=current.commits + commits,
        )
        known += commits

    return Geography(
        countries=countries,
        commits_with_known_location=known,
        commits_with_unknown_location=attributed - known,
    )
