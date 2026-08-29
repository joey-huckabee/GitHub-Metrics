"""Tests for :mod:`github_metrics.config`."""

from __future__ import annotations

from pathlib import Path

import pytest

from github_metrics.config import DEFAULT_API_URL, ConfigError, Settings


def test_from_env_reads_token(monkeypatch: pytest.MonkeyPatch, empty_env_file: Path) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "  ghp_secret  ")

    settings = Settings.from_env(empty_env_file)

    assert settings.github_token == "ghp_secret"
    assert settings.api_url == DEFAULT_API_URL
    assert settings.log_level == "INFO"


def test_from_env_honours_overrides(monkeypatch: pytest.MonkeyPatch, empty_env_file: Path) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
    monkeypatch.setenv("GITHUB_API_URL", "https://ghe.example.com/api/v3")
    monkeypatch.setenv("LOG_LEVEL", "debug")

    settings = Settings.from_env(empty_env_file)

    assert settings.api_url == "https://ghe.example.com/api/v3"
    assert settings.log_level == "DEBUG"


@pytest.mark.parametrize("value", ["", "   "])
def test_missing_token_raises(
    monkeypatch: pytest.MonkeyPatch, empty_env_file: Path, value: str
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", value)

    with pytest.raises(ConfigError, match="GITHUB_TOKEN"):
        Settings.from_env(empty_env_file)
