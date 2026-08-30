"""Scoring collected data.

Every function here is pure: it takes values that collection already gathered
and returns a score. Nothing in this package reaches the network, which is what
lets the bands be tested exhaustively without a token.
"""

from github_metrics.analysis.closed_issues import score_closed_issues
from github_metrics.analysis.elapsed import age_days, last_update_hours
from github_metrics.analysis.last_update import score_last_update
from github_metrics.analysis.maturity import score_maturity
from github_metrics.analysis.popularity import score_forks, score_stars
from github_metrics.analysis.prevalence import score_prevalence
from github_metrics.analysis.releases import score_releases
from github_metrics.analysis.trusted_orgs import (
    TrustedOrganizations,
    is_trusted_org,
    score_trusted_org_bonus,
)

__all__ = [
    "age_days",
    "last_update_hours",
    "TrustedOrganizations",
    "is_trusted_org",
    "score_closed_issues",
    "score_forks",
    "score_maturity",
    "score_last_update",
    "score_prevalence",
    "score_releases",
    "score_stars",
    "score_trusted_org_bonus",
]
