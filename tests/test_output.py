"""Tests for :mod:`github_metrics.model` and :mod:`github_metrics.output`."""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from github_metrics.errors import OutputDestinationError, UnknownFieldError
from github_metrics.model import ScanIdentifier, SoftwareRow
from github_metrics.model.contributor import ContributorBlock
from github_metrics.output import (
    ALL_FIELDS,
    render_console,
    resolve_destination,
    resolve_fields,
    write_csv,
    write_json,
)
from github_metrics.output.destination import DEFAULT_FILENAME, DEFAULT_JSON_FILENAME
from github_metrics.output.fields import split_selection

EXPECTED_HEADER = (
    "name,owner,organization,url,scan_date,scan_id,stars,forks,age_days,"
    "last_update_hours,closed_issues,releases,prevalence_score,stars_score,"
    "forks_score,maturity_score,last_update_score,trusted_org_bonus,total_score,"
    "is_trusted_org"
)

SCAN_DATE = datetime(2026, 7, 12, 20, 33, 7, 254804, tzinfo=timezone.utc)
SCAN_ID = UUID("ca219015-79a4-4bd6-b37e-272fa74bd8c2")


@pytest.fixture
def reference_row() -> SoftwareRow:
    """The worked example from docs/METRICS.md."""
    return SoftwareRow(
        name="cline",
        owner="cline",
        organization="cline",
        url="https://github.com/cline/cline",
        scan_date=SCAN_DATE,
        scan_id=SCAN_ID,
        stars=64574,
        forks=6900,
        age_days=736.5466017006597,
        last_update_hours=8.10177526,
        closed_issues=0,
        releases=825,
        prevalence_score=20.0,
        stars_score=10.0,
        forks_score=15.0,
        maturity_score=12.0,
        last_update_score=15.0,
        trusted_org_bonus=0.0,
        total_score=72.0,
        is_trusted_org=False,
    )


@pytest.fixture
def unfetchable_row() -> SoftwareRow:
    """A repository that could not be read: identity known, nothing measured."""
    return SoftwareRow(
        name="ghost-repo",
        owner="ghost",
        url="https://github.com/ghost/ghost-repo",
        scan_date=SCAN_DATE,
        scan_id=SCAN_ID,
    )


def csv_text(rows: list[SoftwareRow], **kwargs: object) -> str:
    """Render rows to CSV text."""
    buffer = io.StringIO()
    write_csv(rows, buffer, **kwargs)  # type: ignore[arg-type]
    return buffer.getvalue()


def json_text(rows: list[SoftwareRow], **kwargs: object) -> str:
    """Render rows to JSON text."""
    buffer = io.StringIO()
    write_json(rows, buffer, **kwargs)  # type: ignore[arg-type]
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Column definition
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-OUT-001")
def test_the_header_is_the_agreed_column_set_in_the_agreed_order() -> None:
    assert ",".join(ALL_FIELDS) == EXPECTED_HEADER
    assert len(ALL_FIELDS) == 20


EXAMPLE_JSON = Path(__file__).resolve().parents[1] / "docs" / "example.json"


@pytest.mark.requirement("L3-OUT-001")
def test_the_documented_json_example_is_the_row_then_the_block() -> None:
    """`docs/example.json` is the agreed shape for the per-repository JSON.

    The document is every CSV column in canonical order, complete, and then the
    contributor block. Stating it as a prefix and a fixed suffix is the
    strongest form of the rule the two artifacts have to keep: every CSV column
    is a document key, spelled the same way and in the same order, so a row and
    a document join without a translation table.

    The example drifted once already, carrying `prevalance_score` and a
    `trusted_org` string against a `prevalence_score` and an `is_trusted_org`
    boolean in the code, and nothing failed. This is the check.
    """
    example = json.loads(EXAMPLE_JSON.read_text(encoding="utf-8"))

    assert tuple(example)[: len(ALL_FIELDS)] == ALL_FIELDS
    assert tuple(example)[len(ALL_FIELDS) :] == ContributorBlock.keys()
    # The block's keys are the document's alone. A name in both would mean two
    # different things depending on which artifact was being read.
    assert not set(ContributorBlock.keys()) & set(ALL_FIELDS)


