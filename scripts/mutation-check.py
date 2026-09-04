#!/usr/bin/env python
"""Break one documented behaviour at a time; confirm the suite notices.

A passing test suite is not evidence that the tests are any good. The evidence
is a test that **fails** when the thing it describes stops being true, and the
only way to find out is to stop it being true.

Each mutation below is a defect this repository has written down somewhere as
one to avoid - in `CLAUDE.md`, in an ADR, in a requirement, or in the docstring
of the thing it breaks. A mutation that survives means the behaviour is
described but not defended, and the tests covering it are decoration.

Three survived when this was first run, and each was a different shape of the
same problem - a check that could not fail:

- `document_path` lower-casing was asserted with `Path == Path`, and
  `WindowsPath.__eq__` folds case. The test passed on Windows however the code
  behaved, so the rule about case-insensitive GitHub names was verified on
  Linux only - and the platforms it protects were exactly the ones not
  checking it.
- The byte-order mark had two independent defences, `utf-8-sig` in the decode
  and an `lstrip` in the header normaliser. Either alone handled the case, so
  no test could tell which was working and deleting either left the suite
  green. The redundant one is gone.
- `resolve_destination` raises `GM-OUT-002` from two different guards, and the
  test asserted only the code. Deleting the missing-directory guard kept the
  suite green because the other guard caught the same input and said something
  else.

Not part of `make check`: it runs the suite once per mutation, so it costs
minutes rather than seconds. Run it when tests change shape, not on every
commit.

    poetry run python scripts/mutation-check.py
    poetry run python scripts/mutation-check.py --list
"""

# The hyphenated filename is deliberate: this is a command, not an importable
# module. pylint checks it against module naming rules regardless.
# pylint: disable=invalid-name

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Mutation:
    """One deliberate defect, and the promise it breaks.

    Attributes:
        name: What the mutation does, in the present tense.
        relative: The file to edit, relative to the repository root.
        before: Text to replace. Must appear exactly once, or the mutation is
            skipped rather than applied somewhere unintended.
        after: What to replace it with.
        expect: The consequence, in the words of whatever documents it.
    """

    name: str
    relative: str
    before: str
    after: str
    expect: str


