"""Tests for the `github-metrics scan` command.

One run, one scan identity, two artifacts. Most of what is checked here is the
relationship between the two rather than either on its own: they carry the same
`scan_id`, every CSV column is a document key spelled the same way, and they
disagree about which repositories appear only in the one way the design intends
- a repository that could not be fully collected keeps its CSV row and gets no
document.
"""

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
from github_metrics.errors import (
    ContributorCollectionError,
    RateLimitExhaustedError,
    RepositoryNotFoundError,
)
from github_metrics.model.contributor import (
    Address,
    Contributor,
    ContributorBlock,
    Coordinates,
)
from github_metrics.sources import RepositoryRef

DATA = Path(__file__).parent / "data"

EXPECTED_HEADER = (
    "name,owner,organization,url,scan_date,scan_id,stars,forks,age_days,"
    "last_update_hours,closed_issues,releases,prevalence_score,stars_score,"
    "forks_score,maturity_score,last_update_score,trusted_org_bonus,total_score,"
    "is_trusted_org"
)


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


def contributors() -> tuple[Contributor, ...]:
    """Two contributors: one located and resolved, one publishing nothing."""
    return (
        Contributor(
            github_id="7799382",
            name="Saoud Rizwan",
            organization="",
            location="United States",
            internal_address=Address(
                query="United States",
                formatted_address="Decatur County, Kansas, United States",
                street="",
                house_number="",
                suburb="",
                post_code="",
                state="Kansas",
                state_code="US-KS",
                state_district="",
                county="Decatur County",
                country="United States",
                country_code="us",
                city="",
                internal_location=Coordinates(latitude=39.784824, longitude=-100.4458771),
            ),
            contribution=2192,
        ),
        Contributor(github_id="68532117", name="Bee", contribution=834),
    )


def affordable(_client: Any, count: int) -> Budget:
    """A budget that always fits, so a test never depends on a real one."""
    return Budget(
        repositories=count,
        required=count * 2,
        available=5000,
        requests_required=count,
        requests_available=5000,
    )


class _NullClient:
    """Stands in for a real client, without a socket in sight."""

    def __enter__(self) -> _NullClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None


def _patch(monkeypatch: pytest.MonkeyPatch, collector: Any) -> None:
    """Replace everything that would otherwise need a token or a socket."""
    monkeypatch.setattr("github_metrics.cli.verify_credentials", lambda _settings: None)
    monkeypatch.setattr("github_metrics.cli.GitHubClient", lambda _settings: _NullClient())
    monkeypatch.setattr("github_metrics.cli.check_budget", affordable)
    monkeypatch.setattr("github_metrics.cli.Geocoder", lambda _agent: None)
    monkeypatch.setattr("github_metrics.cli.collect_all", collector)


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
            Outcome(
                reference=reference,
                metadata=metadata(reference),
                contributors=contributors(),
            )
            for reference in references
        ]

    _patch(monkeypatch, fake_collect)
    return asked


def run(*args: str) -> Any:
    """Invoke the CLI with a token supplied, against no real environment."""
    return CliRunner().invoke(main, ["--env-file", os.devnull, "--token", "ghp_x", "scan", *args])


def rows_of(directory: Path) -> list[dict[str, str]]:
    """Read the CSV artifact a run wrote into `directory`."""
    text = (directory / "githubmetrics.csv").read_text(encoding="utf-8")
    return list(csv.DictReader(text.splitlines()))


def document(directory: Path, owner: str, repoid: str) -> dict[str, Any]:
    """Read one per-repository document."""
    path = directory / owner / f"{repoid}.json"
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return payload


# ---------------------------------------------------------------------------
# The two artifacts one run produces
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-CLI-008")
@pytest.mark.usefixtures("offline")
def test_a_csv_is_written_with_one_row_per_reference(tmp_path: Path) -> None:
    result = run("pypa/virtualenv", "urllib3/urllib3", "--output", str(tmp_path))

    assert result.exit_code == 0
    rows = rows_of(tmp_path)
    assert [row["name"] for row in rows] == ["virtualenv", "urllib3"]
    assert rows[0]["total_score"] == "75.0"


@pytest.mark.requirement("L3-CLI-010")
@pytest.mark.usefixtures("offline")
def test_a_document_is_written_for_every_repository_that_was_read(tmp_path: Path) -> None:
    result = run("pypa/virtualenv", "urllib3/urllib3", "--output", str(tmp_path))

    assert result.exit_code == 0
    assert (tmp_path / "pypa" / "virtualenv.json").is_file()
    assert (tmp_path / "urllib3" / "urllib3.json").is_file()
    assert "Wrote 2 documents" in result.output


