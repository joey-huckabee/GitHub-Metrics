"""Shared fixtures for the github-metrics test suite."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from github_metrics.config import Settings
from github_metrics.logger import PACKAGE_LOGGER_NAME

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


@pytest.fixture(autouse=True)
def restore_package_logger() -> Iterator[None]:
    """Leave the package logger exactly as each test found it.

    `reset_logger` sets `propagate = False` on the `github_metrics` logger. Any
    test that runs the CLI therefore reconfigures a process-wide singleton, and
    without this fixture it would stop pytest's `caplog` from seeing records
    emitted by tests that run afterwards.
    """
    logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    handlers, level, propagate = list(logger.handlers), logger.level, logger.propagate
    yield
    logger.handlers = handlers
    logger.setLevel(level)
    logger.propagate = propagate
