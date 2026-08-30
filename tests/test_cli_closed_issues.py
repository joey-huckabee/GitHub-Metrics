"""Tests for the `github-metrics closed-issues` command."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from github_metrics.cli import EXIT_REPOSITORY_UNFETCHABLE, main
from github_metrics.collect.closed_issues import ClosedIssueCounts
from github_metrics.errors import RepositoryNotFoundError


class _NullClient:
    """A client that is never asked anything; collection itself is stubbed."""

    def __enter__(self) -> _NullClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None


@pytest.fixture
def token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Satisfy the credential check without reaching the network."""
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the client so no command under test opens a connection."""
    monkeypatch.setattr("github_metrics.cli.GitHubClient", lambda _settings: _NullClient())


def stub_counts(monkeypatch: pytest.MonkeyPatch, counts: ClosedIssueCounts) -> None:
    """Make collection return canned counts."""

    def _collect(_client: Any, _owner: str, _repoid: str) -> ClosedIssueCounts:
        return counts

    monkeypatch.setattr("github_metrics.cli.get_closed_issues", _collect)


def stub_failure(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    """Make collection raise."""

    def _collect(_client: Any, _owner: str, _repoid: str) -> ClosedIssueCounts:
        raise error

    monkeypatch.setattr("github_metrics.cli.get_closed_issues", _collect)


def run(args: list[str], env_file: Path) -> Any:
    """Invoke the CLI with an explicit env file."""
    return CliRunner().invoke(main, ["--env-file", str(env_file), *args])


@pytest.mark.requirement("L3-CLI-005")
@pytest.mark.usefixtures("token", "offline")
def test_it_reports_counts_the_tracker_state_and_the_weight(
    monkeypatch: pytest.MonkeyPatch, empty_env_file: Path
) -> None:
    stub_counts(monkeypatch, ClosedIssueCounts(closed=1429, open=0, issues_enabled=True))

    result = run(["closed-issues", "pypa/virtualenv"], empty_env_file)

    assert result.exit_code == 0
    assert "pypa/virtualenv" in result.output
    assert "1429" in result.output
    assert "enabled" in result.output
    assert "1.0" in result.output


@pytest.mark.requirement("L3-CLI-005")
@pytest.mark.usefixtures("token", "offline")
def test_explain_appends_the_bands(monkeypatch: pytest.MonkeyPatch, empty_env_file: Path) -> None:
    stub_counts(monkeypatch, ClosedIssueCounts(closed=250, open=3, issues_enabled=True))

    plain = run(["closed-issues", "a/b"], empty_env_file)
    explained = run(["closed-issues", "a/b", "--explain"], empty_env_file)

    # The bands are what make a surprising weight diagnosable without reading
    # the source.
    assert "closed-issue bands" not in plain.output
    assert "closed-issue bands" in explained.output
    assert ">=500" in explained.output


@pytest.mark.requirement("L3-CLI-005")
@pytest.mark.usefixtures("token", "offline")
def test_json_carries_the_same_values(
    monkeypatch: pytest.MonkeyPatch, empty_env_file: Path
) -> None:
    stub_counts(monkeypatch, ClosedIssueCounts(closed=3770, open=691, issues_enabled=True))

    result = run(["closed-issues", "cline/cline", "--format", "json"], empty_env_file)

    payload = json.loads(result.stdout)
    assert payload == {
        "owner": "cline",
        "repoid": "cline",
        "closed_issues": 3770,
        "open_issues": 691,
        "issues_enabled": True,
        "weight": 1.0,
        "bands": None,
    }


@pytest.mark.requirement("L3-CLI-005")
@pytest.mark.usefixtures("token", "offline")
def test_json_includes_the_bands_only_when_explained(
    monkeypatch: pytest.MonkeyPatch, empty_env_file: Path
) -> None:
    stub_counts(monkeypatch, ClosedIssueCounts(closed=10, open=0, issues_enabled=True))

    result = run(["closed-issues", "a/b", "--format", "json", "--explain"], empty_env_file)

    bands = json.loads(result.stdout)["bands"]
    assert bands is not None
    assert bands[0] == {"below": 20, "weight": 0.1}


@pytest.mark.requirement("L3-CLI-005")
@pytest.mark.usefixtures("token", "offline")
def test_a_disabled_tracker_is_called_out(
    monkeypatch: pytest.MonkeyPatch, empty_env_file: Path
) -> None:
    stub_counts(monkeypatch, ClosedIssueCounts(closed=0, open=0, issues_enabled=False))

    result = run(["closed-issues", "org/mirror"], empty_env_file)

    assert "DISABLED" in result.output
    # Zero here describes configuration, not maintenance, and the reader should
    # not have to know that.
    assert "configuration fact" in result.output


@pytest.mark.requirement("L3-CLI-005")
@pytest.mark.usefixtures("token", "offline")
def test_an_unreadable_repository_exits_four(
    monkeypatch: pytest.MonkeyPatch, empty_env_file: Path
) -> None:
    stub_failure(monkeypatch, RepositoryNotFoundError("ghost/missing: could not resolve"))

    result = run(["closed-issues", "ghost/missing"], empty_env_file)

    # Degraded, not aborted: a stale inventory entry is an expected outcome.
    assert result.exit_code == EXIT_REPOSITORY_UNFETCHABLE
    assert "GM-COL-001" in result.output


@pytest.mark.requirement("L3-CLI-005")
@pytest.mark.usefixtures("token", "offline")
@pytest.mark.parametrize("slug", ["not-a-slug", ""])
def test_a_malformed_slug_is_a_usage_error(slug: str, empty_env_file: Path) -> None:
    result = run(["closed-issues", slug], empty_env_file)

    assert result.exit_code != 0
    assert "OWNER/REPOID" in result.output


@pytest.mark.requirement("L3-CLI-005")
def test_it_appears_in_the_command_list() -> None:
    result = CliRunner().invoke(main, ["-h"])

    assert "closed-issues" in result.output