@pytest.mark.requirement("L3-OUT-001")
def test_the_documented_json_example_never_geocodes_to_null_island() -> None:
    """0,0 is a real place, so it cannot double as "not resolved".

    A contributor whose location could not be geocoded carries `null`
    coordinates. Zeroes would put the Gulf of Guinea and an unknown address in
    the same bucket, which is the failure the "None, never 0" rule exists to
    prevent - and it survives review, because 0,0 plots.
    """
    example = json.loads(EXAMPLE_JSON.read_text(encoding="utf-8"))

    for contributor in example["contributors"]:
        coordinates = contributor["internal_address"]["internal_location"]
        for axis in ("latitude", "longitude"):
            value = coordinates[axis]
            # A number or nothing. The string "0" would pass a bare `!= 0`
            # while still rendering as Null Island wherever it is plotted.
            assert value is None or isinstance(value, float), (axis, value)
            assert value != 0, (axis, value)


@pytest.mark.requirement("L3-OUT-001")
def test_the_header_is_derived_from_the_dataclass_not_a_parallel_list() -> None:
    # A field added to SoftwareRow must appear in the output automatically;
    # a hand-maintained second list is what lets the two drift apart.
    assert SoftwareRow.to_header() == ALL_FIELDS


@pytest.mark.requirement("L3-OUT-001")
def test_the_reference_row_renders_exactly_as_documented(reference_row: SoftwareRow) -> None:
    rendered = csv_text([reference_row]).splitlines()[1]

    assert rendered == (
        "cline,cline,cline,https://github.com/cline/cline,"
        "2026-07-12 20:33:07.254804+00:00,"
        "ca219015-79a4-4bd6-b37e-272fa74bd8c2,64574,6900,736.5466017006597,"
        "8.10177526,0,825,20.0,10.0,15.0,12.0,15.0,0.0,72.0,false"
    )


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-OUT-002")
def test_csv_writes_a_header_even_with_no_rows() -> None:
    text = csv_text([])

    # A consumer always receives a well-formed file, and can tell "no
    # repositories" from "the run produced nothing at all".
    assert text == EXPECTED_HEADER + "\n"


@pytest.mark.requirement("L3-OUT-002")
def test_csv_preserves_the_order_it_was_given(
    reference_row: SoftwareRow, unfetchable_row: SoftwareRow
) -> None:
    first = csv_text([reference_row, unfetchable_row]).splitlines()
    second = csv_text([unfetchable_row, reference_row]).splitlines()

    assert first[1].startswith("cline")
    assert second[1].startswith("ghost")


@pytest.mark.requirement("L3-OUT-002")
def test_csv_uses_lf_regardless_of_platform(reference_row: SoftwareRow) -> None:
    assert "\r\n" not in csv_text([reference_row])


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-OUT-003")
def test_json_is_an_array_of_objects_keyed_by_column_name(
    reference_row: SoftwareRow,
) -> None:
    payload = json.loads(json_text([reference_row]))

    assert isinstance(payload, list)
    assert list(payload[0]) == list(ALL_FIELDS)


@pytest.mark.requirement("L3-OUT-003")
def test_json_keeps_native_types_rather_than_stringifying(
    reference_row: SoftwareRow,
) -> None:
    entry = json.loads(json_text([reference_row]))[0]

    assert entry["stars"] == 64574
    assert entry["total_score"] == 72.0
    assert entry["is_trusted_org"] is False
    # Only the types JSON cannot express become strings.
    assert entry["scan_id"] == str(SCAN_ID)
    assert entry["scan_date"] == "2026-07-12 20:33:07.254804+00:00"


@pytest.mark.requirement("L3-OUT-003")
def test_json_repeats_the_run_identity_in_every_object(
    reference_row: SoftwareRow, unfetchable_row: SoftwareRow
) -> None:
    payload = json.loads(json_text([reference_row, unfetchable_row]))

    # The same shape as the CSV, so a consumer needs no mapping layer.
    assert {entry["scan_id"] for entry in payload} == {str(SCAN_ID)}
    assert {entry["scan_date"] for entry in payload} == {str(SCAN_DATE)}


# ---------------------------------------------------------------------------
# Unknown values
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-OUT-004")
def test_an_unfetchable_row_keeps_its_identity_and_empties_the_rest(
    unfetchable_row: SoftwareRow,
) -> None:
    cells = csv_text([unfetchable_row]).splitlines()[1].split(",")

    # `organization` is reported by the API, so an unreadable repository has
    # none. The columns that say which repository this was still survive,
    # `url` among them: it is built from `owner` and `name` rather than
    # reported, so it is exactly as knowable as they are.
    assert cells[:4] == ["ghost-repo", "ghost", "", "https://github.com/ghost/ghost-repo"]
    assert cells[4] == str(SCAN_DATE)
    assert cells[5] == str(SCAN_ID)
    assert all(cell == "" for cell in cells[6:])


