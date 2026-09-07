"""Tests for :mod:`github_metrics.logger`."""

from __future__ import annotations

import io
import logging

import pytest

from github_metrics.logger import (
    THIRD_PARTY_LOGGERS,
    Logger,
    LogLevels,
    reset_logger,
)


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
    # Reconfiguring the package logger is enough: the wrapper holds the same
    # object `reset_logger` returns, so there is nothing to reach in and
    # replace. Assigning to the wrapper's own attribute was reaching past the
    # thing under test to set up the thing under test.
    reset_logger(LogLevels.INFO, stream=stream, fmt="%(message)s")

    with pytest.deprecated_call():
        wrapper.log(LogLevels.INFO, "through the wrapper")

    assert stream.getvalue().strip() == "through the wrapper"


# ---------------------------------------------------------------------------
# Libraries that log without a handler of their own
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-LOG-004")
def test_an_adopted_library_writes_through_the_package_handler() -> None:
    """Otherwise it reaches Python's handler of last resort.

    That writes to stderr at WARNING with no formatter, so the output carried
    neither this package's format nor its level. `geopy` is the one library
    here that attaches no `NullHandler`, and `geopy.extra.rate_limiter` logs
    every retry with `exc_info=True` - two full stack traces per location the
    service could not resolve.
    """
    buffer = io.StringIO()
    reset_logger(LogLevels.DEBUG, stream=buffer)

    logging.getLogger("geopy.extra.rate_limiter").warning("caught an error, retrying")

    written = buffer.getvalue()
    assert "caught an error, retrying" in written
    assert written.startswith("WARNING"), "adopted output must carry our format"


@pytest.mark.requirement("L3-LOG-004")
def test_an_adopted_library_is_quiet_unless_someone_is_diagnosing() -> None:
    """`geo.py` already reports a failed lookup once, naming the location.

    geopy's version is the same event twice more with a stack trace attached,
    so it is held at ERROR until the operator asks for DEBUG.
    """
    buffer = io.StringIO()
    reset_logger(LogLevels.INFO, stream=buffer)

    logging.getLogger("geopy.extra.rate_limiter").warning("caught an error, retrying")

    assert buffer.getvalue() == ""


@pytest.mark.requirement("L3-LOG-004")
def test_an_adopted_library_never_reaches_the_last_resort_handler() -> None:
    """The property that made the level uncontrollable.

    A logger with no handler anywhere in its chain is emitted by
    `logging.lastResort`, whose level is WARNING and whose formatter is none -
    so `LOG_LEVEL` could not silence it and the format could not reach it.
    """
    reset_logger(LogLevels.INFO, stream=io.StringIO())

    for name in THIRD_PARTY_LOGGERS:
        adopted = logging.getLogger(name)
        assert adopted.handlers, f"{name} would fall through to lastResort"
        assert not adopted.propagate, f"{name} would also re-emit through root"


@pytest.mark.requirement("L3-LOG-004")
def test_only_libraries_without_a_handler_are_adopted() -> None:
    """Adopting one that never emits would claim behaviour that does not happen.

    `requests`, `urllib3`, `charset_normalizer` and PyGithub each attach a
    `NullHandler`, so they never reach the last-resort handler and are not
    listed.
    """
    for name in ("requests", "urllib3", "github"):
        assert name not in THIRD_PARTY_LOGGERS


@pytest.mark.requirement("L3-LOG-004")
def test_a_library_warning_never_escapes_to_the_real_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The defect stated as behaviour, independent of how it is fixed.

    Records are meant to go where `reset_logger` was told to put them. A
    library whose logger has no handler goes to `logging.lastResort` instead,
    which writes to the process's stderr regardless - which is how twenty-two
    unformatted lines per failing location arrived alongside JSON on stdout.
    """
    buffer = io.StringIO()
    reset_logger(LogLevels.DEBUG, stream=buffer)

    logging.getLogger("geopy.extra.rate_limiter").warning("caught an error, retrying")

    assert capsys.readouterr().err == "", "the record bypassed the configured stream"
    assert "caught an error, retrying" in buffer.getvalue()
