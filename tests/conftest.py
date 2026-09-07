"""Shared fixtures for the github-metrics test suite."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from github_metrics.config import Settings
from github_metrics.logger import PACKAGE_LOGGER_NAME

ENV_VARS = ("GITHUB_TOKEN", "GITHUB_API_URL", "GEOCODER_USER_AGENT", "LOG_LEVEL")

GEOCODE_CACHE_VAR = "GEOCODE_CACHE_PATH"
"""Set to empty for every test, and deliberately **not** in `ENV_VARS`.

Deleting it is the wrong move and the opposite of isolation: `config` reads an
absent variable as "use the platform default", which is the developer's real
cache - `~/AppData/Local/github-metrics/geocode.json` or its equivalent. An
empty value is what turns persistence off, so the tests get a cache with no
path: nothing to read, and `save` returns early because there is nowhere to
write.

Measured before this existed: running `tests/test_cli_scan.py` alone opened the
real cache 29 times. Nothing failed, because a scan test geocodes nothing and
`save` is guarded by a dirty flag - so the suite was reading a developer's file
and would have written it the moment a test resolved a location.
"""


@pytest.fixture
def empty_env_file(tmp_path: Path) -> Path:
    """An empty .env so tests never pick up the developer's real one."""
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    return env_file


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test from the developer's own environment."""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    # Set, not deleted: see `GEOCODE_CACHE_VAR`.
    monkeypatch.setenv(GEOCODE_CACHE_VAR, "")


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