@pytest.mark.requirement("L3-OUT-012")
@pytest.mark.usefixtures("offline")
def test_a_document_is_its_csv_row_then_the_contributor_block(tmp_path: Path) -> None:
    """Every CSV column is a document key, in the same order, spelled the same.

    That is what makes the two artifacts joinable. The block is the part only
    the document has.
    """
    run("pypa/virtualenv", "--output", str(tmp_path))

    payload = document(tmp_path, "pypa", "virtualenv")
    row = rows_of(tmp_path)[0]

    assert tuple(payload)[: len(row)] == tuple(row)
    assert tuple(payload)[len(row) :] == ContributorBlock.keys()
    # Same values too, not merely the same names. The CSV renders everything as
    # a string; the document keeps JSON types, which is the documented
    # difference and the only one.
    assert payload["name"] == row["name"]
    assert str(payload["stars"]) == row["stars"]
    assert payload["scan_id"] == row["scan_id"]


@pytest.mark.requirement("L3-CLI-008")
@pytest.mark.usefixtures("offline")
def test_the_csv_carries_no_contributor_columns(tmp_path: Path) -> None:
    """Twenty columns, whatever the contributors turn out to be.

    The table is the comparable record and its shape is fixed, so two runs
    diff and a column sorts. Contributor detail is the document's job.
    """
    run("pypa/virtualenv", "--output", str(tmp_path))

    header = (tmp_path / "githubmetrics.csv").read_text(encoding="utf-8").splitlines()[0]

    assert header == EXPECTED_HEADER
    assert "contribution_total" not in header


@pytest.mark.requirement("L3-OUT-012")
@pytest.mark.usefixtures("offline")
def test_the_contributor_block_carries_the_run_identity(tmp_path: Path) -> None:
    """A contributor record that cannot be attributed to a run cannot be grouped."""
    run("pypa/virtualenv", "--output", str(tmp_path))

    payload = document(tmp_path, "pypa", "virtualenv")
    people = payload["contributors"]

    assert [entry["name"] for entry in people] == ["Saoud Rizwan", "Bee"]
    assert {entry["scan_id"] for entry in people} == {payload["scan_id"]}
    assert {entry["scan_date"] for entry in people} == {payload["scan_date"]}
    # The aggregate counts what was collected.
    assert payload["contribution_total"] == 2192 + 834


@pytest.mark.requirement("L3-OUT-012")
@pytest.mark.usefixtures("offline")
def test_an_unresolved_address_is_null_throughout(tmp_path: Path) -> None:
    """Null, not zero and not empty: 0,0 is a real place and would plot."""
    run("pypa/virtualenv", "--output", str(tmp_path))

    unresolved = document(tmp_path, "pypa", "virtualenv")["contributors"][1]

    assert unresolved["location"] is None
    address = unresolved["internal_address"]
    assert address["query"] is None
    assert address["country"] is None
    assert address["internal_location"] == {"latitude": None, "longitude": None}


@pytest.mark.requirement("L3-OUT-011")
@pytest.mark.usefixtures("offline")
def test_document_paths_are_nested_and_lower_cased(tmp_path: Path) -> None:
    """One directory per owner, folded to lower case.

    `PyPA/virtualenv` and `pypa/virtualenv` are one repository, so they must
    be one path. A flattened `<owner>-<repoid>.json` would also map
    `foo-bar/baz` and `foo/bar-baz` onto one file, which is two repositories
    rather than a duplicate pair.
    """
    run("PyPA/VirtualEnv", "--output", str(tmp_path))

    # Read back what is actually on disk rather than asking whether a lower-
    # cased path exists: `Path.is_file()` folds case on Windows and macOS, so
    # the question answers yes for `PyPA/VirtualEnv.json` too and the check
    # passes on the platforms this rule exists to protect.
    written = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*.json"))

    assert written == ["pypa/virtualenv.json"]


@pytest.mark.requirement("L3-CLI-010")
@pytest.mark.usefixtures("offline")
def test_the_default_destination_is_a_githubmetrics_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    result = run("pypa/virtualenv")

    assert result.exit_code == 0
    root = tmp_path / "githubmetrics"
    assert (root / "githubmetrics.csv").is_file()
    assert (root / "pypa" / "virtualenv.json").is_file()


