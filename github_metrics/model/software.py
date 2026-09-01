"""One row of the metrics output.

`SoftwareRow` is the single definition of what a result looks like. The CSV
writer, the JSON writer and the console renderer all derive their columns from
it, so the three formats cannot drift apart.

Field definitions and scoring bands live in `docs/METRICS.md`.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from dataclasses_json import DataClassJsonMixin

EMPTY: Final = ""
"""How an unknown value is rendered in every output format."""


# The attribute count is the output contract: twenty columns, defined in
# docs/METRICS.md. The design check is aimed at classes that carry behaviour,
# not at a record whose whole purpose is to hold one row.
@dataclass
class SoftwareRow(DataClassJsonMixin):  # pylint: disable=too-many-instance-attributes
    """Metrics and scores for one repository, at one point in time.

    **Every metric field defaults to `None`, not to zero.** Zero is a
    legitimate measurement — a repository really can have zero releases and
    zero closed issues — so using it to mean "not collected" would make a
    repository that was measured indistinguishable from one that could not be
    read. `None` renders as an empty field, which is unambiguous in both CSV
    and JSON.

    The identity fields default to empty strings instead, because they are
    always known: they come from the input row and the scan, not from the API,
    so they are populated even for a repository that 404s. `organization` is
    the exception — it reads like identity but the API reports it, so it is
    empty for a repository that could not be read.

    Attributes:
        name: The repository's name. GitHub's value when the repository
            was read, since a renamed repository still resolves through a
            redirect and GitHub reports the current name; the `repoid` value
            from the input row otherwise, so the column survives a failed read.
        owner: The `owner` value from the input row, verbatim.
        organization: The owning organisation's login, or empty when the
            repository is owned by an individual account.
        url: The repository's canonical `https://github.com/owner/name`
            address, built from the values above rather than reported
            separately. It is an identity column: a row for a repository
            that could not be read still carries the address the input
            asked for, which is what someone checking the failure needs.
        scan_date: Start of the run that produced this row, UTC.
        scan_id: UUID4 of the run that produced this row.
        stars: Raw star count.
        forks: Raw fork count.
        age_days: Days since the repository was created.
        last_update_hours: Hours since the repository was last updated.
        closed_issues: Count of closed issues.
        releases: Count of releases.
        prevalence_score: Score component. Driving input pending.
        stars_score: Score component derived from `stars`.
        forks_score: Score component derived from `forks`.
        maturity_score: Score component derived from `age_days`.
        last_update_score: Score component derived from `last_update_hours`.
        trusted_org_bonus: Score component derived from `is_trusted_org`.
        total_score: Sum of the six score components.
        is_trusted_org: Whether the owner is on the trusted list.
    """

    name: str = ""
    owner: str = ""
    organization: str = ""
    url: str = ""
    scan_date: datetime | None = None
    scan_id: UUID | None = None
    stars: int | None = None
    forks: int | None = None
    age_days: float | None = None
    last_update_hours: float | None = None
    closed_issues: int | None = None
    releases: int | None = None
    prevalence_score: float | None = None
    stars_score: float | None = None
    forks_score: float | None = None
    maturity_score: float | None = None
    last_update_score: float | None = None
    trusted_org_bonus: float | None = None
    total_score: float | None = None
    is_trusted_org: bool | None = None

    @classmethod
    def to_header(cls) -> tuple[str, ...]:
        """Column names, in their canonical output order.

        Derived from the field order of this dataclass rather than from a
        separate list, so a field cannot be added without appearing in the
        output, and the two cannot fall out of order.
        """
        return tuple(item.name for item in fields(cls))

    def to_iterable(self, columns: tuple[str, ...] | None = None) -> tuple[str, ...]:
        """Render this row as output-ready strings.

        Args:
            columns: Column names to emit, in the order to emit them. Defaults
                to every column in canonical order.

        Returns:
            One rendered string per requested column.
        """
        selected = columns if columns is not None else self.to_header()
        return tuple(render(getattr(self, name)) for name in selected)

    def to_mapping(self, columns: tuple[str, ...] | None = None) -> dict[str, Any]:
        """Render this row as a JSON-ready mapping.

        Unlike `to_iterable`, values keep their JSON types — a number stays a
        number and a boolean stays a boolean — because JSON can express them
        and a consumer should not have to re-parse strings. An unknown value
        becomes `null`.

        Args:
            columns: Column names to emit. Defaults to every column.

        Returns:
            Column name to JSON-ready value.
        """
        selected = columns if columns is not None else self.to_header()
        return {name: jsonable(getattr(self, name)) for name in selected}


def render(value: object) -> str:
    """Render one value for CSV or console output.

    Args:
        value: The value to render.

    Returns:
        `EMPTY` for `None`; lowercase `true`/`false` for a boolean; `str` of
        anything else. Floats keep full precision — these are measurements,
        and rounding at the output boundary would silently discard resolution
        the caller may need.
    """
    if value is None:
        return EMPTY
    if isinstance(value, bool):
        # Checked before the general case: Python's str(True) is "True", and
        # the agreed output form is lowercase.
        return "true" if value else "false"
    # str() is already the agreed rendering for the remaining types, including
    # datetime (ISO-like, with offset) and UUID (canonical hyphenated form).
    return str(value)


def jsonable(value: object) -> Any:
    """Convert one value to something `json.dumps` accepts.

    Args:
        value: The value to convert.

    Returns:
        `None` unchanged, `datetime` and `UUID` as strings matching their CSV
        rendering, and everything else unchanged so numbers and booleans keep
        their JSON types.
    """
    if value is None:
        return None
    if isinstance(value, (datetime, UUID)):
        return str(value)
    return value
