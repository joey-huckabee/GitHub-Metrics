"""Logging for GitHub Metrics."""

from __future__ import annotations

import logging
import sys
import warnings
from typing import Final, TextIO

#: Every module in the package logs through a child of this logger.
PACKAGE_LOGGER_NAME: Final = "github_metrics"

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

    return logger


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
