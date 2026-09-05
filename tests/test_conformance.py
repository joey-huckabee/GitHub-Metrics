"""Conformance: a known input must keep producing byte-identical artifacts.

Every other test in this suite checks a decision. This one checks the
**contract** - that the three artifacts a scan writes have not changed shape,
spelling, ordering or value for input that has not changed either.

It is the test that would have caught the things unit tests kept missing. A
column renamed, a key reordered, a null that became a zero, a percentage whose
denominator quietly changed: each of those passes every focused test in this
repository and changes what every downstream consumer reads.

Real data, no network
---------------------
The fixtures are **recorded from the live API**, not written from
documentation - stubs written from docs prove only that the code agrees with
what someone believed, and this project has been caught three times by exactly
that gap. `scripts/record-conformance.py` captures the traffic; this replays it.

Replay is deterministic and offline, so the suite runs in CI, in milliseconds,
and fails only for reasons that belong to the change under test.

What is normalised, and why only that
-------------------------------------
`scan_id`, `scan_date` and `duration_seconds` are properties of the *run*
rather than of the data, so they are pinned or normalised. `tool_version`
changes at every release and would otherwise make the golden files churn for
reasons no reviewer should have to read past.

**Nothing else is normalised.** Every measured value, every key, every
ordering, and the exit status are compared exactly.

When this test fails
--------------------
It means the output contract moved. Either that was the point - in which case
regenerate the golden files, read the diff carefully, and record the change in
`CHANGELOG.md` - or it was not, and the diff is the bug report.
"""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from click.testing import CliRunner
from github.GithubException import UnknownObjectException

from github_metrics.cli import EXIT_REPOSITORY_UNFETCHABLE, main
from github_metrics.model.scan import ScanIdentifier

CONFORMANCE = Path(__file__).parent / "conformance"
RECORDING = CONFORMANCE / "recording.json"
EXPECTED = CONFORMANCE / "expected"
INVENTORY = CONFORMANCE / "inventory.csv"
GEOCODE = CONFORMANCE / "geocode.json"

EXPECTED_DEEP = CONFORMANCE / "expected-deep"
INVENTORY_DEEP = CONFORMANCE / "inventory-deep.csv"
"""The second set, scanned with `--deep-attribution`.

It exists for two things the first set cannot show. `hukkin/tomli` has a **bot**
contributor, and the deep route learns that from the reserved `[bot]` login
suffix rather than from the account type the contributors endpoint reports -
two different mechanisms that must agree, and did not when the feature landed.
Its history is 333 commits, four pages, short enough to record.
"""

# Pinned so the artifacts are reproducible. A run identity is a property of the
# run, not of the data, and leaving it random would make every field that
# carries it differ on every execution.
SCAN_ID = UUID("00000000-0000-4000-8000-000000000001")
SCAN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)

VOLATILE = re.compile(r'"(tool_version|duration_seconds)": [^,\n]+')
"""Fields normalised before comparison.

`tool_version` changes at every release and `duration_seconds` at every run.
Both are real and worth publishing; neither says anything about whether the
contract held.
"""


class _Repository:
    """Stands in for PyGithub's repository, serving a recorded account list."""

    def __init__(self, accounts: list[dict[str, Any]]) -> None:
        self._accounts = [_Account(entry) for entry in accounts]

    def get_contributors(self) -> list[_Account]:
        """The recorded list, sliceable exactly as the real one is."""
        return self._accounts


class _Account:
    """One recorded contributor, exposing what the collector reads."""

    def __init__(self, entry: dict[str, Any]) -> None:
        self.login = entry["login"]
        self.id = entry["id"]
        self.contributions = entry["contributions"]
        self.type = entry["type"]


