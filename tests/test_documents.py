"""Tests for :mod:`github_metrics.output.documents`.

The per-repository document is the CSV row followed by the contributor block,
and its path is a function of *which repository this is* rather than of how
someone happened to spell it. Both of those are checked here; the CLI tests
check that a run produces the two artifacts together.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from github_metrics.errors import DocumentDirectoryError
from github_metrics.model.contributor import (
    Address,
    Contributor,
    ContributorBlock,
    Coordinates,
)
from github_metrics.model.software import SoftwareRow
from github_metrics.output.documents import (
    DEFAULT_DOCUMENT_ROOT,
    build_document,
    document_path,
    prepare_root,
    write_document,
)


def row(owner: str = "pypa", name: str = "virtualenv") -> SoftwareRow:
    """A minimal but well-formed row."""
    return SoftwareRow(
        name=name,
        owner=owner,
        organization=owner,
        url=f"https://github.com/{owner}/{name}",
        stars=5041,
    )


def block(*people: Contributor) -> ContributorBlock:
    """A block over the given contributors, totalled as the builder would."""
    return ContributorBlock(
        contributors=people,
        contribution_total=sum(entry.contribution or 0 for entry in people),
    )


def contributor() -> Contributor:
    """One contributor with a resolved address."""
    return Contributor(
        github_id="7799382",
        name="Saoud Rizwan",
        location="United States",
        internal_address=Address(
            query="United States",
            formatted_address="Decatur County, Kansas, United States",
            country="United States",
            country_code="us",
            city="",
            internal_location=Coordinates(latitude=39.784824, longitude=-100.4458771),
        ),
        contribution=2192,
    )


# ---------------------------------------------------------------------------
# The document is the row, then the block
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-OUT-012")
def test_a_document_is_the_row_then_the_block() -> None:
    """Every CSV column, in order, then the keys only the document has.

    That is what lets a `githubmetrics.csv` row and a document join without a
    translation table.
    """
    document = build_document(row(), block(contributor()))
    columns = SoftwareRow.to_header()

    assert tuple(document)[: len(columns)] == columns
    assert tuple(document)[len(columns) :] == ContributorBlock.keys()


@pytest.mark.requirement("L3-OUT-012")
def test_the_aggregates_are_document_keys_and_not_csv_columns() -> None:
    """Contributor detail is the document's job; the table is the comparable one.

    The CSV's shape is fixed at twenty columns so that two runs diff and a
    column sorts, and a name appearing in both artifacts would mean two
    different things depending on which was being read.
    """
    assert "contribution_total" not in SoftwareRow.to_header()
    assert not set(ContributorBlock.keys()) & set(SoftwareRow.to_header())


@pytest.mark.requirement("L3-OUT-012")
def test_a_document_keeps_json_types() -> None:
    document = build_document(row(), block(contributor()))

    assert document["stars"] == 5041
    assert document["contribution_total"] == 2192
    # Undefined is null, never zero: a zero would assert that this repository
    # has no foreign contribution, which nothing has measured.
    assert document["foreign_percent"] is None
    assert document["foreign_contribution"] is None


@pytest.mark.requirement("L3-OUT-012")
def test_a_repository_with_no_contributors_totals_zero() -> None:
    """Zero is the honest answer here, and only here.

    A repository whose contributors could not be *read* gets no document at
    all, so a zero in a document always means the list was read and was empty.
    """
    document = build_document(row(), ContributorBlock())

    assert document["contributors"] == []
    assert document["contribution_total"] == 0


@pytest.mark.requirement("L3-OUT-012")
def test_the_contributor_block_matches_the_documented_example() -> None:
    """`docs/example.json` is the agreed shape; a built document must match it."""
    example = json.loads(
        (Path(__file__).parents[1] / "docs" / "example.json").read_text(encoding="utf-8")
    )
    built = build_document(row(), block(contributor()))

    assert tuple(built["contributors"][0]) == tuple(example["contributors"][0])
    address = built["contributors"][0]["internal_address"]
    assert tuple(address) == tuple(example["contributors"][0]["internal_address"])
    assert tuple(address["internal_location"]) == ("latitude", "longitude")


# ---------------------------------------------------------------------------
# Where the file goes
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-OUT-011")
def test_the_path_is_nested_by_owner(tmp_path: Path) -> None:
    assert document_path(tmp_path, "pypa", "virtualenv") == tmp_path / "pypa" / "virtualenv.json"


@pytest.mark.requirement("L3-OUT-011")
def test_the_path_is_lower_cased(tmp_path: Path) -> None:
    """GitHub names are case-insensitive, so one repository is one path.

    A case-sensitive filename would produce two files for one repository on
    Linux, and on Windows or macOS would let the second silently overwrite the
    first while the run reported two successes.
    """
    assert document_path(tmp_path, "PyPA", "VirtualEnv") == document_path(
        tmp_path, "pypa", "virtualenv"
    )


@pytest.mark.requirement("L3-OUT-011")
def test_nesting_removes_a_collision_that_flattening_would_create(tmp_path: Path) -> None:
    """`foo-bar/baz` and `foo/bar-baz` are two repositories, not a duplicate pair.

    A flattened `<owner>-<repoid>.json` maps both onto `foo-bar-baz.json`.
    Duplicate detection correctly says nothing, one file overwrites the other,
    and the run exits 0 with a repository missing. A path separator is not a
    legal character in either name, so nesting cannot express the collision.
    """
    first = document_path(tmp_path, "foo-bar", "baz")
    second = document_path(tmp_path, "foo", "bar-baz")

    assert first != second
    assert first.name == "baz.json"
    assert second.name == "bar-baz.json"


@pytest.mark.requirement("L3-OUT-011")
def test_a_windows_reserved_name_needs_no_sanitising(tmp_path: Path) -> None:
    """The reservation applies to the bare name; these carry a suffix.

    Checked rather than assumed: `con.json` creates, lists and reads back.
    """
    root = prepare_root(tmp_path)
    path = write_document(root, row(owner="acme", name="con"), ContributorBlock())

    assert path.is_file()
    assert json.loads(path.read_text(encoding="utf-8"))["name"] == "con"


# ---------------------------------------------------------------------------
# Preparing the directory, and failing early when it cannot be
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-OUT-011")
def test_the_default_root_is_used_when_none_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    assert prepare_root(None) == Path(DEFAULT_DOCUMENT_ROOT)
    assert (tmp_path / DEFAULT_DOCUMENT_ROOT).is_dir()


@pytest.mark.requirement("L3-OUT-011")
def test_a_missing_root_is_created(tmp_path: Path) -> None:
    root = prepare_root(tmp_path / "deep" / "nested")

    assert root.is_dir()


@pytest.mark.requirement("L3-OUT-011")
def test_a_root_that_is_a_file_is_refused(tmp_path: Path) -> None:
    """Before collection, because quota does not refill on request."""
    occupied = tmp_path / "occupied"
    occupied.write_text("not a directory", encoding="utf-8")

    with pytest.raises(DocumentDirectoryError) as caught:
        prepare_root(occupied)

    assert caught.value.code == "GM-OUT-003"
    assert "is not a directory" in str(caught.value)


@pytest.mark.requirement("L3-OUT-011")
def test_a_document_is_written_with_lf_endings(tmp_path: Path) -> None:
    """A data interchange artifact, and this repository is LF throughout.

    Without an explicit newline the same run would produce different bytes on
    Windows than on Linux.
    """
    root = prepare_root(tmp_path)
    path = write_document(root, row(), block(contributor()))

    raw = path.read_bytes()
    assert b"\r\n" not in raw
    assert raw.endswith(b"\n")
