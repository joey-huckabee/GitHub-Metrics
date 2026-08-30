"""Scoring collected data.

Every function here is pure: it takes values that collection already gathered
and returns a score. Nothing in this package reaches the network, which is what
lets the bands be tested exhaustively without a token.
"""

from github_metrics.analysis.closed_issues import score_closed_issues
from github_metrics.analysis.releases import score_releases

__all__ = ["score_closed_issues", "score_releases"]