class ReplayClient:
    """Serves recorded traffic, and refuses anything it was not asked before.

    Refusing is deliberate. A silent fallback would let a query the recording
    does not cover pass as an empty answer, and the artifacts would come out
    subtly wrong with nothing to say why.
    """

    def __init__(self, recording: dict[str, Any]) -> None:
        self.recording = recording
        self.unmatched: list[str] = []

    def graphql(self, query: str, variables: dict[str, Any]) -> tuple[Any, Any]:
        """Answer one GraphQL exchange from the recording."""
        key = json.dumps({"query": " ".join(query.split()), "variables": variables}, sort_keys=True)
        entry = self.recording["graphql"].get(key)
        if entry is None:
            self.unmatched.append(key)
            raise AssertionError(f"no recorded GraphQL response for {key}")
        if "error" in entry:
            raise UnknownObjectException(404, entry.get("data"), {}, "recorded failure")
        return {}, entry["payload"]

    def contributors_page(self, slug: str, **kwargs: Any) -> tuple[Any, Any]:
        """Answer one contributors page from the recording."""
        key = json.dumps(
            {
                "slug": slug,
                "page": kwargs.get("page", 1),
                "per_page": kwargs.get("per_page", 100),
                "anonymous": bool(kwargs.get("anonymous", False)),
            },
            sort_keys=True,
        )
        entry = self.recording["pages"].get(key)
        if entry is None:
            # Past the recorded pages: an empty page, which is what GitHub
            # returns and what ends a walk.
            return {}, []
        return entry["headers"], entry["payload"]

    def repository(self, slug: str) -> _Repository:
        """The recorded contributor list for one repository."""
        return _Repository(self.recording["contributors"].get(slug, []))

    @staticmethod
    def graphql_points_remaining() -> int:
        """A budget that never runs short, so the policy never engages."""
        return 5000

    @staticmethod
    def graphql_budget() -> tuple[int, None]:
        """As above, with no reset time to wait for."""
        return 5000, None

    @staticmethod
    def rate_limit_remaining() -> int:
        """As above, for REST."""
        return 5000

    def __enter__(self) -> ReplayClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None


@pytest.fixture
def replay(monkeypatch: pytest.MonkeyPatch) -> ReplayClient:
    """Replace the network, the clock and the run identity."""
    recording = json.loads(RECORDING.read_text(encoding="utf-8"))
    client = ReplayClient(recording)

    monkeypatch.setattr("github_metrics.cli.GitHubClient", lambda _settings: client)
    monkeypatch.setattr("github_metrics.cli.verify_credentials", lambda _settings: None)
    # A pinned identity: every artifact carries it, so leaving it random would
    # make all three differ on every run for no reason anyone cares about.
    monkeypatch.setattr(
        "github_metrics.cli.ScanIdentifier",
        lambda: ScanIdentifier(scan_id=SCAN_ID, scan_date=SCAN_DATE),
    )
    # Freeze the cache's clock at the scan date, which is also the timestamp
    # every fixture entry carries. Without this the fixture would age and, one
    # year after it was recorded, the suite would quietly start geocoding over
    # the network - passing, slowly, for the wrong reason.
    monkeypatch.setattr("github_metrics.geocache._utcnow", lambda: SCAN_DATE)
    return client


REGENERATE = os.environ.get("CONFORMANCE_REGENERATE") == "1"
"""Whether to rewrite the golden artifacts instead of comparing against them.

Regeneration lives here rather than in a script of its own, because a script
would have to reproduce this module's replay setup exactly and would drift from
it the first time either changed. `make conformance` sets this.

A golden file regenerated without reading the diff is a test that agrees with
whatever the code now does, which is no test at all - so this writes and then
the tests still run, and the review is `git diff tests/conformance/expected`.
"""


def compare(
    produced: Path,
    relative: Path,
    *,
    golden_root: Path = EXPECTED,
    normalised: bool = False,
) -> None:
    """Assert one artifact matches its golden copy, or rewrite that copy.

    Args:
        produced: The file the scan just wrote.
        relative: Where it belongs under the golden root.
        golden_root: Which golden set this artifact belongs to.
        normalised: Whether to blank the fields that change per run or release.
    """
    golden = golden_root / relative
    if REGENERATE:
        golden.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(produced, golden)
        return
    left = produced.read_text(encoding="utf-8")
    right = golden.read_text(encoding="utf-8")
    if normalised:
        left, right = normalise(left), normalise(right)
    assert left == right, f"{relative.as_posix()} no longer matches its golden copy"


def normalise(text: str) -> str:
    """Blank the fields that change for reasons unrelated to the contract."""
    return VOLATILE.sub(lambda match: f'"{match.group(1)}": "<normalised>"', text)


def run_scan(tmp_path: Path, *extra: str, inventory: Path = INVENTORY) -> Any:
    """Scan one conformance inventory into a temporary directory."""
    # A copy, so a run cannot write back into the committed fixture.
    cache = tmp_path / "geocode.json"
    if not cache.exists():
        shutil.copy2(GEOCODE, cache)
    return CliRunner().invoke(
        main,
        [
            "--env-file",
            str(tmp_path / "absent.env"),
            "--token",
            "ghp_conformance",
            "scan",
            str(inventory),
            "--output",
            str(tmp_path),
            *extra,
        ],
        env={"GEOCODE_CACHE_PATH": str(cache), "GEOCODER_USER_AGENT": "conformance"},
    )


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-CNF-001")
@pytest.mark.usefixtures("replay")
def test_the_tabular_artifact_is_unchanged(tmp_path: Path) -> None:
    """Twenty columns, one row per accepted reference, in input order."""
    run_scan(tmp_path)

    compare(tmp_path / "githubmetrics.csv", Path("githubmetrics.csv"))


