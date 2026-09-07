"""Tests for how the trace matrix generator reads the requirement documents.

The matrix is the only place requirement status exists, so a requirement the
generator cannot read is not reported as unverified - it is reported as nothing
at all, and the coverage summary simply describes a smaller tree. That is not
hypothetical: three L1 requirements were written under a level-four heading,
the pattern for an L1 matches level three only, and the matrix counted 19 of 22
for five releases while every row beneath the three orphans still rendered
correctly. Nothing failed, because nothing was looking.

These tests hold the documents and the generator to the same view of what
exists, from three directions: every id a document declares must be one the
generator reads, every parent link must resolve to a requirement it read, and
every requirement must sit in the section its own id names.
"""

from __future__ import annotations

import ast
import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "build-trace-matrix.py"

# Any heading depth, deliberately. The generator's own patterns pin the level,
# which is the mistake being guarded against, so a test that reused them would
# agree with the bug.
HEADING_ID = re.compile(r"^#+[^\S\n]+(L[123]-[A-Z]+-\d+)[^\S\n]*$", re.MULTILINE)
L3_ENTRY = re.compile(r"^\*\*(L3-[A-Z]+-\d+)\*\*", re.MULTILINE)

# A section heading, and the first thing on a line that announces a
# requirement: a heading at any depth for L1 and L2, a bolded id for L3.
SECTION_HEADING = re.compile(r"^##[^\S\n]+L[123]-([A-Z]+):")
REQUIREMENT_START = re.compile(r"^(?:#+[^\S\n]+|\*\*)(L[123]-([A-Z]+)-\d+)")
CATEGORY_ROW = re.compile(
    r"^\|[^\S\n]*`([A-Z]+)`[^\S\n]*\|[^\S\n]*([^|\n]+?)[^\S\n]*\|[^\S\n]*$", re.MULTILINE
)


