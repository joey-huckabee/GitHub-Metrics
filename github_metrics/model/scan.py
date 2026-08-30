"""Identity of a single collection run."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class ScanIdentifier:
    """Identifies one run, and stamps every row that run produces.

    Both values are per **run**, not per repository: every row from a single
    invocation carries the same pair. That is what makes a result set
    groupable after the fact — without it, rows from two runs that landed in
    the same file or table could not be told apart.

    They are also assigned before any repository is fetched, so a row for a
    repository that could not be read still carries them. A row that cannot be
    attributed to a run is of little use once results are stored.

    Attributes:
        scan_id: A UUID4 generated once per run.
        scan_date: When the run started, timezone-aware and in UTC.
    """

    scan_id: UUID = field(default_factory=uuid4)
    scan_date: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))

    def __post_init__(self) -> None:
        """Reject a naive timestamp.

        Raises:
            ValueError: If `scan_date` carries no timezone. A naive timestamp
                is ambiguous the moment it leaves the machine that made it,
                and these values are written to files and, later, a database.
        """
        if self.scan_date.tzinfo is None:
            raise ValueError("scan_date must be timezone-aware; use UTC")
