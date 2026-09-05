"""Tests for :mod:`github_metrics.config`."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from github_metrics.config import DEFAULT_API_URL, Settings
from github_metrics.errors import MissingCredentialsError


@pytest.mark.requirement("L3-CFG-001")
def test_from_env_reads_token(monkeypatch: pytest.MonkeyPatch, empty_env_file: Path) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "  ghp_secret  ")

    settings = Settings.from_env(empty_env_file)

    assert settings.github_token == "ghp_secret"
    assert settings.api_url == DEFAULT_API_URL
    assert settings.log_level == "INFO"


@pytest.mark.requirement("L3-CFG-001")
def test_from_env_honours_overrides(monkeypatch: pytest.MonkeyPatch, empty_env_file: Path) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
    monkeypatch.setenv("GITHUB_API_URL", "https://ghe.example.com/api/v3")
    monkeypatch.setenv("LOG_LEVEL", "debug")

    settings = Settings.from_env(empty_env_file)

    assert settings.api_url == "https://ghe.example.com/api/v3"
    assert settings.log_level == "DEBUG"


@pytest.mark.parametrize("value", ["", "   "])
@pytest.mark.requirement("L3-CFG-001")
def test_missing_token_raises(
    monkeypatch: pytest.MonkeyPatch, empty_env_file: Path, value: str
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", value)

    with pytest.raises(MissingCredentialsError, match="GM-CFG-001"):
        Settings.from_env(empty_env_file)


@pytest.mark.requirement("L3-CFG-001")
def test_an_explicit_token_beats_the_environment(
    monkeypatch: pytest.MonkeyPatch, empty_env_file: Path
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "from-the-environment")

    settings = Settings.from_env(empty_env_file, token="from-the-flag")

    # An explicit argument beats ambient configuration, so a caller can
    # override a stale .env without editing it.
    assert settings.github_token == "from-the-flag"


@pytest.mark.requirement("L3-CFG-001")
def test_an_explicit_token_works_with_no_environment_at_all(empty_env_file: Path) -> None:
    settings = Settings.from_env(empty_env_file, token="  from-the-flag  ")

    assert settings.github_token == "from-the-flag"


@pytest.mark.requirement("L3-CFG-001")
@pytest.mark.parametrize("blank", ["", "   ", None])
def test_a_blank_explicit_token_falls_back_to_the_environment(
    monkeypatch: pytest.MonkeyPatch, empty_env_file: Path, blank: str | None
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "from-the-environment")

    settings = Settings.from_env(empty_env_file, token=blank)

    assert settings.github_token == "from-the-environment"


@pytest.mark.requirement("L3-CFG-001")
def test_the_token_source_is_logged_but_never_the_token(
    caplog: pytest.LogCaptureFixture, empty_env_file: Path
) -> None:
    secret = "ghp_thisMustNeverAppearInALogLine"

    with caplog.at_level(logging.DEBUG, logger="github_metrics.config"):
        Settings.from_env(empty_env_file, token=secret)

    assert "--token" in caplog.text
    assert secret not in caplog.text


@pytest.mark.requirement("L3-MET-020")
def test_the_geocode_cache_defaults_to_the_platform_location(
    monkeypatch: pytest.MonkeyPatch, empty_env_file: Path
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
    monkeypatch.delenv("GEOCODE_CACHE_PATH", raising=False)

    settings = Settings.from_env(empty_env_file)

    assert settings.geocode_cache_path is not None
    assert settings.geocode_cache_path.name == "geocode.json"


@pytest.mark.requirement("L3-MET-020")
def test_the_geocode_cache_location_can_be_moved(
    monkeypatch: pytest.MonkeyPatch, empty_env_file: Path, tmp_path: Path
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
    monkeypatch.setenv("GEOCODE_CACHE_PATH", str(tmp_path / "elsewhere.json"))

    assert Settings.from_env(empty_env_file).geocode_cache_path == tmp_path / "elsewhere.json"


@pytest.mark.requirement("L3-MET-020")
def test_an_empty_geocode_cache_path_turns_persistence_off(
    monkeypatch: pytest.MonkeyPatch, empty_env_file: Path
) -> None:
    """Deleting the file would only make the next run rebuild it."""
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
    monkeypatch.setenv("GEOCODE_CACHE_PATH", "   ")

    assert Settings.from_env(empty_env_file).geocode_cache_path is None
