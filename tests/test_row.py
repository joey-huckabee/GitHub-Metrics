"""Tests for :mod:`github_metrics.analysis.row`."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import pytest

from github_metrics.analysis.row import build_empty_row, build_row
from github_metrics.analysis.total import MAX_TOTAL_SCORE
from github_metrics.collect.repository import RepoMetaData
from github_metrics.collect.timestamps import RepositoryTimestamps
from github_metrics.model.scan import ScanIdentifier
from github_metrics.output.fields import ALL_FIELDS
from github_metrics.sources import RepositoryRef

SCAN = ScanIdentifier(
    scan_id=UUID("ca219015-79a4-4bd6-b37e-272fa74bd8c2"),
    scan_date=datetime(2026, 7, 12, 20, 33, 7, 254804, tzinfo=timezone.utc),
)

REFERENCE = RepositoryRef(owner="cline", repoid="cline", source_line=2)


DEFAULTS: dict[str, Any] = {
    "owner": "cline",
    "name": "cline",
    "owner_type": "Organization",
    "stars": 64_574,
    "forks": 6_900,
    "closed": 3_770,
    "releases": 398,
    "tags": 717,
    "issues_enabled": True,
}
"""What the worked example reports, before a test changes one of them."""


def metadata(**overrides: Any) -> RepoMetaData:
    """Collected metadata for the worked example.

    Taking the fields as a mapping rather than as nine parameters keeps the
    call sites unchanged while leaving the argument-count limit in place for
    the code that limit is meant to police.
    """
    unknown = set(overrides) - set(DEFAULTS)
    if unknown:
        raise TypeError(f"metadata() got unexpected keyword arguments {sorted(unknown)}")
    values = {**DEFAULTS, **overrides}

    return RepoMetaData(
        owner=REFERENCE.owner,
        repoid=REFERENCE.repoid,
        resolved_owner=str(values["owner"]),
        resolved_name=str(values["name"]),
        owner_type=str(values["owner_type"]),
        stars=int(values["stars"]),
        forks=int(values["forks"]),
        timestamps=RepositoryTimestamps(
            created_at=datetime(2024, 7, 6, 7, 28, 10, tzinfo=timezone.utc),
            updated_at=datetime(2026, 7, 12, 12, 27, 0, tzinfo=timezone.utc),
            pushed_at=datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc),
        ),
        closed_issues=int(values["closed"]),
        open_issues=691,
        issues_enabled=bool(values["issues_enabled"]),
        releases=int(values["releases"]),
        tags=int(values["tags"]),
    )


# ---------------------------------------------------------------------------
# A collected repository
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-ROW-001")
def test_the_reference_row_is_reproduced() -> None:
    """The worked example from docs/METRICS.md, end to end.

    Every score but maturity at full marks, no trusted-organisation bonus:
    72.0 out of 85.0.
    """
    row = build_row(REFERENCE, metadata(), SCAN)

    assert row.name == "cline"
    assert row.owner == "cline"
    assert row.organization == "cline"
    assert row.stars == 64_574
    assert row.forks == 6_900
    assert row.closed_issues == 3_770
    assert row.releases == 717
    assert row.maturity_score == 12.0
    assert row.total_score == 72.0


@pytest.mark.requirement("L3-ROW-001")
def test_the_scored_release_count_is_the_distinct_one() -> None:
    # Not releases plus tags, which counts every release twice.
    assert build_row(REFERENCE, metadata(releases=398, tags=717), SCAN).releases == 717


@pytest.mark.requirement("L3-ROW-001")
def test_the_total_is_the_sum_of_the_six_components() -> None:
    row = build_row(REFERENCE, metadata(), SCAN)

    components = (
        row.prevalence_score,
        row.stars_score,
        row.forks_score,
        row.maturity_score,
        row.last_update_score,
        row.trusted_org_bonus,
    )

    assert row.total_score == sum(component or 0.0 for component in components)
    assert (row.total_score or 0.0) <= MAX_TOTAL_SCORE


@pytest.mark.requirement("L3-ROW-001")
def test_every_score_is_a_float() -> None:
    row = build_row(REFERENCE, metadata(), SCAN)

    for name in (
        "prevalence_score",
        "stars_score",
        "forks_score",
        "maturity_score",
        "last_update_score",
        "trusted_org_bonus",
        "total_score",
    ):
        assert isinstance(getattr(row, name), float), name


@pytest.mark.requirement("L3-ROW-001")
def test_the_run_stamps_every_row_it_produces() -> None:
    row = build_row(REFERENCE, metadata(), SCAN)

    assert row.scan_id == SCAN.scan_id
    assert row.scan_date == SCAN.scan_date


@pytest.mark.requirement("L3-ROW-002")
def test_the_bonus_and_the_column_agree_about_who_is_trusted() -> None:
    """Both resolve through the same registry, so they cannot disagree."""
    trusted = build_row(
        RepositoryRef(owner="google", repoid="guava"),
        metadata(owner="google", name="guava"),
        SCAN,
    )

    assert trusted.is_trusted_org is True
    assert trusted.trusted_org_bonus == 10.0

    untrusted = build_row(REFERENCE, metadata(), SCAN)
    assert untrusted.is_trusted_org is False
    assert untrusted.trusted_org_bonus == 0.0


@pytest.mark.requirement("L3-ROW-002")
def test_a_personally_owned_repository_has_no_organisation() -> None:
    row = build_row(
        RepositoryRef(owner="torvalds", repoid="linux"),
        metadata(owner="torvalds", name="linux", owner_type="User"),
        SCAN,
    )

    assert row.organization == ""
    assert row.owner == "torvalds"  # the reference is echoed verbatim


@pytest.mark.requirement("L3-ROW-002")
def test_the_name_comes_from_the_api_and_the_owner_from_the_input() -> None:
    # `owner` is what someone edits to fix the list; `name` is verified.
    row = build_row(REFERENCE, metadata(name="cline"), SCAN)

    assert row.name == "cline"
    assert row.owner == REFERENCE.owner


# ---------------------------------------------------------------------------
# A repository that could not be collected
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-ROW-003")
def test_an_unreadable_repository_still_says_which_repository_it_was() -> None:
    row = build_empty_row(RepositoryRef(owner="ghost", repoid="missing"), SCAN)

    assert row.name == "missing"
    assert row.owner == "ghost"
    assert row.url == "https://github.com/ghost/missing"
    assert row.scan_id == SCAN.scan_id
    assert row.scan_date == SCAN.scan_date


@pytest.mark.requirement("L3-ROW-003")
def test_nothing_is_scored_as_zero_when_nothing_was_measured() -> None:
    """Empty, not zero.

    Zero is a legitimate score for a repository that was measured and found
    wanting, so using it here would put the unreadable and the inactive in one
    bucket and let every average absorb the difference.
    """
    row = build_empty_row(RepositoryRef(owner="ghost", repoid="missing"), SCAN)

    measured = set(ALL_FIELDS) - {"name", "owner", "url", "scan_date", "scan_id"}
    for name in measured:
        assert getattr(row, name) in ("", None), name


@pytest.mark.requirement("L3-ROW-003")
def test_organization_is_empty_because_only_the_api_could_have_filled_it() -> None:
    row = build_empty_row(RepositoryRef(owner="ghost", repoid="missing"), SCAN)

    assert row.organization == ""
