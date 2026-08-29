"""Command line interface for github-metrics."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import click
from dotenv import load_dotenv

from github_metrics import __version__
from github_metrics.client import GitHubClient
from github_metrics.config import ConfigError, Settings
from github_metrics.errors import IngestError
from github_metrics.geo import Geocoder
from github_metrics.ingest import IngestResult, read_repository_csvs
from github_metrics.logger import LogLevels, reset_logger
from github_metrics.metrics import DEFAULT_CONTRIBUTOR_LIMIT, collect_repository_metrics

EXIT_INPUT_ERROR = 2
"""Exit status for a malformed or unreadable input file."""

EXIT_ROWS_REJECTED = 3
"""Exit status when ingestion succeeded but rejected at least one row."""


class InputError(click.ClickException):
    """A CLI error that exits with `EXIT_INPUT_ERROR` rather than click's 1.

    Click's own exit code for a `ClickException` is 1, which a shell cannot
    tell apart from a generic failure. Ingestion promises a distinct status for
    "the input could not be read", so it needs its own exception type.
    """

    exit_code = EXIT_INPUT_ERROR


@dataclass
class CliContext:
    """Per-invocation state shared by every subcommand.

    Settings are resolved lazily rather than in the group callback. Only the
    commands that reach the GitHub API need a token, and requiring one up front
    would make `github-metrics ingest` - which never touches the network -
    fail on a machine that has no credentials configured at all.
    """

    env_file: Path | None = None
    _settings: Settings | None = None

    def settings(self) -> Settings:
        """Resolve and cache the settings, or fail with a CLI-shaped error.

        Returns:
            The resolved settings.

        Raises:
            click.ClickException: If required configuration is missing.
        """
        if self._settings is None:
            try:
                self._settings = Settings.from_env(self.env_file)
            except ConfigError as exc:
                raise click.ClickException(str(exc)) from exc
        return self._settings


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
    # Logging is configured from LOG_LEVEL alone, which is readable without a
    # token. Anything needing credentials goes through CliContext.settings().
    if env_file is not None:
        load_dotenv(env_file, override=False)
    else:
        load_dotenv(override=False)

    reset_logger(LogLevels.from_name(os.getenv("LOG_LEVEL", "INFO")))
    ctx.obj = CliContext(env_file=env_file)


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
    context: CliContext,
    full_name: str,
    geocode: bool,
    contributor_limit: int,
    output: Path | None,
) -> None:
    """Collect metrics for a single OWNER/NAME repository."""
    settings = context.settings()
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
def rate_limit_command(context: CliContext) -> None:
    """Show how many core API requests remain for the current token."""
    with GitHubClient(context.settings()) as client:
        click.echo(f"{client.rate_limit_remaining()} core requests remaining")


def _render_text(results: list[IngestResult]) -> str:
    """Render ingest results as a short human-readable report."""
    lines: list[str] = []
    for result in results:
        lines.append(f"{result.source}: {result.accepted} repositories, {result.rejected} rejected")
        lines.extend(f"  {reference.full_name}" for reference in result.repositories)
        lines.extend(f"  ! {issue}" for issue in result.issues)
    total = sum(result.accepted for result in results)
    if len(results) > 1:
        lines.append(f"total: {total} repositories from {len(results)} files")
    return "\n".join(lines)


def _render_json(results: list[IngestResult]) -> str:
    """Render ingest results as JSON."""
    return json.dumps([result.to_dict() for result in results], indent=2, default=str)


@main.command("ingest")
@click.argument(
    "sources",
    nargs=-1,
    required=True,
    type=click.Path(dir_okay=False, path_type=Path),
)
@click.option(
    "--strict",
    is_flag=True,
    help="Abort on the first bad row instead of reporting all of them.",
)
@click.option(
    "--workers",
    type=click.IntRange(min=1),
    default=None,
    help="Threads used to read multiple files. Defaults to min(files, 8).",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
    help="Report format.",
)
@click.option(
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Write the report here instead of stdout.",
)
@click.pass_context
def ingest_command(
    ctx: click.Context,
    sources: tuple[Path, ...],
    strict: bool,
    workers: int | None,
    output_format: str,
    output: Path | None,
) -> None:
    """Read one or more OWNER,REPOID CSV files and report what they contain.

    This command validates and reports only. It performs no network access and
    collects no metrics, so it needs no GITHUB_TOKEN.

    Exit status is 0 when every row was accepted, 3 when the files were read
    but some rows were rejected, and 2 when a file could not be read at all.
    """
    try:
        results = read_repository_csvs(sources, strict=strict, max_workers=workers)
    except IngestError as exc:
        raise InputError(str(exc)) from exc

    report = _render_json(results) if output_format == "json" else _render_text(results)

    if output is not None:
        output.write_text(report + "\n", encoding="utf-8")
        click.echo(f"Wrote {output}")
    else:
        click.echo(report)

    if any(result.issues for result in results):
        # A distinct status lets a pipeline tell "nothing loaded" from "loaded,
        # but the inventory needs fixing" without parsing the report.
        ctx.exit(EXIT_ROWS_REJECTED)
