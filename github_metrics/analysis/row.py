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
from github_metrics.model.scan import ScanIdentifier
from github_metrics.model.software import SoftwareRow
from github_metrics.sources import RepositoryRef

LOGGER = logging.getLogger(__name__)


def build_row(
    reference: RepositoryRef,
    metadata: RepoMetaData,
    scan: ScanIdentifier,
    *,
    registry: TrustedOrganizations | None = None,
) -> SoftwareRow:
    """Score one collected repository into an output row.

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
        repo_name=metadata.resolved_name,
        owner=reference.owner,
        organization=metadata.organization,
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
        # answer even - especially - when nothing else does.
        repo_name=reference.repoid,
        owner=reference.owner,
        scan_date=scan.scan_date,
        scan_id=scan.scan_id,
    )
