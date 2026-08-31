"""Tests for :mod:`github_metrics.sources.csv_inventory`."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from github_metrics.errors import (
    ISSUE_DUPLICATE,
    ISSUE_EMPTY_OWNER,
    ISSUE_EMPTY_REPOID,
    ISSUE_FIELD_COUNT,
    ISSUE_INVALID_OWNER,
    ISSUE_INVALID_REPOID,
    HeaderError,
    IngestError,
    MalformedCsvError,
    SourceDecodeError,
    SourceEmptyError,
    SourceNotFoundError,
    SourceUnreadableError,
    StrictModeError,
)
from github_metrics.sources.csv_inventory import (
    IngestResult,
    RepositoryRef,
    read_repository_csv,
    read_repository_csvs,
)

DATA = Path(__file__).parent / "data"


def codes(result: IngestResult) -> list[str]:
    """Issue codes from a result, in the order encountered."""
    return [issue.code for issue in result.issues]


# ---------------------------------------------------------------------------
# The documented happy path
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-ING-001")
def test_reads_the_documented_example() -> None:
    result = read_repository_csv(DATA / "repositories.csv")

    assert [reference.full_name for reference in result.repositories] == [
        "urllib3/urllib3",
        "bokeh/bokeh",
        "pypa/virtualenv",
    ]
    assert result.ok
    assert result.rows_read == 3
    assert result.rejected == 0


@pytest.mark.requirement("L3-ING-001")
def test_each_row_becomes_a_github_url() -> None:
    result = read_repository_csv(DATA / "repositories.csv")

    assert [reference.url for reference in result.repositories] == [
        "https://github.com/urllib3/urllib3",
        "https://github.com/bokeh/bokeh",
        "https://github.com/pypa/virtualenv",
    ]


@pytest.mark.requirement("L3-ING-001")
def test_source_line_is_recorded_for_every_reference() -> None:
    result = read_repository_csv(DATA / "repositories.csv")

    # Line 1 is the header, so data starts at line 2.
    assert [reference.source_line for reference in result.repositories] == [2, 3, 4]


# ---------------------------------------------------------------------------
# Header tolerance
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-ING-002")
def test_a_byte_order_mark_does_not_hide_the_header() -> None:
    result = read_repository_csv(DATA / "with-bom.csv")

    assert [reference.full_name for reference in result.repositories] == ["pypa/virtualenv"]


@pytest.mark.requirement("L3-ING-003")
def test_crlf_line_endings_are_accepted() -> None:
    result = read_repository_csv(DATA / "crlf.csv")

    assert [reference.full_name for reference in result.repositories] == [
        "urllib3/urllib3",
        "bokeh/bokeh",
    ]


@pytest.mark.requirement("L3-ING-004")
def test_columns_may_be_reordered_recased_and_padded() -> None:
    result = read_repository_csv(DATA / "reordered-columns.csv")

    assert [reference.full_name for reference in result.repositories] == [
        "pypa/virtualenv",
        "urllib3/urllib3",
    ]


@pytest.mark.requirement("L3-ING-004")
def test_unrecognised_columns_are_ignored() -> None:
    result = read_repository_csv(DATA / "extra-columns.csv")

    assert [reference.full_name for reference in result.repositories] == ["pypa/virtualenv"]
    assert result.ok


@pytest.mark.requirement("L3-ING-005")
def test_blank_lines_and_padding_are_tolerated() -> None:
    result = read_repository_csv(DATA / "messy.csv")

    assert [reference.full_name for reference in result.repositories] == [
        "urllib3/urllib3",
        "bokeh/bokeh",
    ]
    # Blank lines and the trailing ",," are skipped, not counted or reported.
    assert result.rows_read == 2
    assert result.ok


# ---------------------------------------------------------------------------
# Duplicates
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-ING-006")
def test_duplicates_are_dropped_case_insensitively_keeping_the_first() -> None:
    result = read_repository_csv(DATA / "duplicates.csv")

    assert [reference.full_name for reference in result.repositories] == [
        "pypa/virtualenv",
        "bokeh/bokeh",
    ]
    assert codes(result) == [ISSUE_DUPLICATE, ISSUE_DUPLICATE]
    # The first occurrence is named so the reader can find the pair.
    assert "already appears on line 2" in result.issues[0].message


@pytest.mark.requirement("L3-ING-006")
def test_repository_ref_identity_folds_case() -> None:
    assert RepositoryRef("PyPA", "VirtualEnv").key == RepositoryRef("pypa", "virtualenv").key


# ---------------------------------------------------------------------------
# Row-level rejections
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-ERR-002")
def test_every_row_rejection_kind_is_reported_and_the_good_row_survives() -> None:
    result = read_repository_csv(DATA / "invalid-rows.csv")

    assert [reference.full_name for reference in result.repositories] == ["bokeh/bokeh"]
    assert codes(result) == [
        ISSUE_FIELD_COUNT,
        ISSUE_EMPTY_OWNER,
        ISSUE_EMPTY_REPOID,
        ISSUE_INVALID_OWNER,
        ISSUE_INVALID_REPOID,
    ]
    assert not result.ok
    assert result.rows_read == 6
    assert result.rejected == 5


@pytest.mark.requirement("L3-ERR-002")
def test_issues_carry_the_line_number_and_render_like_a_compiler_diagnostic() -> None:
    result = read_repository_csv(DATA / "invalid-rows.csv")

    field_count = result.issues[0]
    assert field_count.line == 3
    rendered = str(field_count)
    assert "invalid-rows.csv:3" in rendered
    assert ISSUE_FIELD_COUNT in rendered


@pytest.mark.requirement("L3-LOG-001")
def test_row_issues_are_logged_at_debug(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger="github_metrics.sources.csv_inventory"):
        read_repository_csv(DATA / "invalid-rows.csv")

    assert ISSUE_INVALID_REPOID in caplog.text


# ---------------------------------------------------------------------------
# Strict mode
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-ERR-003")
def test_strict_mode_aborts_on_the_first_bad_row() -> None:
    with pytest.raises(StrictModeError) as caught:
        read_repository_csv(DATA / "invalid-rows.csv", strict=True)

    assert ISSUE_FIELD_COUNT in str(caught.value)
    assert "strict mode" in str(caught.value)


@pytest.mark.requirement("L3-ERR-003")
def test_strict_mode_accepts_a_clean_file_unchanged() -> None:
    lenient = read_repository_csv(DATA / "repositories.csv")
    strict = read_repository_csv(DATA / "repositories.csv", strict=True)

    assert strict.repositories == lenient.repositories


@pytest.mark.requirement("L3-ERR-003")
def test_a_duplicate_alone_is_enough_to_trip_strict_mode() -> None:
    with pytest.raises(StrictModeError) as caught:
        read_repository_csv(DATA / "duplicates.csv", strict=True)

    assert ISSUE_DUPLICATE in str(caught.value)


# ---------------------------------------------------------------------------
# File-level failures
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-ERR-001")
def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(SourceNotFoundError) as caught:
        read_repository_csv(tmp_path / "absent.csv")

    assert "GM-ING-001" in str(caught.value)


@pytest.mark.requirement("L3-ERR-001")
def test_a_directory_given_instead_of_a_file_raises(tmp_path: Path) -> None:
    with pytest.raises(SourceUnreadableError) as caught:
        read_repository_csv(tmp_path)

    assert "GM-ING-002" in str(caught.value)


@pytest.mark.requirement("L3-ERR-001")
@pytest.mark.parametrize("name", ["empty.csv", "blank-lines-only.csv"])
def test_a_file_with_no_header_raises(name: str) -> None:
    with pytest.raises(SourceEmptyError) as caught:
        read_repository_csv(DATA / name)

    assert "GM-ING-003" in str(caught.value)


@pytest.mark.requirement("L3-ERR-001")
def test_a_header_missing_required_columns_raises() -> None:
    with pytest.raises(HeaderError) as caught:
        read_repository_csv(DATA / "bad-header.csv")

    message = str(caught.value)
    assert "GM-ING-004" in message
    # The message must name both what was wanted and what was found, or the
    # reader has to open the file to learn anything.
    assert "'owner'" in message
    assert "organisation" in message


@pytest.mark.requirement("L3-ERR-001")
def test_a_header_declaring_a_column_twice_raises() -> None:
    with pytest.raises(HeaderError) as caught:
        read_repository_csv(DATA / "duplicate-column.csv")

    assert "more than once" in str(caught.value)


@pytest.mark.requirement("L3-ERR-001")
def test_a_file_that_is_not_utf8_raises() -> None:
    with pytest.raises(SourceDecodeError) as caught:
        read_repository_csv(DATA / "not-utf8.csv")

    assert "GM-ING-005" in str(caught.value)


@pytest.mark.requirement("L3-ERR-001")
def test_a_file_containing_a_nul_byte_raises() -> None:
    with pytest.raises(MalformedCsvError) as caught:
        read_repository_csv(DATA / "nul-byte.csv")

    assert "GM-ING-006" in str(caught.value)


@pytest.mark.requirement("L3-ERR-004")
def test_every_ingest_failure_shares_one_base_class() -> None:
    # A caller that wants "any ingestion problem" needs exactly one except.
    with pytest.raises(IngestError):
        read_repository_csv(DATA / "bad-header.csv")


@pytest.mark.requirement("L3-ERR-004")
def test_error_codes_are_unique_across_the_taxonomy() -> None:
    classes = [
        SourceNotFoundError,
        SourceUnreadableError,
        SourceEmptyError,
        HeaderError,
        SourceDecodeError,
        MalformedCsvError,
        StrictModeError,
    ]
    seen = [cls.code for cls in classes]

    assert len(seen) == len(set(seen))


# ---------------------------------------------------------------------------
# Concurrent multi-file reads
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-CON-001")
def test_multiple_files_are_read_and_returned_in_input_order() -> None:
    sources = [DATA / "repositories.csv", DATA / "with-bom.csv", DATA / "crlf.csv"]

    results = read_repository_csvs(sources)

    assert [Path(result.source).name for result in results] == [
        "repositories.csv",
        "with-bom.csv",
        "crlf.csv",
    ]
    assert [result.accepted for result in results] == [3, 1, 2]


@pytest.mark.requirement("L3-CON-001")
def test_reading_no_files_starts_no_work() -> None:
    results = read_repository_csvs([])

    assert isinstance(results, list)
    assert not results


@pytest.mark.requirement("L3-CON-002")
def test_the_reported_error_is_the_earliest_source_not_the_fastest_thread() -> None:
    # Both sources fail. Ordering by input rather than by completion is what
    # keeps the reported error the same on every run.
    sources = [DATA / "bad-header.csv", DATA / "not-utf8.csv"]

    with pytest.raises(HeaderError):
        read_repository_csvs(sources)

    with pytest.raises(SourceDecodeError):
        read_repository_csvs(list(reversed(sources)))


@pytest.mark.requirement("L3-CON-002")
def test_results_are_stable_across_repeated_runs() -> None:
    sources = [DATA / "repositories.csv", DATA / "duplicates.csv", DATA / "messy.csv"]

    first = read_repository_csvs(sources)
    second = read_repository_csvs(sources)

    assert [r.source for r in first] == [r.source for r in second]
    assert [r.repositories for r in first] == [r.repositories for r in second]


@pytest.mark.requirement("L3-CON-003")
@pytest.mark.parametrize("workers", [1, 2, 16])
def test_the_worker_count_changes_nothing_about_the_answer(workers: int) -> None:
    sources = [DATA / "repositories.csv", DATA / "duplicates.csv"]

    results = read_repository_csvs(sources, max_workers=workers)

    assert [result.accepted for result in results] == [3, 2]


@pytest.mark.requirement("L3-CON-003")
def test_more_workers_than_files_is_harmless() -> None:
    results = read_repository_csvs([DATA / "repositories.csv"], max_workers=64)

    assert results[0].accepted == 3


@pytest.mark.requirement("L3-CON-001")
def test_strict_mode_propagates_through_the_pool() -> None:
    with pytest.raises(StrictModeError):
        read_repository_csvs([DATA / "repositories.csv", DATA / "duplicates.csv"], strict=True)


# ---------------------------------------------------------------------------
# Scale
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-ING-007")
def test_a_large_inventory_reads_without_special_handling(tmp_path: Path) -> None:
    source = tmp_path / "large.csv"
    rows = "\n".join(f"owner{i},repo{i}" for i in range(5_000))
    source.write_text(f"owner,repoid\n{rows}\n", encoding="utf-8")

    result = read_repository_csv(source)

    assert result.accepted == 5_000
    assert result.ok
    assert result.repositories[-1].full_name == "owner4999/repo4999"


@pytest.mark.requirement("L3-ING-007")
def test_a_quoted_field_containing_a_separator_is_one_cell(tmp_path: Path) -> None:
    source = tmp_path / "quoted.csv"
    source.write_text('owner,repoid\n"pypa,inc",virtualenv\n', encoding="utf-8")

    result = read_repository_csv(source)

    # The comma stays inside the cell, so this is an invalid owner rather than
    # an extra field. Getting that wrong would silently shift every column.
    assert codes(result) == [ISSUE_INVALID_OWNER]
    assert "pypa,inc" in result.issues[0].message
