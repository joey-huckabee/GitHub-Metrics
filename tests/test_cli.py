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
    assert "repo" in result.output
    assert "rate-limit" in result.output


def test_missing_token_is_a_friendly_error(empty_env_file: Path) -> None:
    result = CliRunner().invoke(main, ["--env-file", str(empty_env_file), "rate-limit"])

    assert result.exit_code != 0
    assert "GITHUB_TOKEN" in result.output