def _load_generator() -> ModuleType:
    """Import the generator, whose filename is a command name rather than a module name."""
    spec = importlib.util.spec_from_file_location("build_trace_matrix", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # `@dataclass` resolves annotations through `sys.modules[cls.__module__]`,
    # so the module has to be registered before its body runs, not after.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


GENERATOR = _load_generator()


def _declared(doc: Path, level: str) -> set[str]:
    """Every id the document announces as a section heading, at any depth."""
    return {
        found
        for found in HEADING_ID.findall(doc.read_text(encoding="utf-8"))
        if found.startswith(level)
    }


def _without(mapping: dict[str, Any], key: str) -> dict[str, Any]:
    """The mapping less one entry, standing in for a requirement that failed to parse."""
    return {name: value for name, value in mapping.items() if name != key}


def _used_categories() -> set[str]:
    """Every category code that a requirement id in any document uses."""
    return {
        req.split("-")[1]
        for level in (GENERATOR.parse_l1(), GENERATOR.parse_l2(), GENERATOR.parse_l3())
        for req in level
    }


def _misfiled(doc: Path) -> list[str]:
    """Requirements whose id names a category other than the section holding them."""
    section: str | None = None
    wrong: list[str] = []
    for line in doc.read_text(encoding="utf-8").split("\n"):
        heading = SECTION_HEADING.match(line)
        if heading:
            section = heading.group(1)
            continue
        found = REQUIREMENT_START.match(line)
        if found and found.group(2) != section:
            wrong.append(f"{found.group(1)} sits in {doc.name} section {section}")
    return wrong


def test_every_l1_the_document_declares_is_one_the_generator_reads() -> None:
    """A heading at the wrong depth loses the requirement, not the document."""
    assert _declared(GENERATOR.L1_DOC, "L1-") == set(GENERATOR.parse_l1())


def test_every_l2_the_document_declares_is_one_the_generator_reads() -> None:
    assert _declared(GENERATOR.L2_DOC, "L2-") == set(GENERATOR.parse_l2())


def test_every_l3_the_document_declares_is_one_the_generator_reads() -> None:
    """L3s are one-line entries, so their grammar drifts a different way."""
    text = GENERATOR.L3_DOC.read_text(encoding="utf-8")
    assert set(L3_ENTRY.findall(text)) == set(GENERATOR.parse_l3())


def test_the_committed_documents_form_one_connected_graph() -> None:
    """Every parent link resolves, so nothing is stranded above the rows it owns."""
    GENERATOR.load_trace()


def test_an_l1_the_generator_missed_fails_the_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """The historical failure, reproduced: L1-STA-001 unread, its children orphaned.

    Before this check the run succeeded and published a smaller tree, which is
    why it survived. The three L2 rows below it kept rendering, and a category
    section without an L1 table is a legitimate shape here - `COL`, `ROW` and
    `SRC` parent to L1s in other categories - so nothing about the output
    looked wrong.
    """
    l1 = GENERATOR.parse_l1()
    monkeypatch.setattr(GENERATOR, "parse_l1", lambda: _without(l1, "L1-STA-001"))

    with pytest.raises(SystemExit) as raised:
        GENERATOR.load_trace()

    assert "L2-STA-001 -> L1-STA-001" in str(raised.value)


def test_an_l2_the_generator_missed_fails_the_build(monkeypatch: pytest.MonkeyPatch) -> None:
    l2 = GENERATOR.parse_l2()
    monkeypatch.setattr(GENERATOR, "parse_l2", lambda: _without(l2, "L2-STA-001"))

    with pytest.raises(SystemExit) as raised:
        GENERATOR.load_trace()

    assert "L3-STA-001 -> L2-STA-001" in str(raised.value)


def test_every_l1_declares_a_verification_method() -> None:
    """The convention in L1.md requires one, and all three orphans lacked it."""
    assert [req for req, (methods, _) in GENERATOR.parse_l1().items() if not methods] == []


def test_every_category_carries_a_title() -> None:
    """A category with no title renders in the matrix as the bare code, `CNF: CNF`."""
    assert sorted(_used_categories() - set(GENERATOR.parse_categories())) == []


def test_every_requirement_sits_in_the_section_its_id_names() -> None:
    """The document's own organisation, checked against the ids it organises.

    This is the general form of the level-four heading fault. `L1-STA-001` was
    not only at the wrong depth, it was inside `## L1-OUT`; `L2-COL-001` and
    `L2-ROW-001` were inside `## L2-LOG`, and neither `SRC` nor `CNF` had a
    section at all. A section heading is read by no tool, so filing a
    requirement under the wrong one costs nothing until somebody believes it.
    """
    misfiled = [
        entry
        for doc in (GENERATOR.L1_DOC, GENERATOR.L2_DOC, GENERATOR.L3_DOC)
        for entry in _misfiled(doc)
    ]
    assert misfiled == []


def test_the_tables_of_categories_agree_with_the_section_headings() -> None:
    """The tables are reader documentation; the headings are what the generator reads."""
    titles = GENERATOR.parse_categories()
    for doc, ids in (
        (GENERATOR.L1_DOC, GENERATOR.parse_l1()),
        (GENERATOR.L2_DOC, GENERATOR.parse_l2()),
    ):
        used = {req.split("-")[1] for req in ids}
        table = dict(CATEGORY_ROW.findall(doc.read_text(encoding="utf-8")))
        assert table == {code: titles[code] for code in used}


@pytest.mark.requirement("L3-COL-001")
def test_no_requirement_rests_only_on_a_test_the_gate_never_runs() -> None:
    """The soak checks add coverage; they must not be the only coverage.

    They are marked `soak` and CI deselects them, so a requirement whose sole
    artifact lived there would read **Implemented** in the matrix on the
    strength of something no merge ever runs. That is this repository's most
    persistent failure - a marker claiming coverage the assertion does not
    provide - in a new place, and it is cheap to refuse.
    """
    trace = GENERATOR.load_trace()
    soak = set(_soak_tests())
    # Without this the check passes by finding nothing - the failure mode of
    # every guard in this file, and the reason each one asserts it looked.
    assert soak, "no soak tests were found; this guard would pass vacuously"

    unbacked = sorted(
        requirement
        for requirement, artifacts in trace.markers.items()
        if artifacts and all(artifact in soak for artifact in artifacts)
    )

    assert unbacked == []


def _soak_tests() -> set[str]:
    """Every `path::name` the gate deselects for being a soak check."""
    found: set[str] = set()
    for path in sorted((ROOT / "tests").rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        relative = path.relative_to(ROOT).as_posix()
        module_wide = any(
            "soak" in {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(getattr(target, "id", "") == "pytestmark" for target in node.targets)
        )
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            marked = module_wide or any(
                "soak" in {n.attr for n in ast.walk(decorator) if isinstance(n, ast.Attribute)}
                for decorator in node.decorator_list
            )
            if marked:
                found.add(f"{relative}::{node.name}")
    return found