MUTATIONS: tuple[Mutation, ...] = (
    # ---------------------------------------------------------------- output
    Mutation(
        "document paths stop folding case",
        "github_metrics/output/documents.py",
        'return root / owner.lower() / f"{repoid.lower()}.json"',
        'return root / owner / f"{repoid}.json"',
        "two spellings of one repository would produce two files",
    ),
    Mutation(
        "CSV rows are written in sorted order",
        "github_metrics/output/render.py",
        "    writer.writerow(selected)\n    for row in rows:",
        "    writer.writerow(selected)\n    for row in sorted(rows, key=lambda item: item.name):",
        "two runs of one inventory would stop being diffable",
    ),
    Mutation(
        "CSV stops forcing LF endings",
        "github_metrics/output/render.py",
        'lineterminator="\\n"',
        'lineterminator="\\r\\n"',
        "the artifact would differ byte-for-byte between platforms",
    ),
    Mutation(
        "JSON renders every value as a string",
        "github_metrics/model/software.py",
        "    if isinstance(value, (datetime, UUID)):\n        return str(value)\n    return value",
        "    return str(value)",
        "a consumer would have to re-parse numbers and booleans",
    ),
    Mutation(
        "field selection stops sorting into canonical order",
        "github_metrics/output/fields.py",
        "    return tuple(name for name in ALL_FIELDS if name in wanted)",
        "    return tuple(wanted)",
        "two runs asking for the same columns would produce different headers",
    ),
    Mutation(
        "an unknown field is accepted instead of rejected",
        "github_metrics/output/fields.py",
        "        raise UnknownFieldError(",
        "        pass\n        _unused = UnknownFieldError(",
        "output would silently omit a column the caller asked for",
    ),
    Mutation(
        "a directory destination stops gaining the default filename",
        "github_metrics/output/destination.py",
        "    if path.is_dir() or looks_like_directory:\n"
        "        path = path / (DEFAULT_JSON_FILENAME if json_format else DEFAULT_FILENAME)",
        "    if False:\n"
        "        path = path / (DEFAULT_JSON_FILENAME if json_format else DEFAULT_FILENAME)",
        "output would be written to a path that is a directory",
    ),
    Mutation(
        "an absent parent directory is no longer refused up front",
        "github_metrics/output/destination.py",
        "    if not parent.exists():\n        raise OutputDestinationError(",
        "    if False:\n        raise OutputDestinationError(",
        "the failure would arrive after a run had spent its quota",
    ),
    Mutation(
        "the JSON destination stops differing from the CSV one",
        "github_metrics/output/destination.py",
        'DEFAULT_JSON_FILENAME: Final = "githubmetrics.json"',
        'DEFAULT_JSON_FILENAME: Final = "githubmetrics.csv"',
        "a JSON run would overwrite a CSV run's output",
    ),
    # ------------------------------------------------------------ contributors
    Mutation(
        "contribution_total counts nothing",
        "github_metrics/analysis/row.py",
        "total = sum(entry.contribution or 0 for entry in stamped)",
        "total = 0",
        "the aggregate would read as a repository with no commits",
    ),
    Mutation(
        "a contributor with no name is recorded as empty",
        "github_metrics/collect/contributors.py",
        "        name=str(name) if name else account.login,",
        '        name=str(name) if name else "",',
        "a record would not say who it belongs to",
    ),
    Mutation(
        "an unresolved address plots at Null Island",
        "github_metrics/model/contributor.py",
        "    latitude: float | None = None\n    longitude: float | None = None",
        "    latitude: float | None = 0.0\n    longitude: float | None = 0.0",
        "0,0 is a real place, so the failure would look like data",
    ),
    Mutation(
        "the subdivision code takes the finest level, not the coarsest",
        "github_metrics/geo.py",
        "return min(levels)[1]",
        "return max(levels)[1]",
        "state_code would carry a county code rather than the state's",
    ),
    # ------------------------------------------------------------- collection
    Mutation(
        "a moved repository is collected rather than refused",
        "github_metrics/collect/repository.py",
        "    if not (metadata.was_renamed or metadata.was_transferred):\n        return",
        "    return\n"
        "    if not (metadata.was_renamed or metadata.was_transferred):\n"
        "        return",
        "a stale reference would return correct numbers about the wrong repository",
    ),
    Mutation(
        "the metrics query starts asking for nodes",
        "github_metrics/collect/repository.py",
        "    releases { totalCount }",
        "    releases { totalCount nodes { tagName } }",
        "cost would scale with repository size instead of staying at one point",
    ),
    Mutation(
        "a personally owned repository echoes its owner as an organisation",
        "github_metrics/collect/repository.py",
        '        return self.resolved_owner if self.owner_type == ORGANIZATION_TYPE else ""',
        "        return self.resolved_owner",
        "individual maintainers would each become an organisation",
    ),
    Mutation(
        "distinct versions become releases plus tags",
        "github_metrics/collect/repository.py",
        "        return max(self.releases, self.tags)",
        "        return self.releases + self.tags",
        "every version count would be inflated, some nearly doubled",
    ),
    Mutation(
        "results come back in completion order",
        "github_metrics/collect/runner.py",
        "        outcomes = list(pool.map(one, references))",
        "        outcomes = sorted(\n"
        "            (one(reference) for reference in references),\n"
        "            key=lambda outcome: outcome.reference.repoid,\n"
        "        )",
        "two runs of one inventory would stop being diffable",
    ),
    Mutation(
        "the budget ignores REST entirely",
        "github_metrics/collect/budget.py",
        "return self.available >= self.required and "
        "self.requests_available >= self.requests_required",
        "return self.available >= self.required",
        "a run with no REST quota would start and fail halfway",
    ),
    Mutation(
        "a 401 no longer fails the credential check",
        "github_metrics/collect/credentials.py",
        "    except BadCredentialsException as exc:\n        raise InvalidCredentialsError(",
        "    except BadCredentialsException as exc:\n"
        "        _unused = exc\n        _skip = InvalidCredentialsError(",
        "a run would start with a token GitHub will refuse",
    ),
    # -------------------------------------------------------------- ingestion
    Mutation(
        "a NUL byte is no longer detected",
        "github_metrics/sources/csv_inventory.py",
        'nul_at = raw.find(b"\\x00")',
        "nul_at = -1",
        "a renamed binary would produce one bogus issue per row",
    ),
    Mutation(
        "the byte-order mark is no longer stripped",
        "github_metrics/sources/csv_inventory.py",
        'text = raw.decode("utf-8-sig")',
        'text = raw.decode("utf-8")',
        "Excel's CSV export would report a missing owner column",
    ),
    Mutation(
        "row validation reports the wrong code for an empty owner",
        "github_metrics/sources/csv_inventory.py",
        '    if not owner:\n        return ISSUE_EMPTY_OWNER, f"owner is empty in {echo!r}"',
        '    if not owner:\n        return ISSUE_INVALID_OWNER, f"owner is empty in {echo!r}"',
        "a rejected row would carry the wrong code",
    ),
    Mutation(
        "strict mode stops raising on the first issue",
        "github_metrics/sources/csv_inventory.py",
        "        if strict:\n            raise StrictModeError",
        "        if False:\n            raise StrictModeError",
        "a defective inventory would read as merely degraded",
    ),
    Mutation(
        "duplicate references stop being dropped",
        "github_metrics/sources/resolve.py",
        "        first = seen.get(reference.key)",
        "        first = None",
        "one repository would be collected and billed twice",
    ),
    Mutation(
        "an owner's character grammar stops being checked",
        "github_metrics/validation.py",
        "    if not _OWNER_CHARS.match(owner):\n"
        '        return "may only contain letters, digits and hyphens"',
        '    if False:\n        return "may only contain letters, digits and hyphens"',
        "a malformed reference would reach the API",
    ),
    Mutation(
        "a hyphen at the edge of an owner is accepted",
        "github_metrics/validation.py",
        '    if owner.startswith("-") or owner.endswith("-"):\n'
        '        return "may not begin or end with a hyphen"',
        '    if False:\n        return "may not begin or end with a hyphen"',
        "a name GitHub refuses would be collected against",
    ),
    # ---------------------------------------------------------------- scoring
    Mutation(
        "total_score drops a component",
        "github_metrics/analysis/total.py",
        "    total = float(sum(components))",
        "    total = float(sum(components[:-1]))",
        "the published total would not be the sum of its parts",
    ),
    Mutation(
        "trusted-org matching becomes case-sensitive",
        "github_metrics/analysis/trusted_orgs.py",
        "        matched = owner.strip().casefold() in self.entries",
        "        matched = owner.strip() in self.entries",
        "an organisation would lose its bonus over its spelling",
    ),
    Mutation(
        "an unfetchable repository scores zero instead of nothing",
        "github_metrics/analysis/row.py",
        "        name=reference.repoid,\n        owner=reference.owner,",
        "        name=reference.repoid,\n        owner=reference.owner,\n        total_score=0.0,",
        "an unreadable repository would be indistinguishable from a bad one",
    ),
)


