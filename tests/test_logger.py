"""Tests for :mod:`github_metrics.logger`."""

from __future__ import annotations

import io

import pytest

from github_metrics.logger import Logger, LogLevels, reset_logger


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("debug", LogLevels.DEBUG),
        ("INFO", LogLevels.INFO),
        ("  warning  ", LogLevels.WARNING),
        ("CRITICAL", LogLevels.CRITICAL),
    ],
)
@pytest.mark.requirement("L3-LOG-003")
def test_from_name_resolves_known_levels(name: str, expected: int) -> None:
    assert LogLevels.from_name(name) == expected


@pytest.mark.requirement("L3-LOG-003")
def test_from_name_falls_back_for_unknown_levels() -> None:
    assert LogLevels.from_name("not-a-level") == LogLevels.INFO
    assert LogLevels.from_name("not-a-level", default=LogLevels.ERROR) == LogLevels.ERROR


@pytest.mark.requirement("L3-LOG-003")
def test_reset_logger_writes_to_the_given_stream() -> None:
    stream = io.StringIO()

    logger = reset_logger(LogLevels.DEBUG, stream=stream, fmt="%(levelname)s|%(message)s")
    logger.debug("hello")

    assert stream.getvalue().strip() == "DEBUG|hello"


@pytest.mark.requirement("L3-LOG-003")
def test_reset_logger_is_idempotent() -> None:
    reset_logger(stream=io.StringIO())
    logger = reset_logger(stream=io.StringIO())

    assert len(logger.handlers) == 1
    assert logger.propagate is False


@pytest.mark.requirement("L3-LOG-003")
def test_reset_logger_filters_below_the_minimum_level() -> None:
    stream = io.StringIO()

    logger = reset_logger(LogLevels.WARNING, stream=stream, fmt="%(message)s")
    logger.info("quiet")
    logger.warning("loud")

    assert stream.getvalue().strip() == "loud"


def test_wrapper_logs_and_warns_that_it_is_deprecated() -> None:
    wrapper = Logger(LogLevels.INFO)
    stream = io.StringIO()
    wrapper.logger = reset_logger(LogLevels.INFO, stream=stream, fmt="%(message)s")

    with pytest.deprecated_call():
        wrapper.log(LogLevels.INFO, "through the wrapper")

    assert stream.getvalue().strip() == "through the wrapper"
