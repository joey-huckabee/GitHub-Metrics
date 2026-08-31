"""Tests for the `github-metrics metrics` command."""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from github_metrics.cli import EXIT_REPOSITORY_UNFETCHABLE, EXIT_ROWS_REJECTED, main
from github_metrics.collect.budget import Budget
from github_metrics.collect.repository import RepoMetaData
from github_metrics.collect.runner import Outcome
from github_metrics.collect.timestamps import RepositoryTimestamps
from github_metrics.errors import RateLimitExhaustedError, RepositoryNotFoundError
from github_metrics.sources import RepositoryRef

DATA = Path(__file__).parent / "data"


def metadata(reference: RepositoryRef) -> RepoMetaData:
    """Plausible collected metadata for any reference."""
    return RepoMetaData(
        owner=reference.owner,
        repoid=reference.repoid,
        resolved_owner=reference.owner,
        resolved_name=reference.repoid,
        owner_type="Organization",
        stars=5041,
        forks=1114,
        timestamps=RepositoryTimestamps(
            created_at=datetime(2011, 2, 16, tzinfo=timezone.utc),
            updated_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
            pushed_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
        ),
        closed_issues=1429,
        open_issues=0,
        issues_enabled=True,
        releases=98,
        tags=285,
    )


def affordable(_client: Any, count: int) -> Budget:
    """A budget that always fits, so a test never depends on a real one."""
    return Budget(repositories=count, required=count + 10, available=5000)


class _NullClient:
    """Stands in for a real client, without a socket in sight."""

    def __enter__(self) -> _NullClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> list[RepositoryRef]:
    """Collect every reference successfully, without a network or a token.

    Returns the list the fake collector was asked for, so a test can assert
    what a run would have spent.
    """
    asked: list[RepositoryRef] = []

    def fake_collect(_client: Any, references: Any, **_kwargs: Any) -> list[Outcome]:
        asked.extend(references)
        return [
            Outcome(reference=reference, metadata=metadata(reference)) for reference in references
        ]

    monkeypatch.setattr("github_metrics.cli.verify_credentials", lambda _settings: None)
    monkeypatch.setattr("github_metrics.cli.GitHubClient", lambda _settings: _NullClient())
    monkeypatch.setattr("github_metrics.cli.check_budget", affordable)
    monkeypatch.setattr("github_metrics.cli.collect_all", fake_collect)
    return asked


def run(*args: str) -> Any:
    """Invoke the CLI with a token supplied, against no real environment."""
    return CliRunner().invoke(
        main, ["--env-file", os.devnull, "--token", "ghp_x", "metrics", *args]
    )


# ---------------------------------------------------------------------------
# The file it produces
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-CLI-008")
@pytest.mark.usefixtures("offline")
def test_a_csv_is_written_with_one_row_per_reference(tmp_path: Path) -> None:
    destination = tmp_path / "out.csv"

    result = run("pypa/virtualenv", "urllib3/urllib3", "--output", str(destination))

    assert result.exit_code == 0
    rows = list(csv.DictReader(destination.read_text(encoding="utf-8").splitlines()))
    assert [row["repo_name"] for row in rows] == ["virtualenv", "urllib3"]
    assert rows[0]["total_score"] == "75.0"


@pytest.mark.requirement("L3-CLI-008")
@pytest.mark.usefixtures("offline")
def test_a_directory_gets_the_default_filename(tmp_path: Path) -> None:
    result = run("pypa/virtualenv", "--output", str(tmp_path))

    assert result.exit_code == 0
    assert (tmp_path / "githubmetrics.csv").is_file()


@pytest.mark.requirement("L3-CLI-008")
@pytest.mark.usefixtures("offline")
def test_no_output_renders_to_the_console(tmp_path: Path) -> None:
    del tmp_path
    result = run("pypa/virtualenv")

    # Vertical, because nineteen columns do not fit across a terminal.
    assert result.exit_code == 0
    assert "repo_name" in result.output
    assert "total_score" in result.output


@pytest.mark.requirement("L3-CLI-008")
@pytest.mark.usefixtures("offline")
def test_json_is_available_as_a_file_and_to_the_console(tmp_path: Path) -> None:
    printed = run("pypa/virtualenv", "--format", "json")
    assert printed.exit_code == 0
    assert json.loads(printed.stdout)[0]["repo_name"] == "virtualenv"

    destination = tmp_path / "out.json"
    written = run("pypa/virtualenv", "--format", "json", "--output", str(destination))
    assert written.exit_code == 0
    assert json.loads(destination.read_text(encoding="utf-8"))[0]["owner"] == "pypa"


@pytest.mark.requirement("L3-CLI-008")
@pytest.mark.usefixtures("offline")
def test_fields_selects_columns_in_canonical_order(tmp_path: Path) -> None:
    destination = tmp_path / "out.csv"

    result = run(
        "pypa/virtualenv",
        "--fields",
        "total_score,owner",
        "--output",
        str(destination),
    )

    assert result.exit_code == 0
    header = destination.read_text(encoding="utf-8").splitlines()[0]
    # Canonical order, not the order asked for: two runs wanting the same
    # columns should produce identical headers.
    assert header == "owner,total_score"


