"""Long-running checks against the live GitHub API.

Every defect fixed between v0.6.3 and v0.6.15 lived on a path the ordinary
suite cannot reach. The budget guard could not detect exhaustion, a dropped
connection lost every row, a failed history page ended the run - and all three
were found by reading, then fixed and verified by simulation. The suite has one
integration test, and CI deselects it, so nothing in this repository has ever
driven those paths against the real service.

That is what this file is for. It is deliberately not part of `make check`: it
costs API budget, it takes minutes rather than seconds, and it can fail for
reasons that are nobody's fault - GitHub being slow, a network blip, a token
someone else is spending. **A failure here is a question, not a verdict.**

Two profiles, chosen with `SOAK_PROFILE`:

- **`quick`** (the default, and what the schedule runs) collects a handful of
  real repositories and checks what only a live run can show: that spend is
  actually measured, that the artifacts are well-formed, that a repository the
  inventory names but GitHub does not is degraded rather than fatal, and that
  the default log level stays quiet.

- **`exhaustion`** drives the hourly budget to its end on purpose and watches
  `--on-exhaustion` do what it promises. It is **manual only**: it spends a
  token's whole hourly quota and then waits for the reset, so it takes over an
  hour and leaves that token unusable meanwhile. Scheduling that weekly would
  buy one check at the cost of a token nobody else can use.

Neither profile asserts exact numbers. Star counts change, contributors come
and go, and a test that pins them is a test that fails on Tuesdays. What is
asserted is structure and direction: that a figure moved, that a bucket is
populated, that a failure was classified rather than escaping.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

LIVE_TOKEN = os.environ.get("GITHUB_TOKEN", "")
"""Read at import, like the other live test.

The autouse `clean_env` fixture scrubs `GITHUB_TOKEN` before every test, so a
fixture-time read would find nothing.
"""

PROFILE = os.environ.get("SOAK_PROFILE", "quick")

QUICK_INVENTORY = (
    "pypa/virtualenv",
    "psf/requests",
    "ghost/definitely-not-a-real-repository-xyz",
)
"""Small, stable, and one that does not exist.

The absent repository is the point of the third entry: a live run has to
degrade it to an identity-only row and carry on, which is `L2-COL-001` and the
thing that broke twice in this release series.
"""

DEEP_INVENTORY = ("torvalds/linux",)
"""One repository with an enormous history, for the exhaustion profile.