@pytest.mark.requirement("L3-CLI-008")
@pytest.mark.usefixtures("offline")
def test_console_format_prints_the_rows_and_still_writes_documents(tmp_path: Path) -> None:
    result = run("pypa/virtualenv", "--format", "console", "--output", str(tmp_path))

    # Vertical, because twenty-five columns do not fit across a terminal.
    assert result.exit_code == 0
    assert "total_score" in result.output
    assert not (tmp_path / "githubmetrics.csv").exists()
    # There is no console form of a directory of documents, so they are still
    # written. The format flag governs the tabular artifact only.
    assert (tmp_path / "pypa" / "virtualenv.json").is_file()


@pytest.mark.requirement("L3-CLI-008")
@pytest.mark.usefixtures("offline")
def test_json_is_available_for_the_tabular_artifact(tmp_path: Path) -> None:
    result = run("pypa/virtualenv", "--format", "json", "--output", str(tmp_path))

    assert result.exit_code == 0
    payload = json.loads((tmp_path / "githubmetrics.json").read_text(encoding="utf-8"))
    assert payload[0]["owner"] == "pypa"


@pytest.mark.requirement("L3-CLI-008")
@pytest.mark.usefixtures("offline")
def test_fields_selects_columns_in_canonical_order(tmp_path: Path) -> None:
    result = run("pypa/virtualenv", "--fields", "total_score,owner", "--output", str(tmp_path))

    assert result.exit_code == 0
    header = (tmp_path / "githubmetrics.csv").read_text(encoding="utf-8").splitlines()[0]
    # Canonical order, not the order asked for: two runs wanting the same
    # columns should produce identical headers.
    assert header == "owner,total_score"


@pytest.mark.requirement("L3-OUT-012")
@pytest.mark.usefixtures("offline")
def test_field_selection_does_not_reach_the_documents(tmp_path: Path) -> None:
    """Selection is a rendering filter on the tabular artifact.

    A document with columns missing would stop being the row, and the two
    artifacts would no longer join on identical keys - which is the one
    property the pair exists to have.
    """
    run("pypa/virtualenv", "--fields", "owner", "--output", str(tmp_path))

    payload = document(tmp_path, "pypa", "virtualenv")
    assert "total_score" in payload
    assert "contributors" in payload


