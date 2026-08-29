"""Calculate GitHub metrics for FOSS analysis."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("github-metrics")
except PackageNotFoundError:  # pragma: no cover - source checkout without install
    __version__ = "0.0.0.dev0"

__all__ = ["__version__"]
