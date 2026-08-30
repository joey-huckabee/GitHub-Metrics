"""Data types shared across collection, analysis and output.

Kept in one package so that the shape of a result is defined in exactly one
place. Collection fills these in, analysis scores them, and output renders
them; none of those three owns the definition.
"""

from github_metrics.model.scan import ScanIdentifier
from github_metrics.model.software import SoftwareRow

__all__ = ["ScanIdentifier", "SoftwareRow"]
