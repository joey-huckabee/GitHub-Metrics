"""Logging for GitHub Metrics."""

from __future__ import annotations

import logging
import sys
import warnings
from typing import Final, TextIO

#: Every module in the package logs through a child of this logger.
PACKAGE_LOGGER_NAME: Final = "github_metrics"

THIRD_PARTY_LOGGERS: Final = ("geopy",)
"""Libraries that log without attaching a handler of their own.

Only these need adopting. A library that attaches a `NullHandler` - which
`requests`, `urllib3`, `charset_normalizer` and PyGithub all do - never reaches
Python's handler of last resort, so its output is already ours to control or
silently discarded. `geopy` attaches none, and `geopy.extra.rate_limiter` logs
every retry with `exc_info=True`.

Checked by walking the logger tree after importing the CLI, rather than
assumed: `asyncio` is in the same position and is not listed because nothing
here uses it, and adopting a logger this package never causes to emit would be
a claim about behaviour that does not happen.
"""

_DEFAULT_LOG_FORMAT: Final = "%(levelname)-8s %(asctime)s %(filename)s:%(lineno)s:%(message)s"
_DEFAULT_DATE_FORMAT: Final = "%Y-%m-%dT%H:%M:%S%z"


class LogLevels:
    """Log level constants."""

    NOTSET: Final = logging.NOTSET
    DEBUG: Final = logging.DEBUG
    INFO: Final = logging.INFO
    WARNING: Final = logging.WARNING
    ERROR: Final = logging.ERROR
    CRITICAL: Final = logging.CRITICAL

    @classmethod
    def from_name(cls, name: str, default: int = INFO) -> int:
        """Resolve a level name to its numeric value.

        Args:
            name: A level name such as `DEBUG`; case and surrounding
                whitespace are ignored.
            default: Returned when `name` is not a known level.

        Returns:
            The numeric logging level.
        """
        level = logging.getLevelName(name.strip().upper())
        return level if isinstance(level, int) else default


def reset_logger(
    min_level: int = LogLevels.INFO,
    *,
    stream: TextIO | None = None,
    fmt: str = _DEFAULT_LOG_FORMAT,
    date_fmt: str = _DEFAULT_DATE_FORMAT,
) -> logging.Logger:
    """Configure the package logger, replacing any handlers already attached.

    Calling this more than once is safe: the previous handlers are removed
    first, so repeated calls never duplicate log lines.

    Args:
        min_level: Lowest level that will be emitted.
        stream: Where records are written. Defaults to `sys.stderr` so log
            output never contaminates the JSON the CLI writes to stdout.
        fmt: Format string passed to `logging.Formatter`.
        date_fmt: `strftime` format used for `%(asctime)s`.

    Returns:
        The configured `github_metrics` logger.
    """
    logger = logging.getLogger(PACKAGE_LOGGER_NAME)

    # Drained rather than iterated. `removeHandler` mutates the very list
    # being walked, so iterating it directly skips every other handler and
    # leaves half of them attached - which is how a second `reset_logger`
    # ends up duplicating every record. The copy this replaces was correct
    # and read as redundant; a drain cannot be mistaken for either.
    while logger.handlers:
        existing = logger.handlers[0]
        logger.removeHandler(existing)
        existing.close()

    handler = logging.StreamHandler(sys.stderr if stream is None else stream)
    handler.setFormatter(logging.Formatter(fmt, datefmt=date_fmt))
    logger.addHandler(handler)
    logger.setLevel(min_level)
    # The package owns its own output; don't re-emit through the root logger.
    logger.propagate = False

    _adopt_third_party(handler, min_level)

    return logger


def _adopt_third_party(handler: logging.Handler, min_level: int) -> None:
    """Bring the libraries that log without a handler under this one.

    A library that logs and attaches no handler of its own reaches Python's
    handler of last resort, which writes to **stderr at WARNING with no
    formatter** - outside this package's format and, worse, outside its level.
    Most libraries avoid that by attaching a `NullHandler`; `requests`,
    `urllib3`, `charset_normalizer` and PyGithub all do. `geopy` does not.

    What that cost: `geopy.extra.rate_limiter` logs each retry with
    `exc_info=True`, so a location the service could not resolve printed
    **two full stack traces**, twenty-two lines, per location - unformatted,
    and emitted identically at `LOG_LEVEL=ERROR`, `INFO` and `DEBUG`, because
    the level that governed them was the last-resort handler's rather than
    ours. Meanwhile the one honest line about the same event, from
    `geo.py`, was correctly suppressed at ERROR. The operator could silence
    the useful message and not the noise.

    Args:
        handler: The package handler, so adopted output shares the format.
        min_level: The level the package was configured with.
    """
    for name in THIRD_PARTY_LOGGERS:
        adopted = logging.getLogger(name)
        while adopted.handlers:
            existing = adopted.handlers[0]
            adopted.removeHandler(existing)
        adopted.addHandler(handler)
        adopted.propagate = False
        # Quiet unless someone is diagnosing. `geo.py` already reports a
        # failed lookup once, naming the location; geopy's version is the same
        # event twice more with a stack trace attached. An error it does not
        # swallow is raised rather than logged, so nothing is lost by holding
        # its own logging at ERROR.
        adopted.setLevel(min_level if min_level <= LogLevels.DEBUG else LogLevels.ERROR)


class Logger:
    """Class providing log method which logs to stdout.

    Deprecated:
        Retained for callers written against the original helper. New code
        should use `logging.getLogger(__name__)` and let `reset_logger`
        configure the destination and level once, at startup.
    """

    def __init__(self, min_level: int = LogLevels.INFO) -> None:
        self._delegate = reset_logger(min_level)

    def log(self, log_level: int, message: object) -> None:
        """Log `message` at `log_level`.

        Args:
            log_level: One of the `LogLevels` constants.
            message: The object to log.
        """
        # Log a deprecation/runtime warning.
        # Clients should be using standard loggers instead of this wrapper.
        warning = (
            "github_metrics.logger.Logger is deprecated; use "
            "logging.getLogger(__name__) and configure it once with "
            "github_metrics.logger.reset_logger()."
        )
        warnings.warn(warning, DeprecationWarning, stacklevel=2)

        # Log the message
        self._delegate.log(log_level, message)