def run_suite() -> bool:
    """Run the fast suite. Returns whether it passed."""
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-x", "-q", "--no-cov", "-m", "not integration"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0


def apply_and_test(mutation: Mutation) -> str:
    """Apply one mutation, run the suite, and put the file back.

    Args:
        mutation: The defect to introduce.

    Returns:
        `"caught"`, `"survived"`, or `"skipped"` when the anchor did not match
        exactly once - which means the source moved and the mutation needs
        rewriting rather than that the behaviour is safe.
    """
    path = ROOT / mutation.relative
    original = path.read_text(encoding="utf-8")
    if original.count(mutation.before) != 1:
        return "skipped"

    path.write_text(
        original.replace(mutation.before, mutation.after), encoding="utf-8", newline="\n"
    )
    try:
        passed = run_suite()
    finally:
        # Restored whatever happened, including a KeyboardInterrupt: leaving a
        # deliberate defect in the tree is worse than any result.
        path.write_text(original, encoding="utf-8", newline="\n")
    return "survived" if passed else "caught"


def main() -> int:
    """Run every mutation and report the survivors."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--list", action="store_true", help="Print the mutations without running them."
    )
    arguments = parser.parse_args()

    if arguments.list:
        for mutation in MUTATIONS:
            print(f"{mutation.name}\n    {mutation.relative}\n    -> {mutation.expect}")
        return 0

    survivors: list[Mutation] = []
    skipped: list[Mutation] = []

    for index, mutation in enumerate(MUTATIONS, start=1):
        outcome = apply_and_test(mutation)
        print(f"[{index:>2}/{len(MUTATIONS)}] {outcome:<9} {mutation.name}", flush=True)
        if outcome == "survived":
            survivors.append(mutation)
        elif outcome == "skipped":
            skipped.append(mutation)

    print()
    caught = len(MUTATIONS) - len(survivors) - len(skipped)
    print(f"caught {caught}, survived {len(survivors)}, skipped {len(skipped)}")

    for mutation in skipped:
        print(f"  SKIPPED:  {mutation.name} - the anchor no longer matches; rewrite it")
    for mutation in survivors:
        print(f"  SURVIVED: {mutation.name}\n              {mutation.expect}")

    # A skip is a failure too. It means the mutation silently stopped testing
    # anything, which is the state this whole script exists to detect.
    return 1 if survivors or skipped else 0


if __name__ == "__main__":
    raise SystemExit(main())
