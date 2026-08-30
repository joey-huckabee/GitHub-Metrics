"""Scoring collected data.

Every function here is pure: it takes values that collection already gathered
and returns a score. Nothing in this package reaches the network, which is what
lets the bands be tested exhaustively without a token.
"""

from github_metrics.analysis.closed_issues import score_closed_issues
from github_metrics.analysis.prevalence import score_prevalence
from github_metrics.analysis.releases import score_releases
from github_metrics.analysis.trusted_orgs import TrustedOrganizations, is_trusted_org

__all__ = [
    "TrustedOrganizations",
    "is_trusted_org",
    "score_closed_issues",
    "score_prevalence",
    "score_releases",
]