`--deep-attribution` costs a point per hundred commits, so this reaches the
wall in one repository rather than the ~555 an ordinary scan would need.
"""

pytestmark = [
    pytest.mark.soak,
    pytest.mark.skipif(not LIVE_TOKEN, reason="GITHUB_TOKEN is not set"),
]


def run_scan(
    directory: Path, *args: str, inventory: tuple[str, ...]
) -> subprocess.CompletedProcess[str]:
    """Run a real scan in a subprocess, as an operator would.

    A subprocess rather than `CliRunner`, because two of the things worth
    checking are what reaches the process's stderr and what exit status the
    shell sees - neither of which is observable in-process.

    Args:
        directory: Where the artifacts go.
        *args: Extra flags for `scan`.
        inventory: Repositories to name on the command line.

    Returns:
        The finished process, with output captured.
    """
    return subprocess.run(  # noqa: S603 - the argv is this module's own constants
        [
            sys.executable,
            "-m",
            # `github_metrics`, not `github_metrics.cli`: the latter imports
            # the module and exits 0 without running anything, which would
            # make every assertion below pass against no scan at all.
            "github_metrics",
            "--token",
            LIVE_TOKEN,
            "scan",
            *inventory,
            "--output",
            str(directory),
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=1800,
    )


def statistics_of(directory: Path) -> dict[str, Any]:
    """Read the statistics artifact a scan wrote."""
    payload: dict[str, Any] = json.loads(
        (directory / "statistics.json").read_text(encoding="utf-8")
    )
    return payload


# ---------------------------------------------------------------------------
# quick: what only a live run can show
# ---------------------------------------------------------------------------


@pytest.mark.skipif(PROFILE != "quick", reason="SOAK_PROFILE is not 'quick'")
@pytest.mark.requirement("L3-COL-001", "L3-STA-008")
def test_a_live_scan_measures_what_it_spent(tmp_path: Path) -> None:
    """Spend is measured by difference against the API's own counters.

    Every stub in the ordinary suite answers a constant, so a budget that never
    moved would look identical. Only a real run can show the number changing -
    and the pre-flight reading the wrong budget entirely is a defect this
    series shipped twice.
    """
    result = run_scan(tmp_path, inventory=QUICK_INVENTORY)

    assert result.returncode in (0, 4), result.stderr[-2000:]
    budget = statistics_of(tmp_path)["budget"]

    assert budget["graphql_points_spent"] > 0, "a real run spends real points"
    assert budget["graphql_points_remaining"] < 5000, "and the remaining count moves"
    assert budget["exhausted"] is False, "this run is far too small to run dry"


@pytest.mark.skipif(PROFILE != "quick", reason="SOAK_PROFILE is not 'quick'")
@pytest.mark.requirement("L3-COL-001")
def test_a_repository_that_does_not_exist_degrades_the_row_not_the_run(tmp_path: Path) -> None:
    """The obligation that broke twice: one bad reference must not end the run.

    Against the live API rather than a stub, because both times it broke, the
    stub was answering something the real transport never sends.
    """
    result = run_scan(tmp_path, inventory=QUICK_INVENTORY)

    assert result.returncode == 4, "degraded, not aborted"
    rows = (tmp_path / "githubmetrics.csv").read_text(encoding="utf-8").splitlines()

    assert len(rows) == len(QUICK_INVENTORY) + 1, "one row per reference, plus the header"
    assert (tmp_path / "pypa" / "virtualenv.json").is_file(), "the good ones are documented"
    assert not (tmp_path / "ghost").exists(), "the absent one gets a row and no document"


@pytest.mark.skipif(PROFILE != "quick", reason="SOAK_PROFILE is not 'quick'")
@pytest.mark.requirement("L3-LOG-004")
def test_a_live_run_is_quiet_at_the_default_level(tmp_path: Path) -> None:
    """geopy logged twenty-two unformatted lines per unresolvable location.

    That was invisible to the ordinary suite because nothing there geocodes
    against a service that can fail. Here the geocoder is real, so if a
    library starts writing outside the package's handler again, it shows up
    as unformatted lines on stderr.
    """
    result = run_scan(tmp_path, inventory=QUICK_INVENTORY)

    unformatted = [
        line
        for line in result.stderr.splitlines()
        if line.strip() and not line.startswith(("INFO", "WARNING", "ERROR", "DEBUG", "!"))
    ]

    assert "Traceback" not in result.stderr, "a traceback is never a correct outcome"
    assert not unformatted, f"output bypassing the package logger: {unformatted[:5]}"


@pytest.mark.skipif(PROFILE != "quick", reason="SOAK_PROFILE is not 'quick'")
@pytest.mark.requirement("L3-STA-011", "L3-STA-012")
def test_the_identity_breakdown_adds_up_on_real_data(tmp_path: Path) -> None:
    """The buckets are derived, so one that nobody populates hides in the
    remainder - which is exactly how `unresolvable_accounts` stayed zero.

    The sum is the property worth checking: it holds whatever the counts are,
    so it does not need pinning to a repository's current contributors.
    """
    run_scan(tmp_path, inventory=QUICK_INVENTORY)

    for repository in statistics_of(tmp_path)["repository_statistics"]:
        breakdown = repository.get("contributors", {}).get("breakdown")
        if breakdown is None:
            continue
        assert (
            sum(breakdown.values()) == repository["contributors"]["identities"]
        ), f"{repository['repository']}: the breakdown must account for every identity"


# ---------------------------------------------------------------------------
# exhaustion: manual, and expensive on purpose
# ---------------------------------------------------------------------------


@pytest.mark.skipif(PROFILE != "exhaustion", reason="SOAK_PROFILE is not 'exhaustion'")
@pytest.mark.requirement("L3-EXH-004", "L3-EXH-005")
def test_a_run_that_reaches_the_wall_stops_and_says_so(tmp_path: Path) -> None:
    """The defect this whole series turned on: the guard could not see the wall.

    `--on-exhaustion partial` rather than `wait`, because `wait` would sleep to
    the reset and this would take an hour longer to tell us the same thing:
    that the budget was observed running out, that the run stopped rather than
    filling the file with empty rows, and that the artifact says so.

    Expensive by design. `--deep-attribution` on a large history spends a point
    per hundred commits, which is the only way to reach the wall in minutes
    rather than in five hundred repositories.
    """
    result = run_scan(
        tmp_path,
        "--deep-attribution",
        "--on-exhaustion",
        "partial",
        inventory=DEEP_INVENTORY,
    )

    assert result.returncode in (0, 4), result.stderr[-2000:]
    budget = statistics_of(tmp_path)["budget"]

    if not budget["exhausted"]:
        pytest.skip(
            "the token had more budget than this inventory could spend; "
            "re-run against a token that has already been used, or add repositories"
        )

    assert budget["incomplete_because_exhausted"] is True, (
        "a run that hit the wall must say so - reporting False here is the "
        "v0.6.4 defect, where statistics.json asserted the budget had held"
    )
    assert (tmp_path / "githubmetrics.csv").is_file(), "a stopped run still writes its file"