@pytest.mark.requirement("L3-CLI-008")
@pytest.mark.usefixtures("offline")
def test_the_sources_are_the_same_ones_validate_takes(tmp_path: Path) -> None:
    destination = tmp_path / "out.csv"

    result = run(
        str(DATA / "repositories.csv"),
        "cline/cline",
        "https://github.com/psf/requests",
        "--output",
        str(destination),
    )

    assert result.exit_code == 0
    rows = list(csv.DictReader(destination.read_text(encoding="utf-8").splitlines()))
    assert [row["owner"] for row in rows] == [
        "urllib3",
        "bokeh",
        "pypa",
        "cline",
        "psf",
    ]


# ---------------------------------------------------------------------------
# What happens when something is wrong
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-CLI-009")
def test_an_unreadable_repository_still_gets_a_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def half(_client: Any, references: Any, **_kwargs: Any) -> list[Outcome]:
        return [
            (
                Outcome(reference=reference, metadata=metadata(reference))
                if reference.owner != "ghost"
                else Outcome(
                    reference=reference,
                    error=RepositoryNotFoundError(f"{reference.full_name}: not found"),
                )
            )
            for reference in references
        ]

    monkeypatch.setattr("github_metrics.cli.verify_credentials", lambda _settings: None)
    monkeypatch.setattr("github_metrics.cli.GitHubClient", lambda _settings: _NullClient())
    monkeypatch.setattr("github_metrics.cli.check_budget", affordable)
    monkeypatch.setattr("github_metrics.cli.collect_all", half)

    destination = tmp_path / "out.csv"
    result = run("pypa/virtualenv", "ghost/missing", "--output", str(destination))

    assert result.exit_code == EXIT_REPOSITORY_UNFETCHABLE
    rows = list(csv.DictReader(destination.read_text(encoding="utf-8").splitlines()))
    assert len(rows) == 2
    # Identity kept, measurements empty. Empty rather than zero.
    assert rows[1]["repo_name"] == "missing"
    assert rows[1]["owner"] == "ghost"
    assert rows[1]["stars"] == ""
    assert rows[1]["total_score"] == ""
    # And the run says which one, by name, rather than leaving it to be spotted.
    assert "! ghost/missing" in result.stderr


@pytest.mark.requirement("L3-CLI-009")
@pytest.mark.usefixtures("offline")
def test_a_rejected_reference_is_a_lesser_status_than_an_unreadable_one(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "out.csv"

    result = run("pypa/virtualenv", "notaslug", "--output", str(destination))

    assert result.exit_code == EXIT_ROWS_REJECTED
    assert destination.is_file()


@pytest.mark.requirement("L3-CLI-009")
def test_an_unaffordable_run_spends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pre-flight is the whole point: refusing costs one free request."""
    collected: list[Any] = []

    def refuse(_client: Any, count: int) -> None:
        raise RateLimitExhaustedError(f"{count} repositories need more than remain")

    monkeypatch.setattr("github_metrics.cli.verify_credentials", lambda _settings: None)
    monkeypatch.setattr("github_metrics.cli.GitHubClient", lambda _settings: _NullClient())
    monkeypatch.setattr("github_metrics.cli.check_budget", refuse)

    def record(*args: Any, **kwargs: Any) -> list[Outcome]:
        del kwargs
        collected.append(args)
        return []

    monkeypatch.setattr("github_metrics.cli.collect_all", record)

    result = run("pypa/virtualenv")

    assert result.exit_code != 0
    assert not collected


@pytest.mark.requirement("L3-CLI-009")
@pytest.mark.usefixtures("offline")
def test_a_bad_destination_fails_before_any_quota_is_spent(
    tmp_path: Path, offline: list[RepositoryRef]
) -> None:
    result = run("pypa/virtualenv", "--output", str(tmp_path / "absent" / "out.csv"))

    assert result.exit_code != 0
    assert "does not exist" in result.output
    assert offline == []


@pytest.mark.requirement("L3-CLI-009")
@pytest.mark.usefixtures("offline")
def test_a_run_that_names_nothing_still_produces_a_well_formed_file(
    tmp_path: Path, offline: list[RepositoryRef]
) -> None:
    destination = tmp_path / "out.csv"

    result = run("notaslug", "--output", str(destination))

    assert result.exit_code == EXIT_ROWS_REJECTED
    # A header and no rows: "no repositories" is distinguishable from "the run
    # produced nothing at all".
    assert destination.read_text(encoding="utf-8").splitlines() == [
        "repo_name,owner,organization,scan_date,scan_id,stars,forks,age_days,"
        "last_update_hours,closed_issues,releases,prevalence_score,stars_score,"
        "forks_score,maturity_score,last_update_score,trusted_org_bonus,total_score,"
        "is_trusted_org"
    ]
    assert offline == []


# ---------------------------------------------------------------------------
# The run's identity
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-CLI-008")
@pytest.mark.usefixtures("offline")
def test_every_row_of_one_run_carries_the_same_scan(tmp_path: Path) -> None:
    destination = tmp_path / "out.csv"

    run("pypa/virtualenv", "urllib3/urllib3", "--output", str(destination))

    rows = list(csv.DictReader(destination.read_text(encoding="utf-8").splitlines()))
    assert len({row["scan_id"] for row in rows}) == 1
    assert len({row["scan_date"] for row in rows}) == 1


@pytest.mark.requirement("L3-CLI-008")
@pytest.mark.usefixtures("offline")
def test_two_runs_are_told_apart(tmp_path: Path) -> None:
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"

    run("pypa/virtualenv", "--output", str(first))
    run("pypa/virtualenv", "--output", str(second))

    def scan_id(path: Path) -> str:
        return next(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))["scan_id"]

    assert scan_id(first) != scan_id(second)
