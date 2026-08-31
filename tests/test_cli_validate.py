"""Tests for the `github-metrics validate` command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from github_metrics.cli import EXIT_INPUT_UNREADABLE, EXIT_ROWS_REJECTED, main

DATA = Path(__file__).parent / "data"


@pytest.mark.requirement("L3-CLI-001")
def test_validate_lists_every_repository_it_read() -> None:
    result = CliRunner().invoke(main, ["validate", str(DATA / "repositories.csv")])

    assert result.exit_code == 0
    assert "urllib3/urllib3" in result.output
    assert "bokeh/bokeh" in result.output
    assert "pypa/virtualenv" in result.output


@pytest.mark.requirement("L3-CLI-002")
def test_validate_needs_no_github_token(empty_env_file: Path) -> None:
    # No GITHUB_TOKEN is set (the clean_env fixture removes it) and the env
    # file is empty. Ingestion touches no network, so it must still succeed
    # where `rate-limit` would refuse to run.
    result = CliRunner().invoke(
        main,
        ["--env-file", str(empty_env_file), "validate", str(DATA / "repositories.csv")],
    )

    assert result.exit_code == 0

    refused = CliRunner().invoke(main, ["--env-file", str(empty_env_file), "rate-limit"])
    assert refused.exit_code != 0
    assert "GITHUB_TOKEN" in refused.output


@pytest.mark.requirement("L3-CLI-003")
def test_json_output_is_machine_readable() -> None:
    result = CliRunner().invoke(
        main, ["validate", str(DATA / "repositories.csv"), "--format", "json"]
    )

    assert result.exit_code == 0
    # Logging goes to stderr, so the captured stdout must parse on its own.
    payload = json.loads(result.stdout)
    # One document per run, not one per file: the sources are an input
    # detail, and a consumer wants the references in the order asked for.
    assert [entry["owner"] for entry in payload["repositories"]] == [
        "urllib3",
        "bokeh",
        "pypa",
    ]


@pytest.mark.requirement("L3-CLI-004")
def test_rejected_rows_produce_a_distinct_exit_status() -> None:
    result = CliRunner().invoke(main, ["validate", str(DATA / "invalid-rows.csv")])

    # The file was read and usable rows were kept, so this is neither success
    # nor an unreadable input.
    assert result.exit_code == EXIT_ROWS_REJECTED
    assert "bokeh/bokeh" in result.output
    assert "GM-ING-013" in result.output


@pytest.mark.requirement("L3-CLI-004")
def test_an_unreadable_file_produces_its_own_exit_status(tmp_path: Path) -> None:
    result = CliRunner().invoke(main, ["validate", str(tmp_path / "absent.csv")])

    assert result.exit_code == EXIT_INPUT_UNREADABLE
    assert "GM-ING-001" in result.output


@pytest.mark.requirement("L3-CLI-004")
def test_a_clean_file_exits_zero() -> None:
    result = CliRunner().invoke(main, ["validate", str(DATA / "messy.csv")])

    assert result.exit_code == 0


@pytest.mark.requirement("L3-CLI-001")
def test_several_files_are_summarised_together() -> None:
    """One report for the run, not one per file.

    These two fixtures both name `pypa/virtualenv`, which the per-file reader
    cannot see. Collecting it twice would spend the rate limit twice and put
    two identical rows in the output, so the repetition is refused and named
    against the file that already had it.
    """
    result = CliRunner().invoke(
        main,
        [
            "validate",
            str(DATA / "repositories.csv"),
            str(DATA / "with-bom.csv"),
            "--workers",
            "2",
        ],
    )

    assert result.exit_code == EXIT_ROWS_REJECTED
    assert "3 repositories, 1 rejected, from 2 file(s)" in result.output
    assert "was already named by" in result.output


@pytest.mark.requirement("L3-CLI-003")
def test_output_can_be_written_to_a_file(tmp_path: Path) -> None:
    destination = tmp_path / "report.json"

    result = CliRunner().invoke(
        main,
        [
            "validate",
            str(DATA / "repositories.csv"),
            "--format",
            "json",
            "--output",
            str(destination),
        ],
    )

    assert result.exit_code == 0
    assert f"Wrote {destination}" in result.output
    assert len(json.loads(destination.read_text(encoding="utf-8"))["repositories"]) == 3


@pytest.mark.requirement("L3-CLI-004")
def test_strict_mode_reports_the_first_bad_row_and_stops() -> None:
    result = CliRunner().invoke(main, ["validate", str(DATA / "invalid-rows.csv"), "--strict"])

    assert result.exit_code == EXIT_INPUT_UNREADABLE
    assert "strict mode" in result.output
    # Nothing is reported as loaded, because the read did not complete.
    assert "bokeh/bokeh" not in result.output


@pytest.mark.requirement("L3-CLI-001")
def test_validate_requires_at_least_one_source() -> None:
    result = CliRunner().invoke(main, ["validate"])

    # Click's own usage error, which is exit code 2 by convention.
    assert result.exit_code != 0
    assert "Usage:" in result.output


@pytest.mark.requirement("L3-CLI-001")
def test_validate_appears_in_the_command_list() -> None:
    result = CliRunner().invoke(main, ["-h"])

    assert "validate" in result.output
