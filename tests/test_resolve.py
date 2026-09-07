"""Tests for :mod:`github_metrics.sources.resolve`."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from github_metrics.errors import ISSUE_DUPLICATE, IngestError, StrictModeError
from github_metrics.sources.resolve import is_csv_source, resolve_sources

DATA = Path(__file__).parent / "data"
INVENTORY = str(DATA / "repositories.csv")
WITH_BOM = str(DATA / "with-bom.csv")

LOGGER_NAME = "github_metrics.sources.resolve"


def names(*values: str) -> list[str]:
    """Resolve sources and return the accepted slugs."""
    return [reference.full_name for reference in resolve_sources(values).repositories]


# ---------------------------------------------------------------------------
# Telling a path from a repository
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-SRC-003")
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("inventory.csv", True),
        ("data/inventory.csv", True),
        ("INVENTORY.CSV", True),
        ("pypa/virtualenv", False),
        ("https://github.com/pypa/virtualenv", False),
        # A URL is a URL even when it ends in .csv, because rule 1 wins.
        ("https://github.com/pypa/some.csv", False),
    ],
)
def test_the_rules_are_checked_in_order(value: str, expected: bool) -> None:
    assert is_csv_source(value) is expected


@pytest.mark.requirement("L3-SRC-003")
def test_an_existing_file_is_read_even_without_a_csv_suffix(tmp_path: Path) -> None:
    # Rule 2. Someone who named their list `inventory` should not have to
    # rename it to be allowed to use it.
    inventory = tmp_path / "inventory"
    inventory.write_bytes(b"owner,repoid\npypa,virtualenv\n")

    assert is_csv_source(str(inventory)) is True
    assert names(str(inventory)) == ["pypa/virtualenv"]


@pytest.mark.requirement("L3-SRC-003")
def test_a_mistyped_path_reports_a_missing_file_not_a_bad_name() -> None:
    """Rule 3 exists for exactly this diagnosis.

    Without it, `inventroy.csv` would be read as a slug and refused for having
    no `/`, which is true and useless.
    """
    with pytest.raises(IngestError, match="no such file"):
        resolve_sources(["inventroy.csv"])


# ---------------------------------------------------------------------------
# Order and mixing
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-SRC-004")
def test_sources_of_different_kinds_mix_in_the_order_written() -> None:
    assert names("cline/cline", INVENTORY, "https://github.com/psf/requests") == [
        "cline/cline",
        "urllib3/urllib3",
        "bokeh/bokeh",
        "pypa/virtualenv",
        "psf/requests",
    ]


@pytest.mark.requirement("L3-SRC-004")
def test_the_same_arguments_always_resolve_the_same_way() -> None:
    # Files are read concurrently; the result is assembled from the arguments
    # as written, so scheduling cannot reorder it.
    arguments = (INVENTORY, "cline/cline", WITH_BOM)
    first = [reference.full_name for reference in resolve_sources(arguments).repositories]
    second = [reference.full_name for reference in resolve_sources(arguments).repositories]

    assert first == second


# ---------------------------------------------------------------------------
# Repetition across sources
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-SRC-005")
def test_a_repository_named_twice_is_collected_once() -> None:
    resolved = resolve_sources(["pypa/virtualenv", "pypa/virtualenv"])

    assert [reference.full_name for reference in resolved.repositories] == ["pypa/virtualenv"]
    assert resolved.issues[0].code == ISSUE_DUPLICATE
    assert "spend the rate limit twice" in resolved.issues[0].message


@pytest.mark.requirement("L3-SRC-005")
def test_a_repetition_across_two_files_is_caught() -> None:
    """Neither file can see it; only the resolution can."""
    resolved = resolve_sources([INVENTORY, WITH_BOM])

    assert resolved.accepted == 3
    assert resolved.rejected == 1
    assert "repositories.csv" in resolved.issues[0].message


@pytest.mark.requirement("L3-SRC-005")
def test_repetition_ignores_case_as_github_does() -> None:
    resolved = resolve_sources(["pypa/virtualenv", "PyPA/VirtualEnv"])

    assert resolved.accepted == 1


@pytest.mark.requirement("L3-SRC-005")
def test_the_first_mention_is_the_one_that_survives() -> None:
    # Keeping the later one would make the output order depend on a
    # repetition, which is the opposite of what input order is for.
    resolved = resolve_sources(["cline/cline", INVENTORY, "cline/cline"])

    assert resolved.repositories[0].full_name == "cline/cline"
    assert len(resolved.repositories) == 4


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-SRC-004")
def test_counts_cover_every_kind_of_source() -> None:
    resolved = resolve_sources([INVENTORY, "cline/cline", "notaslug"])

    assert resolved.rows_read == 5
    assert resolved.accepted == 4
    assert resolved.rejected == 1
    assert resolved.ok is False
    assert [str(path) for path in resolved.files] == [INVENTORY]


@pytest.mark.requirement("L3-LOG-002")
def test_the_run_reports_its_outcome_once_at_info(caplog: pytest.LogCaptureFixture) -> None:
    # Per run, not per repository: this one survives the DEBUG rule.
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        resolve_sources(["cline/cline", "pypa/virtualenv"])

    assert len(caplog.records) == 1
    assert "Resolved 2 repositories" in caplog.text


# ---------------------------------------------------------------------------
# strict, which used to reach only the CSV reader
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-SRC-006")
def test_strict_promotes_a_bad_reference_named_on_the_command_line() -> None:
    """`--strict` is documented as "fail the pipeline on any defect".

    It was passed only to `read_repository_csvs`, so it abandoned a bad row in
    a file and ignored the identical defect written as an argument -
    `validate --strict gitlab.com/a/b` exited 3, the status for "read, but
    degraded", rather than 6.
    """
    with pytest.raises(StrictModeError) as caught:
        resolve_sources(["gitlab.com/a/b"], strict=True)

    assert "strict mode" in str(caught.value)


@pytest.mark.requirement("L3-SRC-006")
def test_strict_promotes_a_repetition_across_two_sources(tmp_path: Path) -> None:
    """The duplicate check runs outside the reader, so strict never saw it.

    Without this the flag changed nothing at all for a repetition: the same
    exit code, the same report, with `--strict` and without it.
    """
    inventory = tmp_path / "inventory.csv"
    inventory.write_text("owner,repoid\npypa,virtualenv\n", encoding="utf-8")

    with pytest.raises(StrictModeError) as caught:
        resolve_sources([str(inventory), "pypa/virtualenv"], strict=True)

    assert ISSUE_DUPLICATE in str(caught.value)


@pytest.mark.requirement("L3-SRC-006")
def test_strict_promotes_the_earliest_problem_in_argument_order(tmp_path: Path) -> None:
    """Files are read before the arguments are walked, so the reader's own
    strict mode would abandon a file named third before a slug named first."""
    inventory = tmp_path / "inventory.csv"
    inventory.write_text("owner,repoid\ngitlab.com,\n", encoding="utf-8")

    with pytest.raises(StrictModeError) as caught:
        resolve_sources(["gitlab.com/a/b", str(inventory)], strict=True)

    assert "<argument>" in str(caught.value), "the slug came first"

    with pytest.raises(StrictModeError) as second:
        resolve_sources([str(inventory), "gitlab.com/a/b"], strict=True)

    assert "inventory.csv" in str(second.value), "and here the file did"


@pytest.mark.requirement("L3-SRC-006")
def test_a_clean_run_resolves_the_same_either_way(tmp_path: Path) -> None:
    """Strict changes what happens to a problem, never what a good run yields."""
    inventory = tmp_path / "inventory.csv"
    inventory.write_text("owner,repoid\npypa,virtualenv\n", encoding="utf-8")

    lenient = resolve_sources([str(inventory), "psf/requests"])
    strict = resolve_sources([str(inventory), "psf/requests"], strict=True)

    assert names_of(lenient) == names_of(strict)
    assert not strict.issues


def names_of(resolved: object) -> list[str]:
    """The accepted references, by full name."""
    return [reference.full_name for reference in resolved.repositories]  # type: ignore[attr-defined]
