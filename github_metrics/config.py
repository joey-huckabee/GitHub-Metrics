"""Runtime configuration loaded from the environment (and an optional `.env`)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from github_metrics.errors import MissingCredentialsError

LOGGER = logging.getLogger(__name__)

DEFAULT_API_URL = "https://api.github.com"
DEFAULT_GEOCODER_USER_AGENT = "github-metrics"


@dataclass(frozen=True, slots=True)
class Settings:
    """Resolved settings for a single run."""

    github_token: str
    api_url: str = DEFAULT_API_URL
    geocoder_user_agent: str = DEFAULT_GEOCODER_USER_AGENT
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env_file: Path | None = None, token: str | None = None) -> Settings:
        """Build settings from environment variables.

        Args:
            env_file: Optional path to a `.env` file to load before reading
                the environment. When omitted, the nearest `.env` is used.
            token: A token supplied directly, which takes precedence over the
                environment. An explicit argument beats ambient configuration,
                so a caller can override a stale `.env` without editing it.

        Returns:
            The resolved settings.

        Raises:
            MissingCredentialsError: If no token is supplied by either route.
        """
        if env_file is not None:
            load_dotenv(env_file, override=False)
        else:
            load_dotenv(override=False)

        supplied = (token or "").strip()
        resolved = supplied or os.getenv("GITHUB_TOKEN", "").strip()

        if not resolved:
            raise MissingCredentialsError(
                "no GitHub token available. Pass --token, set GITHUB_TOKEN in the "
                "environment, or copy .env.example to .env and add one."
            )

        # Which route supplied it, never the value itself.
        LOGGER.debug("GitHub token resolved from %s", "--token" if supplied else "GITHUB_TOKEN")

        return cls(
            github_token=resolved,
            api_url=os.getenv("GITHUB_API_URL", DEFAULT_API_URL).strip() or DEFAULT_API_URL,
            geocoder_user_agent=os.getenv("GEOCODER_USER_AGENT", DEFAULT_GEOCODER_USER_AGENT),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )
