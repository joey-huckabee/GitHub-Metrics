"""Tests for the `bands` command and `--token-file`."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from github_metrics.cli import BAND_TABLES, main

SECRET = "ghp_aTokenReadFromAFile1234567890"


def run(args: list[str]) -> Any:
    """Invoke the CLI against an env file that supplies nothing."""
    return CliRunner().invoke(main, ["--env-file", os.devnull, *args])


# ---------------------------------------------------------------------------
# bands
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-CLI-007")
def test_every_scoring_table_is_reachable() -> None:
    """The command is what gives each `describe_bands` a caller.

    Five modules define one. Before this command existed, three of them were
    unreachable from anywhere but their own tests - the same defect that was
    flagged for `closed_issues` and then repeated three times.
    """
    assert set(BAND_TABLES) == {
        "closed-issues",
        "releases",
        "last-update",
        "maturity",
        "popularity",
    }

    for metric in BAND_TABLES:
        result = run(["bands", metric])
        assert result.exit_code == 0, metric
        assert result.output.strip(), metric


@pytest.mark.requirement("L3-CLI-007")
def test_no_argument_prints_every_table() -> None:
    result = run(["bands"])

    assert result.exit_code == 0
    for fragment in ("closed-issue bands", "release bands", "last-update bands", "maturity bands"):
        assert fragment in result.output
    assert "star and fork bands" in result.output


@pytest.mark.requirement("L3-CLI-007")
def test_the_tables_carry_their_boundaries() -> None:
    assert ">=500" in run(["bands", "closed-issues"]).output
    assert ">=80" in run(["bands", "releases"]).output
    assert "26280" in run(["bands", "last-update"]).output
    assert "0.25" in run(["bands", "maturity"]).output
    assert "300" in run(["bands", "popularity"]).output


@pytest.mark.requirement("L3-CLI-007")
def test_it_needs_no_token() -> None:
    # Reviewing the scoring model should not require credentials.
    result = run(["bands", "maturity"])

    assert result.exit_code == 0
    assert "GITHUB_TOKEN" not in result.output


@pytest.mark.requirement("L3-CLI-007")
def test_an_unknown_metric_is_a_usage_error() -> None:
    result = run(["bands", "not-a-metric"])

    assert result.exit_code != 0
    # click lists the valid choices, so the message is self-correcting.
    assert "maturity" in result.output


# ---------------------------------------------------------------------------
# --token-file
# ---------------------------------------------------------------------------


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Accept any token without reaching the network."""
    monkeypatch.setattr("github_metrics.cli.verify_credentials", lambda _settings: None)
    monkeypatch.setattr("github_metrics.cli.GitHubClient", lambda _settings: _NullClient())


class _NullClient:
    def __enter__(self) -> _NullClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    @staticmethod
    def rate_limit_remaining() -> int:
        """Stand in for the real call."""
        return 4_242


@pytest.mark.requirement("L3-CFG-008")
@pytest.mark.usefixtures("offline")
def test_a_token_is_read_from_a_file(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    # Written by `echo`, so it ends in a newline. A token with a trailing
    # newline is rejected by the API for reasons that are hard to see.
    token_file.write_text(f"{SECRET}\n", encoding="utf-8")

    result = run(["--token-file", str(token_file), "rate-limit"])

    assert result.exit_code == 0
    assert "4242" in result.output


@pytest.mark.requirement("L3-CFG-008")
def test_an_empty_token_file_is_a_usage_error(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("   \n", encoding="utf-8")

    result = run(["--token-file", str(token_file), "rate-limit"])

    assert result.exit_code != 0
    assert "is empty" in result.output


@pytest.mark.requirement("L3-CFG-008")
def test_an_unreadable_token_file_is_a_usage_error(tmp_path: Path) -> None:
    result = run(["--token-file", str(tmp_path / "absent"), "rate-limit"])

    assert result.exit_code != 0
    assert "could not read" in result.output


@pytest.mark.requirement("L3-CFG-008")
def test_supplying_both_forms_is_refused(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text(SECRET, encoding="utf-8")

    result = run(["--token", "x", "--token-file", str(token_file), "rate-limit"])

    # Two answers to one question can only be a mistake, and guessing which
    # was meant would be worse than saying so.
    assert result.exit_code != 0
    assert "not both" in result.output


@pytest.mark.requirement("L3-CFG-008")
@pytest.mark.usefixtures("offline")
def test_the_token_from_a_file_is_never_echoed(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text(SECRET, encoding="utf-8")

    result = run(["--token-file", str(token_file), "rate-limit"])

    assert SECRET not in result.output
