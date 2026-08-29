"""Command line interface for github-metrics."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import click

from github_metrics import __version__
from github_metrics.client import GitHubClient
from github_metrics.config import ConfigError, Settings
from github_metrics.geo import Geocoder
from github_metrics.logger import LogLevels, reset_logger
from github_metrics.metrics import DEFAULT_CONTRIBUTOR_LIMIT, collect_repository_metrics

LOGGER = logging.getLogger(__name__)


@click.group(
    context_settings={"help_option_names": ["-h", "--help"]},
    help=f"Calculate GitHub metrics for FOSS analysis. (v{__version__})",
)
@click.version_option(__version__, "-V", "--version", prog_name="github-metrics")
@click.option(
    "--env-file",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Path to a .env file. Defaults to the nearest .env.",
)
@click.pass_context
def main(ctx: click.Context, env_file: Path | None) -> None:
    """Calculate GitHub metrics for FOSS analysis."""
    try:
        settings = Settings.from_env(env_file)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    reset_logger(LogLevels.from_name(settings.log_level))
    ctx.obj = settings


@main.command("repo")
@click.argument("full_name")
@click.option("--geocode", is_flag=True, help="Resolve contributor locations to coordinates.")
@click.option(
    "--contributors",
    "contributor_limit",
    default=DEFAULT_CONTRIBUTOR_LIMIT,
    show_default=True,
    help="Maximum number of contributors to inspect.",
)
@click.option(
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Write JSON here instead of stdout.",
)
@click.pass_obj
def repo_command(
    settings: Settings,
    full_name: str,
    geocode: bool,
    contributor_limit: int,
    output: Path | None,
) -> None:
    """Collect metrics for a single OWNER/NAME repository."""
    geocoder = Geocoder(settings.geocoder_user_agent) if geocode else None

    with GitHubClient(settings) as client:
        metrics = collect_repository_metrics(
            client,
            full_name,
            geocoder=geocoder,
            contributor_limit=contributor_limit,
        )

    payload = json.dumps(metrics.to_dict(), indent=2, default=str)
    if output is not None:
        output.write_text(payload + "\n", encoding="utf-8")
        click.echo(f"Wrote {output}")
    else:
        click.echo(payload)


@main.command("rate-limit")
@click.pass_obj
def rate_limit_command(settings: Settings) -> None:
    """Show how many core API requests remain for the current token."""
    with GitHubClient(settings) as client:
        click.echo(f"{client.rate_limit_remaining()} core requests remaining")