@pytest.mark.requirement("L3-CLI-008")
@pytest.mark.usefixtures("offline")
def test_the_sources_are_the_same_ones_validate_takes(tmp_path: Path) -> None:
    result = run(
        str(DATA / "repositories.csv"),
        "cline/cline",
        "https://github.com/psf/requests",
        "--output",
        str(tmp_path),
    )

    assert result.exit_code == 0
    assert [row["owner"] for row in rows_of(tmp_path)] == [
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
def test_an_unreadable_repository_gets_a_row_but_no_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The asymmetry between the artifacts is the design, not an oversight.

    A CSV row is positional, so omitting one would shift what every later row
    means. A directory has no positions, so an absent file says "named, not
    measured" on its own - and a document carrying an empty contributor array
    and a zero total would be indistinguishable from a repository that
    genuinely has no contributors.
    """

    def half(_client: Any, references: Any, **_kwargs: Any) -> list[Outcome]:
        return [
            (
                Outcome(
                    reference=reference,
                    metadata=metadata(reference),
                    contributors=contributors(),
                )
                if reference.owner != "ghost"
                else Outcome(
                    reference=reference,
                    error=RepositoryNotFoundError(f"{reference.full_name}: not found"),
                )
            )
            for reference in references
        ]

    _patch(monkeypatch, half)
    result = run("pypa/virtualenv", "ghost/missing", "--output", str(tmp_path))

    assert result.exit_code == EXIT_REPOSITORY_UNFETCHABLE
    rows = rows_of(tmp_path)
    assert len(rows) == 2
    # Identity kept, measurements empty. Empty rather than zero.
    assert rows[1]["name"] == "missing"
    assert rows[1]["owner"] == "ghost"
    assert rows[1]["stars"] == ""
    assert rows[1]["total_score"] == ""
    # And the run says which one, by name, rather than leaving it to be spotted.
    assert "! ghost/missing" in result.stderr

    assert (tmp_path / "pypa" / "virtualenv.json").is_file()
    assert not (tmp_path / "ghost").exists()
    assert "Wrote 1 documents" in result.output


@pytest.mark.requirement("L3-CLI-009")
def test_a_repository_whose_contributors_failed_keeps_its_row_and_loses_its_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Measurements survive; the document does not.

    Writing it anyway would publish a `contribution_total` of zero for a
    repository whose contributors were never read, which reads as data.
    """

    def partial(_client: Any, references: Any, **_kwargs: Any) -> list[Outcome]:
        return [
            Outcome(
                reference=reference,
                metadata=metadata(reference),
                contributor_error=ContributorCollectionError(
                    f"{reference.full_name}: could not read contributors"
                ),
            )
            for reference in references
        ]

    _patch(monkeypatch, partial)
    result = run("pypa/virtualenv", "--output", str(tmp_path))

    rows = rows_of(tmp_path)
    # Every measurement survives; the row is complete and says nothing about
    # contributors, because no column of it ever does.
    assert rows[0]["stars"] == "5041"
    assert rows[0]["total_score"] == "75.0"
    # The document is the only place a contribution total would have appeared,
    # and it is absent rather than zeroed.
    assert not (tmp_path / "pypa").exists()
    assert "Wrote 0 documents" in result.output


@pytest.mark.requirement("L3-CLI-009")
@pytest.mark.usefixtures("offline")
def test_a_rejected_reference_is_a_lesser_status_than_an_unreadable_one(
    tmp_path: Path,
) -> None:
    result = run("pypa/virtualenv", "notaslug", "--output", str(tmp_path))

    assert result.exit_code == EXIT_ROWS_REJECTED
    assert (tmp_path / "githubmetrics.csv").is_file()


@pytest.mark.requirement("L3-CLI-009")
def test_an_unaffordable_run_spends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pre-flight is the whole point: refusing costs one free request."""
    collected: list[Any] = []

    def refuse(_client: Any, count: int) -> None:
        raise RateLimitExhaustedError(f"{count} repositories need more than remain")

    def record(*args: Any, **kwargs: Any) -> list[Outcome]:
        del kwargs
        collected.append(args)
        return []

    _patch(monkeypatch, record)
    monkeypatch.setattr("github_metrics.cli.check_budget", refuse)

    result = run("pypa/virtualenv")

    assert result.exit_code != 0
    assert not collected


@pytest.mark.requirement("L3-CLI-009")
@pytest.mark.usefixtures("offline")
def test_a_bad_destination_fails_before_any_quota_is_spent(
    tmp_path: Path, offline: list[RepositoryRef]
) -> None:
    """An unwritable destination found afterwards has already cost an hour."""
    blocked = tmp_path / "occupied"
    blocked.write_text("not a directory", encoding="utf-8")

    result = run("pypa/virtualenv", "--output", str(blocked))

    # Click refuses a file where a directory is required, before the command
    # body runs at all. The check in `prepare_root` stands behind it for a
    # path that becomes a file between parsing and writing.
    assert result.exit_code != 0
    assert "is a file" in result.output
    assert offline == []


@pytest.mark.requirement("L3-CLI-009")
@pytest.mark.usefixtures("offline")
def test_a_run_that_names_nothing_still_produces_a_well_formed_file(
    tmp_path: Path, offline: list[RepositoryRef]
) -> None:
    result = run("notaslug", "--output", str(tmp_path))

    assert result.exit_code == EXIT_ROWS_REJECTED
    # A header and no rows: "no repositories" is distinguishable from "the run
    # produced nothing at all".
    csv_lines = (tmp_path / "githubmetrics.csv").read_text(encoding="utf-8").splitlines()
    assert csv_lines == [EXPECTED_HEADER]
    assert offline == []


# ---------------------------------------------------------------------------
# The run's identity
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-CLI-010")
@pytest.mark.usefixtures("offline")
def test_both_artifacts_of_one_run_carry_the_same_scan(tmp_path: Path) -> None:
    """The reason one command produces both.

    Two invocations would produce two UUIDs and two timestamps, and a CSV and
    a folder of documents collected minutes apart could not be joined or
    grouped by the run that made them.
    """
    run("pypa/virtualenv", "urllib3/urllib3", "--output", str(tmp_path))

    rows = rows_of(tmp_path)
    assert len({row["scan_id"] for row in rows}) == 1
    assert len({row["scan_date"] for row in rows}) == 1

    identities = {
        document(tmp_path, "pypa", "virtualenv")["scan_id"],
        document(tmp_path, "urllib3", "urllib3")["scan_id"],
        rows[0]["scan_id"],
    }
    assert len(identities) == 1


@pytest.mark.requirement("L3-CLI-008")
@pytest.mark.usefixtures("offline")
def test_two_runs_are_told_apart(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"

    run("pypa/virtualenv", "--output", str(first))
    run("pypa/virtualenv", "--output", str(second))

    assert rows_of(first)[0]["scan_id"] != rows_of(second)[0]["scan_id"]
