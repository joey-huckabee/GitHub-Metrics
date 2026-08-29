"""Runtime configuration loaded from the environment (and an optional `.env`)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_API_URL = "https://api.github.com"
DEFAULT_GEOCODER_USER_AGENT = "github-metrics"


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or malformed."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Resolved settings for a single run."""

    github_token: str
    api_url: str = DEFAULT_API_URL
    geocoder_user_agent: str = DEFAULT_GEOCODER_USER_AGENT
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> Settings:
        """Build settings from environment variables.

        Args:
            env_file: Optional path to a `.env` file to load before reading
                the environment. When omitted, the nearest `.env` is used.

        Returns:
            The resolved settings.

        Raises:
            ConfigError: If `GITHUB_TOKEN` is unset or empty.
        """
        if env_file is not None:
            load_dotenv(env_file, override=False)
        else:
            load_dotenv(override=False)

        token = os.getenv("GITHUB_TOKEN", "").strip()
        if not token:
            raise ConfigError("GITHUB_TOKEN is not set. Copy .env.example to .env and add a token.")

        return cls(
            github_token=token,
            api_url=os.getenv("GITHUB_API_URL", DEFAULT_API_URL).strip() or DEFAULT_API_URL,
            geocoder_user_agent=os.getenv("GEOCODER_USER_AGENT", DEFAULT_GEOCODER_USER_AGENT),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )
