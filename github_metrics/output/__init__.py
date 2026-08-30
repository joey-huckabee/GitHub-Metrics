"""Rendering results to CSV, JSON and the console.

Every format derives its columns from `SoftwareRow`, so the three cannot drift
apart. Where results *go* lives here too; where they come *from* lives in
`github_metrics.sources`.
"""

from github_metrics.output.destination import resolve_destination
from github_metrics.output.fields import ALL_FIELDS, resolve_fields
from github_metrics.output.render import render_console, write_csv, write_json

__all__ = [
    "ALL_FIELDS",
    "render_console",
    "resolve_destination",
    "resolve_fields",
    "write_csv",
    "write_json",
]
