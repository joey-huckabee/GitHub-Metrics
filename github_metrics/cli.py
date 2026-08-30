"""Command line interface for github-metrics."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import click
from dotenv import load_dotenv

from github_metrics import __version__
from github_metrics.analysis.closed_issues import (
    CLOSED_ISSUE_BANDS,
    describe_bands,
    score_closed_issues,
)
from github_metrics.analysis.last_update import describe_bands as describe_last_update_bands
from github_metrics.analysis.maturity import describe_bands as describe_maturity_bands
from github_metrics.analysis.popularity import describe_bands as describe_popularity_bands
from github_metrics.analysis.releases import RELEASE_BANDS, SATURATION_COUNT
from github_metrics.analysis.releases import describe_bands as describe_release_bands
from github_metrics.analysis.releases import score_releases
from github_metrics.client import GitHubClient
from github_metrics.collect.closed_issues import ClosedIssueCounts, get_closed_issues
from github_metrics.collect.credentials import verify_credentials
from github_metrics.collect.releases import ReleaseCounts, get_release_counts
from github_metrics.config import Settings
from github_metrics.errors import (
    CollectionError,
    IngestError,
    InvalidCredentialsError,
    MissingCredentialsError,
)
from github_metrics.geo import Geocoder
from github_metrics.ingest import IngestResult, read_repository_csvs
from github_metrics.logger import LogLevels, reset_logger
from github_metrics.metrics import DEFAULT_CONTRIBUTOR_LIMIT, collect_repository_metrics

# Exit statuses, severity-ordered; the highest applicable one wins. Codes 1 and
# 2 belong to click (ClickException and UsageError) and are listed for
# completeness rather than chosen. See docs/adr/0004-exit-code-scheme.md.
LOGGER = logging.getLogger(__name__)

EXIT_ROWS_REJECTED = 3
"""Degraded: the input was read but at least one row was rejected."""

EXIT_REPOSITORY_UNFETCHABLE = 4
"""Degraded: a repository could not be read from the API."""

EXIT_RATE_LIMITED = 5
"""Aborted: the API budget was exhausted, or pre-flight refused the run."""

EXIT_INPUT_UNREADABLE = 6
"""Aborted: the input file could not be read at all."""

EXIT_NO_CREDENTIALS = 7
"""Aborted: no GitHub token was supplied, by flag or by environment."""

EXIT_BAD_CREDENTIALS = 8
"""Aborted: GitHub rejected the token that was supplied."""


class InputError(click.ClickException):
    """A CLI error that exits with `EXIT_INPUT_UNREADABLE`.

    Click's own exit code for a `ClickException` is 1, which a shell cannot
    tell apart from a generic failure. Reading the input is the one thing that
    must be distinguishable, so it gets its own status.
    """

    exit_code = EXIT_INPUT_UNREADABLE


class NoCredentialsError(click.ClickException):
    """A CLI error that exits with `EXIT_NO_CREDENTIALS`.

    Separate from a rejected token because the fix differs: this one means
    "configure a token", not "your token stopped working".
    """

    exit_code = EXIT_NO_CREDENTIALS


class BadCredentialsError(click.ClickException):
    """A CLI error that exits with `EXIT_BAD_CREDENTIALS`."""

    exit_code = EXIT_BAD_CREDENTIALS


class RepositoryError(click.ClickException):
    """A CLI error that exits with `EXIT_REPOSITORY_UNFETCHABLE`.

    A repository that is deleted, renamed or private is an expected outcome of
    a syntactically valid reference, not a failure of the run. It gets a status
    below the aborting ones so a caller can tell "some repositories are stale"
    from "nothing usable came out".
    """

    exit_code = EXIT_REPOSITORY_UNFETCHABLE


@dataclass
class CliContext:
    """Per-invocation state shared by every subcommand.

    Settings are resolved lazily rather than in the group callback. Only the
    commands that reach the GitHub API need a token, and requiring one up front
    would make `github-metrics ingest` - which never touches the network -
    fail on a machine that has no credentials configured at all.
    """

    env_file: Path | None = None
    token: str | None = None
    verify: bool = True
    _settings: Settings | None = None

    def settings(self) -> Settings:
        """Resolve, verify and cache the settings.

        The token is checked against GitHub the first time it is needed. That
        check is free - the rate-limit endpoint does not count against the
        budget - and it converts a mid-run 401 into one message before any work
        starts.

        Returns:
            The resolved settings.

        Raises:
            NoCredentialsError: No token was supplied by any route.
            BadCredentialsError: GitHub rejected the token.
        """
        if self._settings is not None:
            return self._settings

        try:
            settings = Settings.from_env(self.env_file, token=self.token)
        except MissingCredentialsError as exc:
            raise NoCredentialsError(str(exc)) from exc

        if self.verify:
            try:
                verify_credentials(settings)
            except InvalidCredentialsError as exc:
                raise BadCredentialsError(str(exc)) from exc

        self._settings = settings
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
@click.option(
    "--token",
    envvar="GITHUB_TOKEN",
    default=None,
    help=(
        "GitHub token, overriding GITHUB_TOKEN. Note that a token passed as an "
        "argument is visible to other processes and lands in shell history; "
        "prefer the environment or a .env file where either will do."
    ),
)
@click.option(
    "--token-file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Read the token from this file. Safer than --token, which is visible "
        "to other processes and lands in shell history."
    ),
)
@click.option(
    "--no-verify-token",
    is_flag=True,
    help="Skip the credential check. It is free and catches a bad token early.",
)
@click.pass_context
def main(
    ctx: click.Context,
    env_file: Path | None,
    token: str | None,
    token_file: Path | None,
    no_verify_token: bool,
) -> None:
    """Calculate GitHub metrics for FOSS analysis."""
    # Logging is configured from LOG_LEVEL alone, which is readable without a
    # token. Anything needing credentials goes through CliContext.settings().
    if env_file is not None:
        load_dotenv(env_file, override=False)
    else:
        load_dotenv(override=False)

    reset_logger(LogLevels.from_name(os.getenv("LOG_LEVEL", "INFO")))
    ctx.obj = CliContext(
        env_file=env_file,
        token=_resolve_token(token, token_file),
        verify=not no_verify_token,
    )


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


def _closed_issue_lines(
    owner: str,
    repoid: str,
    counts: ClosedIssueCounts,
    weight: float,
    *,
    explain: bool,
) -> list[str]:
    """Render a closed-issue result as aligned label-and-value lines."""
    tracker = "enabled" if counts.issues_enabled else "DISABLED"
    lines = [
        f"{owner}/{repoid}",
        f"  closed issues  {counts.closed:>8}",
        f"  open issues    {counts.open:>8}",
        f"  tracker        {tracker:>8}",
        f"  weight         {weight:>8}",
    ]
    if not counts.issues_enabled:
        lines.append(
            "  note: the issue tracker is off, so zero is a configuration fact "
            "rather than a maintenance one"
        )
    if explain:
        lines.append("")
        lines.append(describe_bands())
    return lines


@main.command("closed-issues")
@click.argument("full_name")
@click.option(
    "--explain",
    is_flag=True,
    help="Print the scoring bands alongside the result.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
    help="Report format.",
)
@click.pass_obj
def closed_issues_command(
    context: CliContext,
    full_name: str,
    explain: bool,
    output_format: str,
) -> None:
    """Report closed-issue counts and score for one OWNER/REPOID repository.

    A probe for one metric at a time. Each metric gets one of these as it is
    defined, so a definition can be checked against real repositories before it
    is wired into the full collection run.

    Counts exclude pull requests, and cost one GraphQL point.

    Exit status is 0 on success and 4 when the repository cannot be read.
    """
    owner, repoid = _split_slug(full_name)

    try:
        with GitHubClient(context.settings()) as client:
            counts = get_closed_issues(client, owner, repoid)
    except CollectionError as exc:
        raise RepositoryError(str(exc)) from exc

    weight = score_closed_issues(counts.closed)

    if output_format == "json":
        click.echo(
            json.dumps(
                {
                    "owner": owner,
                    "repoid": repoid,
                    "closed_issues": counts.closed,
                    "open_issues": counts.open,
                    "issues_enabled": counts.issues_enabled,
                    "weight": weight,
                    "bands": (
                        [
                            {"below": bound, "weight": band_weight}
                            for bound, band_weight in CLOSED_ISSUE_BANDS
                        ]
                        if explain
                        else None
                    ),
                },
                indent=2,
            )
        )
        return

    click.echo("\n".join(_closed_issue_lines(owner, repoid, counts, weight, explain=explain)))


def _split_slug(full_name: str) -> tuple[str, str]:
    """Split an `OWNER/REPOID` argument.

    Args:
        full_name: The argument as typed.

    Returns:
        The owner and repository name.

    Raises:
        click.BadParameter: If the value is not two parts separated by a slash.
    """
    owner, separator, repoid = full_name.partition("/")
    if not separator or not owner or not repoid:
        raise click.BadParameter(
            f"expected OWNER/REPOID, got {full_name!r}", param_hint="FULL_NAME"
        )
    return owner, repoid


def _releases_lines(
    owner: str, repoid: str, counts: ReleaseCounts, weight: float, *, explain: bool
) -> list[str]:
    """Render release counts as aligned label-and-value lines."""
    lines = [
        f"{owner}/{repoid}",
        f"  releases            {counts.releases:>8}",
        f"  tags                {counts.tags:>8}",
        f"  distinct versions   {counts.distinct_versions:>8}",
        f"  weight              {weight:>8}",
    ]
    if counts.releases == 0 and counts.tags > 0:
        lines.append(
            "  note: this project tags versions but publishes no GitHub Releases, "
            "so counting releases alone would score it zero"
        )
    elif counts.releases > 0:
        lines.append(f"  tags with no release{counts.tags_without_releases:>8}")
    if counts.legacy_sum != counts.distinct_versions:
        inflation = counts.legacy_sum / counts.distinct_versions
        lines.append(
            f"  note: releases + tags would report {counts.legacy_sum} "
            f"({inflation:.2f}x), counting every release twice"
        )
    if counts.distinct_versions >= SATURATION_COUNT:
        lines.append(
            f"  note: at or above {SATURATION_COUNT} versions the weight is capped at 1.0, "
            "so this project is indistinguishable from any other above that line"
        )
    if explain:
        lines.append("")
        lines.append(describe_release_bands())
    return lines


@main.command("releases")
@click.argument("full_name")
@click.option(
    "--explain",
    is_flag=True,
    help="Print the scoring bands alongside the result.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
    help="Report format.",
)
@click.pass_obj
def releases_command(
    context: CliContext, full_name: str, explain: bool, output_format: str
) -> None:
    """Report release and tag counts for one OWNER/REPOID repository.

    The scored value is the distinct version count, which is the tag count -
    not releases plus tags, which counts every release twice.

    Costs one GraphQL point. Exit status is 0 on success and 4 when the
    repository cannot be read.
    """
    owner, repoid = _split_slug(full_name)

    try:
        with GitHubClient(context.settings()) as client:
            counts = get_release_counts(client, owner, repoid)
    except CollectionError as exc:
        raise RepositoryError(str(exc)) from exc

    weight = score_releases(counts.distinct_versions)

    if output_format == "json":
        click.echo(
            json.dumps(
                {
                    "owner": owner,
                    "repoid": repoid,
                    "releases": counts.releases,
                    "tags": counts.tags,
                    "distinct_versions": counts.distinct_versions,
                    "legacy_sum": counts.legacy_sum,
                    "weight": weight,
                    "bands": (
                        [
                            {"below": bound, "weight": band_weight}
                            for bound, band_weight in RELEASE_BANDS
                        ]
                        if explain
                        else None
                    ),
                },
                indent=2,
            )
        )
        return

    click.echo("\n".join(_releases_lines(owner, repoid, counts, weight, explain=explain)))


def _resolve_token(token: str | None, token_file: Path | None) -> str | None:
    """Choose between the two ways of supplying a token on the command line.

    Args:
        token: The value of `--token`, if given.
        token_file: The path from `--token-file`, if given.

    Returns:
        The token, or `None` to fall back to the environment.

    Raises:
        click.UsageError: If both were supplied, which can only be a mistake,
            or if the file cannot be read.
    """
    if token and token_file:
        raise click.UsageError("pass --token or --token-file, not both")

    if token_file is None:
        return token

    try:
        contents = token_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise click.UsageError(f"could not read --token-file {token_file}: {exc}") from exc

    # A file written by `echo` ends in a newline, and a token with a trailing
    # newline is rejected by the API for reasons that are hard to see.
    stripped = contents.strip()
    if not stripped:
        raise click.UsageError(f"--token-file {token_file} is empty")

    LOGGER.debug("Token read from %s (%d characters)", token_file, len(stripped))
    return stripped


BAND_TABLES: dict[str, Callable[[], str]] = {
    "closed-issues": describe_bands,
    "releases": describe_release_bands,
    "last-update": describe_last_update_bands,
    "maturity": describe_maturity_bands,
    "popularity": describe_popularity_bands,
}
"""Every scoring table, by the name of the metric it scores."""


@main.command("bands")
@click.argument("metric", required=False, type=click.Choice(sorted(BAND_TABLES)))
def bands_command(metric: str | None) -> None:
    """Print the scoring bands for METRIC, or for every metric.

    The tables are the scoring model. Printing them is how a surprising score
    gets explained without reading source, and how the model gets reviewed
    without trusting a document to have kept up with the code.

    Needs no network and no token.
    """
    chosen = [metric] if metric else sorted(BAND_TABLES)
    click.echo("\n\n".join(BAND_TABLES[name]() for name in chosen))