@pytest.mark.requirement("L3-OUT-004")
def test_not_collected_is_distinguishable_from_collected_zero() -> None:
    measured = SoftwareRow(owner="a", stars=0, closed_issues=0)
    unmeasured = SoftwareRow(owner="b")

    measured_cells = csv_text([measured], columns=["stars", "closed_issues"]).splitlines()[1]
    unmeasured_cells = csv_text([unmeasured], columns=["stars", "closed_issues"]).splitlines()[1]

    # This is the whole reason the fields default to None rather than 0.
    assert measured_cells == "0,0"
    assert unmeasured_cells == ","


@pytest.mark.requirement("L3-OUT-004")
def test_unknown_values_are_null_in_json_not_zero(unfetchable_row: SoftwareRow) -> None:
    entry = json.loads(json_text([unfetchable_row]))[0]

    assert entry["stars"] is None
    assert entry["total_score"] is None
    assert entry["is_trusted_org"] is None


@pytest.mark.requirement("L3-OUT-005")
@pytest.mark.parametrize(("value", "expected"), [(True, "true"), (False, "false")])
def test_booleans_render_lowercase(value: bool, expected: str) -> None:
    row = SoftwareRow(owner="a", is_trusted_org=value)

    assert csv_text([row], columns=["is_trusted_org"]).splitlines()[1] == expected


@pytest.mark.requirement("L3-OUT-005")
def test_floats_keep_full_precision() -> None:
    row = SoftwareRow(owner="a", age_days=736.5466017006597)

    assert csv_text([row], columns=["age_days"]).splitlines()[1] == "736.5466017006597"


# ---------------------------------------------------------------------------
# Field selection
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-OUT-006")
@pytest.mark.parametrize("selection", [None, [], ["", "  "]])
def test_selecting_nothing_selects_everything(selection: list[str] | None) -> None:
    assert resolve_fields(selection) == ALL_FIELDS


@pytest.mark.requirement("L3-OUT-006")
def test_selection_is_returned_in_canonical_order_not_the_order_given() -> None:
    # Two runs asking for the same columns must produce identical headers, so
    # a diff between them shows changed data rather than reordered columns.
    assert resolve_fields(["total_score", "owner", "stars"]) == (
        "owner",
        "stars",
        "total_score",
    )


@pytest.mark.requirement("L3-OUT-006")
def test_selection_tolerates_case_padding_and_duplicates() -> None:
    assert resolve_fields([" OWNER ", "owner", "Stars"]) == ("owner", "stars")


@pytest.mark.requirement("L3-OUT-007")
def test_an_unknown_field_is_rejected_with_its_code() -> None:
    with pytest.raises(UnknownFieldError) as caught:
        resolve_fields(["stars", "not_a_field"])

    message = str(caught.value)
    assert "GM-OUT-001" in message
    assert "not_a_field" in message


@pytest.mark.requirement("L3-OUT-007")
def test_a_near_miss_gets_a_suggestion() -> None:
    with pytest.raises(UnknownFieldError) as caught:
        resolve_fields(["star_score"])

    # These are typed by hand, so naming the closest match saves a round trip
    # to the documentation.
    assert "did you mean 'stars_score'?" in str(caught.value)


@pytest.mark.requirement("L3-OUT-006")
def test_a_command_line_field_list_splits_forgivingly() -> None:
    assert split_selection("stars, forks ,total_score,") == [
        "stars",
        "forks",
        "total_score",
    ]


@pytest.mark.requirement("L3-OUT-006")
def test_selection_applies_to_every_format(reference_row: SoftwareRow) -> None:
    columns = ["owner", "stars"]

    assert csv_text([reference_row], columns=columns).splitlines()[0] == "owner,stars"
    assert list(json.loads(json_text([reference_row], columns=columns))[0]) == columns
    assert "forks" not in render_console([reference_row], columns=columns)


# ---------------------------------------------------------------------------
# Console
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-OUT-008")
def test_console_renders_vertically_one_label_per_line(
    reference_row: SoftwareRow,
) -> None:
    rendered = render_console([reference_row], columns=["owner", "stars", "total_score"])

    assert rendered.splitlines() == [
        "owner        cline",
        "stars        64574",
        "total_score  72.0",
    ]


