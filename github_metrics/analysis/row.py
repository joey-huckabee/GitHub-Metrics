"""Assembling one output row from one collected repository.

This is the join between the two halves: collection produced measurements,
`analysis` turns them into scores, and a `SoftwareRow` is the two together
under the column names `githubmetrics.csv` publishes.

It is deliberately the only place that knows how a row is built. Scattering
this across the collectors would put scoring inside collection, and scattering
it across the renderers would put it after the point where a row is supposed to
be finished.

A repository that could not be read still produces a row. Its identity columns
carry what the input asked for, its measurements are `None` - empty in CSV,
`null` in JSON - and nothing is scored. Not zero: zero is a legitimate score
for a repository that was measured and found wanting, so using it here would
put the unreadable and the inactive in the same bucket and let every average
absorb the difference.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import replace

from github_metrics.analysis.elapsed import age_days, last_update_hours
from github_metrics.analysis.last_update import score_last_update
from github_metrics.analysis.maturity import score_maturity
from github_metrics.analysis.popularity import score_forks, score_stars
from github_metrics.analysis.prevalence import score_prevalence
from github_metrics.analysis.total import score_total
from github_metrics.analysis.trusted_orgs import (
    TrustedOrganizations,
    is_trusted_org,
    score_org_bonus,
)
from github_metrics.collect.repository import RepoMetaData
from github_metrics.model.contributor import Contributor, ContributorBlock
from github_metrics.model.scan import ScanIdentifier
from github_metrics.model.software import SoftwareRow
from github_metrics.sources import RepositoryRef

LOGGER = logging.getLogger(__name__)


def build_block(
    contributors: Sequence[Contributor],
    scan: ScanIdentifier,
) -> ContributorBlock:
    """Assemble the contributor block of one repository's document.

    Called only for a repository whose contributor list was read. A repository
    whose list failed produces a CSV row and no document at all, so there is no
    block to build and no aggregate to leave unset - which is why
    `contribution_total` is a plain `int` here rather than an optional one.

    The run identity is stamped here rather than in `collect` for the same
    reason a row's is: the scan is a property of the run, and collection should
    not have to know which run it is part of.

    Args:
        contributors: The records as collected, most commits first.
        scan: Identity of this run.

    Returns:
        The block, stamped and totalled. The four judgement-dependent
        aggregates are left `None`: nothing has measured them, and a `0` would
        assert that a repository has no foreign contribution rather than say
        that no rule has been applied.
    """
    stamped = tuple(
        replace(entry, scan_id=scan.scan_id, scan_date=scan.scan_date) for entry in contributors
    )
    # Counts what was collected, not what exists. Since v0.5.0 that is every
    # contributor GitHub returns, but GitHub itself links only the first 500
    # author email addresses to accounts and serves counts it has cached, so
    # the narrower wording stays: a total that silently means something other
    # than its name is the kind of number that survives review. METRICS.md
    # records both ceilings.
    total = sum(entry.contribution or 0 for entry in stamped)

    return ContributorBlock(contributors=stamped, contribution_total=total)


def build_row(
    reference: RepositoryRef,
    metadata: RepoMetaData,
    scan: ScanIdentifier,
    *,
    registry: TrustedOrganizations | None = None,
) -> SoftwareRow:
    """Score one collected repository into an output row.

    Contributors are deliberately not an argument. The row is the twenty
    columns `githubmetrics.csv` publishes, and none of them is derived from a
    contributor list; what is derived from one lives in `ContributorBlock` and
    reaches only the document.

    Args:
        reference: The reference as the input named it.
        metadata: What GitHub reported.
        scan: Identity of this run, stamped on every row.
        registry: Trusted-organisation registry. Defaults to the built-in list.

    Returns:
        The finished row.
    """
    elapsed_days = age_days(metadata.timestamps.created_at, scan.scan_date)
    elapsed_hours = last_update_hours(metadata.timestamps.updated_at, scan.scan_date)
    versions = metadata.distinct_versions

    prevalence = score_prevalence(
        metadata.closed_issues,
        versions,
        issues_enabled=metadata.issues_enabled,
    )
    stars = score_stars(metadata.stars)
    forks = score_forks(metadata.forks)
    maturity = score_maturity(elapsed_days)
    last_update = score_last_update(elapsed_hours)

    # The bonus and the column resolve through the same registry, so they
    # cannot disagree about who is trusted.
    owner = metadata.resolved_owner
    bonus = score_org_bonus(owner, registry)

    return SoftwareRow(
        name=metadata.resolved_name,
        owner=reference.owner,
        organization=metadata.organization,
        url=metadata.url,
        scan_date=scan.scan_date,
        scan_id=scan.scan_id,
        stars=metadata.stars,
        forks=metadata.forks,
        age_days=elapsed_days,
        last_update_hours=elapsed_hours,
        closed_issues=metadata.closed_issues,
        releases=versions,
        prevalence_score=prevalence,
        stars_score=stars,
        forks_score=forks,
        maturity_score=maturity,
        last_update_score=last_update,
        trusted_org_bonus=bonus,
        total_score=score_total(prevalence, stars, forks, maturity, last_update, bonus),
        is_trusted_org=is_trusted_org(owner, registry),
    )


def build_empty_row(reference: RepositoryRef, scan: ScanIdentifier) -> SoftwareRow:
    """Produce the row for a repository that could not be collected.

    Args:
        reference: The reference as the input named it.
        scan: Identity of this run.

    Returns:
        A row carrying only what is known without the API.
    """
    LOGGER.debug(
        "%s produced no measurements; emitting an identity-only row",
        reference.full_name,
    )
    return SoftwareRow(
        # The name comes from the input here. GitHub reported nothing, and the
        # column that says which repository a row is about has to have an
        # answer even - especially - when nothing else does. The same goes for
        # the address: it is where someone would go to find out why the read
        # failed, so it is exactly the row that must not omit it.
        name=reference.repoid,
        owner=reference.owner,
        url=reference.url,
        scan_date=scan.scan_date,
        scan_id=scan.scan_id,
    )
