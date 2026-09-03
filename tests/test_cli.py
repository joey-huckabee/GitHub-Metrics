"""Tests for :mod:`github_metrics.cli`."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from github_metrics import __version__
from github_metrics.cli import main


def test_version_flag() -> None:
    result = CliRunner().invoke(main, ["--version"])

    assert result.exit_code == 0
    assert __version__ in result.output


def test_short_version_flag() -> None:
    result = CliRunner().invoke(main, ["-V"])

    assert result.exit_code == 0
    assert __version__ in result.output


def test_help_shows_the_version() -> None:
    result = CliRunner().invoke(main, ["-h"])

    assert result.exit_code == 0
    assert f"(v{__version__})" in result.output


def test_help_lists_commands() -> None:
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    listed = {
        line.strip().split()[0]
        for line in result.output.splitlines()
        if line.startswith("  ") and line.strip() and not line.strip().startswith("-")
    }
    for command in ("scan", "validate", "bands", "rate-limit"):
        assert command in listed, command

    # Retired names, and never recycled. `repo` was scaffolding collecting a
    # different set of fields; `metrics` became `scan` so that the command and
    # the `scan_id` it stamps share a word; `contributors` folded into `scan`
    # once the per-repository document turned out to be the metrics row plus a
    # contributor block, which one run has to produce under one identity.
    #
    # Matched against the parsed command list rather than the whole help text,
    # which is how this check previously passed for a `metrics` command that
    # had already been renamed: the word still appeared in the group summary.
    for retired in ("repo", "metrics", "contributors"):
        assert retired not in listed, retired


def test_missing_token_is_a_friendly_error(empty_env_file: Path) -> None:
    result = CliRunner().invoke(main, ["--env-file", str(empty_env_file), "rate-limit"])

    assert result.exit_code != 0
    assert "GITHUB_TOKEN" in result.output