@pytest.mark.requirement("L3-CNF-001")
@pytest.mark.usefixtures("replay")
def test_every_document_is_unchanged(tmp_path: Path) -> None:
    """Including which repositories get one, which is half the contract."""
    run_scan(tmp_path)

    written = sorted(
        path.relative_to(tmp_path) for path in tmp_path.rglob("*.json") if path.parent != tmp_path
    )
    for relative in written:
        compare(tmp_path / relative, relative)

    if not REGENERATE:
        # Which repositories get a document is half the contract, so an extra
        # one is as much a failure as a changed one.
        expected = sorted(
            path.relative_to(EXPECTED)
            for path in EXPECTED.rglob("*.json")
            if path.parent != EXPECTED
        )
        assert written == expected


@pytest.mark.requirement("L3-CNF-002")
@pytest.mark.usefixtures("replay")
def test_the_statistics_artifact_is_unchanged(tmp_path: Path) -> None:
    """Every bound the scan publishes about its own data."""
    run_scan(tmp_path)

    compare(tmp_path / "statistics.json", Path("statistics.json"), normalised=True)


@pytest.mark.requirement("L3-CNF-001")
@pytest.mark.usefixtures("replay")
def test_the_exit_status_is_unchanged(tmp_path: Path) -> None:
    """The inventory names a repository that does not exist, so the run is
    degraded rather than clean - and that is part of the contract too."""
    result = run_scan(tmp_path)

    assert result.exit_code == EXIT_REPOSITORY_UNFETCHABLE


@pytest.mark.requirement("L3-CNF-001")
@pytest.mark.usefixtures("replay")
def test_no_document_is_written_for_a_repository_that_was_not_collected(
    tmp_path: Path,
) -> None:
    """An absent file says 'named, not measured'; the row says which."""
    run_scan(tmp_path)

    assert not (tmp_path / "ghost").exists()
    rows = list(csv.DictReader((tmp_path / "githubmetrics.csv").read_text().splitlines()))
    ghost = next(row for row in rows if row["owner"] == "ghost")
    assert ghost["stars"] == ""
    assert ghost["url"].endswith("/ghost/no-such-repository-conformance")