@pytest.mark.requirement("L3-OUT-008")
def test_console_separates_repositories(
    reference_row: SoftwareRow, unfetchable_row: SoftwareRow
) -> None:
    rendered = render_console([reference_row, unfetchable_row], columns=["owner"])

    assert "cline" in rendered
    assert "ghost" in rendered
    assert "---" in rendered


@pytest.mark.requirement("L3-OUT-008")
def test_console_marks_an_unknown_value_rather_than_leaving_a_gap(
    unfetchable_row: SoftwareRow,
) -> None:
    rendered = render_console([unfetchable_row], columns=["stars"])

    # A trailing blank would be indistinguishable from a rendering bug.
    assert rendered == "stars  -"


@pytest.mark.requirement("L3-OUT-008")
def test_console_says_so_when_there_is_nothing_to_show() -> None:
    assert render_console([]) == "no repositories"


# ---------------------------------------------------------------------------
# Destination
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-OUT-009")
def test_no_destination_means_the_console() -> None:
    assert resolve_destination(None) is None


@pytest.mark.requirement("L3-OUT-009")
def test_a_directory_gets_the_default_filename(tmp_path: Path) -> None:
    assert resolve_destination(tmp_path) == tmp_path / DEFAULT_FILENAME
    assert resolve_destination(tmp_path, json_format=True) == tmp_path / DEFAULT_JSON_FILENAME


@pytest.mark.requirement("L3-OUT-009")
def test_a_trailing_separator_reads_as_a_directory_even_if_absent(tmp_path: Path) -> None:
    target = f"{tmp_path}/"

    assert resolve_destination(target) == tmp_path / DEFAULT_FILENAME


@pytest.mark.requirement("L3-OUT-009")
def test_a_named_file_is_used_as_given(tmp_path: Path) -> None:
    assert resolve_destination(tmp_path / "custom.csv") == tmp_path / "custom.csv"


@pytest.mark.requirement("L3-OUT-009")
def test_a_missing_parent_directory_fails_early(tmp_path: Path) -> None:
    """Better here than after a run has spent its API budget.

    The *reason* is asserted, not only the code. Both of the guards in
    `resolve_destination` raise `GM-OUT-002`, so a test that checks the code
    alone passes when either fires - which left the missing-directory branch
    unverified: deleting it entirely kept this test green, because the
    is-not-a-directory branch below caught the same input and said something
    else.
    """
    with pytest.raises(OutputDestinationError) as caught:
        resolve_destination(tmp_path / "nope" / "out.csv")

    message = str(caught.value)
    assert "GM-OUT-002" in message
    assert "does not exist" in message


@pytest.mark.requirement("L3-OUT-009")
def test_a_parent_that_is_a_file_is_refused_with_its_own_reason(tmp_path: Path) -> None:
    """The other guard, which had no test of its own.

    A parent that exists but is a file is a different mistake from one that is
    absent, and the operator fixes it differently.
    """
    occupied = tmp_path / "occupied"
    occupied.write_text("not a directory", encoding="utf-8")

    with pytest.raises(OutputDestinationError) as caught:
        resolve_destination(occupied / "out.csv")

    message = str(caught.value)
    assert "GM-OUT-002" in message
    assert "is not a directory" in message


# ---------------------------------------------------------------------------
# Scan identity
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-OUT-010")
def test_a_scan_identifier_is_timezone_aware_and_unique() -> None:
    first = ScanIdentifier()
    second = ScanIdentifier()

    assert first.scan_date.tzinfo is not None
    assert first.scan_id != second.scan_id


@pytest.mark.requirement("L3-OUT-010")
def test_a_naive_scan_date_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        # Naive on purpose: this is the value the constructor exists to
        # refuse. Suppressed at the line rather than for the file, so an
        # accidental naive datetime elsewhere still fails.
        ScanIdentifier(scan_date=datetime(2026, 7, 12, 20, 33, 7))  # noqa: DTZ001


@pytest.mark.requirement("L3-OUT-010")
def test_one_identity_stamps_every_row_of_a_run() -> None:
    scan = ScanIdentifier()
    rows = [
        SoftwareRow(owner=name, scan_id=scan.scan_id, scan_date=scan.scan_date)
        for name in ("a", "b", "c")
    ]

    assert {row.scan_id for row in rows} == {scan.scan_id}
    assert {row.scan_date for row in rows} == {scan.scan_date}
