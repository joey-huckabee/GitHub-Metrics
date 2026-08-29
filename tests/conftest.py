"""Shared fixtures for the github-metrics test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from github_metrics.config import Settings

ENV_VARS = ("GITHUB_TOKEN", "GITHUB_API_URL", "GEOCODER_USER_AGENT", "LOG_LEVEL")


@pytest.fixture
def empty_env_file(tmp_path: Path) -> Path:
    """An empty .env so tests never pick up the developer's real one."""
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    return env_file


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove project environment variables for every test."""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def settings() -> Settings:
    """Settings with a dummy token, for tests that never call the API."""
    return Settings(github_token="test-token")