@pytest.mark.requirement("L3-CNF-002")
@pytest.mark.usefixtures("replay")
def test_two_runs_of_one_input_are_identical(tmp_path: Path) -> None:
    """Determinism is a requirement, not a quality attribute: these files are
    diffed between runs, and a reordering would read as changed data."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    run_scan(first)
    run_scan(second)

    for path in sorted(first.rglob("*")):
        if path.is_dir():
            continue
        other = second / path.relative_to(first)
        assert normalise(path.read_text(encoding="utf-8")) == normalise(
            other.read_text(encoding="utf-8")
        )


@pytest.mark.requirement("L3-CNF-003")
@pytest.mark.usefixtures("replay")
def test_the_replay_covers_every_request_the_scan_makes(
    tmp_path: Path, replay: ReplayClient
) -> None:
    """A recording with a hole would let a query pass as an empty answer.

    That would produce artifacts that are subtly wrong with nothing saying so -
    the exact failure mode this suite exists to catch, occurring inside the
    suite itself.
    """
    run_scan(tmp_path)

    assert replay.unmatched == []


@pytest.mark.requirement("L3-CNF-003")
@pytest.mark.usefixtures("replay")
def test_the_run_reaches_no_network_at_all(tmp_path: Path) -> None:
    """The geocoder is the one component the replay client does not stand in
    for, so the artifact is asked to prove it stayed offline.

    `lookups` counts requests actually made. Zero means every location came
    from the shipped cache; anything else means this suite reached Nominatim,
    which would make it slow, flaky, and dependent on a third party's uptime
    for a result that has nothing to do with the change under test.
    """
    for inventory, extra in (
        (INVENTORY, ()),
        # The deep set is checked too: it was added later, its contributors
        # publish locations the first set does not, and the suite silently
        # started geocoding over the network until this covered it.
        (INVENTORY_DEEP, ("--deep-attribution",)),
    ):
        target = tmp_path / inventory.stem
        target.mkdir()
        run_scan(target, *extra, inventory=inventory)

        statistics = json.loads((target / "statistics.json").read_text(encoding="utf-8"))
        geocoding = statistics["geocoding"]

        assert geocoding["lookups"] == 0, f"{inventory.name} reached Nominatim"
        assert geocoding["service_failures"] == 0
        assert geocoding["cache_hits"] > 0, f"{inventory.name} used no cached location"


@pytest.mark.requirement("L3-CNF-003")
def test_every_fixture_the_suite_needs_is_present() -> None:
    """A missing fixture must fail here, not deep inside a comparison.

    This was not hypothetical. The golden CSV is called `githubmetrics.csv`,
    which `.gitignore` excludes so that scan output never lands in a commit -
    so it was silently left out. Locally the suite passed, because the
    untracked file was still on disk; on a clean checkout every artifact test
    failed with a `FileNotFoundError` from inside `compare`.

    The ignore rule now exempts `tests/conformance/expected/`, and this asserts
    the result rather than trusting it.
    """
    required = [
        INVENTORY,
        RECORDING,
        GEOCODE,
        EXPECTED / "githubmetrics.csv",
        EXPECTED / "statistics.json",
    ]

    missing = [path for path in required if not path.is_file()]

    assert not missing, f"conformance fixtures missing: {[p.name for p in missing]}"
    # At least one document, or the suite would be asserting nothing about them.
    assert any(path.parent != EXPECTED for path in EXPECTED.rglob("*.json"))


# ---------------------------------------------------------------------------
# The deep-attribution route
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-CNF-004")
@pytest.mark.usefixtures("replay")
def test_the_deep_route_artifacts_are_unchanged(tmp_path: Path) -> None:
    """A different population, and therefore a different everything.

    Walking the history finds contributors the endpoint's 500-email ceiling
    would have hidden, so the totals, the concentration and the coverage all
    differ from the same repository scanned normally. That is the point, and it
    is why `attribution.method` travels with the result.
    """
    run_scan(tmp_path, "--deep-attribution", inventory=INVENTORY_DEEP)

    compare(
        tmp_path / "githubmetrics.csv",
        Path("githubmetrics.csv"),
        golden_root=EXPECTED_DEEP,
    )
    compare(
        tmp_path / "statistics.json",
        Path("statistics.json"),
        golden_root=EXPECTED_DEEP,
        normalised=True,
    )
    for path in sorted(tmp_path.rglob("*.json")):
        if path.parent == tmp_path:
            continue
        compare(path, path.relative_to(tmp_path), golden_root=EXPECTED_DEEP)


@pytest.mark.requirement("L3-CNF-004")
@pytest.mark.usefixtures("replay")
def test_the_deep_route_records_the_method_that_produced_it(tmp_path: Path) -> None:
    """Two runs of one repository by different methods are not comparable, and
    only this field stops them being diffed as though they were."""
    run_scan(tmp_path, "--deep-attribution", inventory=INVENTORY_DEEP)

    statistics = json.loads((tmp_path / "statistics.json").read_text(encoding="utf-8"))

    assert statistics["repository_statistics"][0]["attribution"]["method"] == "commit_history"


@pytest.mark.requirement("L3-CNF-005")
@pytest.mark.usefixtures("replay")
def test_both_routes_find_the_same_bots(tmp_path: Path) -> None:
    """The regression this fixture exists for.

    The contributors endpoint reports an account type; `Commit.author.user`
    does not, so the deep route reads the reserved `[bot]` login suffix
    instead. When the feature landed the second mechanism was missing entirely
    and a deep run reported **zero** bots for a repository carrying four - a
    number that looked measured and was not.

    Two mechanisms, one answer. Nothing but a test comparing the routes over
    one repository would notice them diverging again.
    """
    listed = tmp_path / "listed"
    deep = tmp_path / "deep"
    listed.mkdir()
    deep.mkdir()

    run_scan(listed, inventory=INVENTORY_DEEP)
    run_scan(deep, "--deep-attribution", inventory=INVENTORY_DEEP)

    def bots_of(root: Path) -> set[str]:
        statistics = json.loads((root / "statistics.json").read_text(encoding="utf-8"))
        return set(statistics["repository_statistics"][0]["bots"]["logins"])

    from_list = bots_of(listed)

    assert from_list, "the fixture repository is supposed to have a bot"
    assert bots_of(deep) == from_list
