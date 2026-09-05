"""Command line interface for github-metrics."""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import click
from dotenv import load_dotenv

from github_metrics import __version__
from github_metrics.analysis.closed_issues import describe_bands
from github_metrics.analysis.last_update import describe_bands as describe_last_update_bands
from github_metrics.analysis.maturity import describe_bands as describe_maturity_bands
from github_metrics.analysis.popularity import describe_bands as describe_popularity_bands
from github_metrics.analysis.releases import describe_bands as describe_release_bands
from github_metrics.analysis.row import build_block, build_empty_row, build_row
from github_metrics.analysis.statistics import build_repository_statistics
from github_metrics.client import GitHubClient
from github_metrics.collect.budget import check_budget
from github_metrics.collect.credentials import verify_credentials
from github_metrics.collect.runner import Outcome, collect_all
from github_metrics.config import Settings
from github_metrics.errors import (
    DocumentDirectoryError,
    IngestError,
    InvalidCredentialsError,
    MissingCredentialsError,
    OutputDestinationError,
)
from github_metrics.geo import Geocoder
from github_metrics.geocache import GeocodeCache
from github_metrics.logger import LogLevels, reset_logger
from github_metrics.model.scan import ScanIdentifier
from github_metrics.model.software import SoftwareRow
from github_metrics.model.statistics import (
    BudgetStatistics,
    Exclusion,
    ExclusionReason,
    GeocodingStatistics,
    IdentityGaps,
    ScanStatistics,
)
from github_metrics.output import (
    render_console,
    resolve_destination,
    resolve_fields,
    write_csv,
    write_json,
)
from github_metrics.output.documents import (
    prepare_root,
    write_document,
)
from github_metrics.output.fields import split_selection
from github_metrics.output.statistics import write_statistics
from github_metrics.sources import RepositoryRef, ResolvedSources, resolve_sources

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


@dataclass(frozen=True, slots=True)
class CollectionRun:
    """Everything one collection produced, including what it cost.

    `_collect` used to return outcomes alone. `statistics.json` reports on the
    run as well as on the repositories - what was spent, whether the budget
    held, what the geocoder did - and none of that is recoverable from the
    outcomes afterwards, so it is carried out of the collection rather than
    re-derived.

    Attributes:
        outcomes: What each reference produced, in input order.
        budget: What the run spent and whether it finished.
        geocoding: What the geocoder and its cache did.
    """

    outcomes: list[Outcome]
    budget: BudgetStatistics = field(default_factory=BudgetStatistics)
    geocoding: GeocodingStatistics = field(default_factory=GeocodingStatistics)


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


@main.command("scan")
@click.argument("sources", nargs=-1, required=True)
@click.option(
    "--output",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help=("Directory for both artifacts. Defaults to ./githubmetrics."),
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["csv", "json", "console"]),
    default="csv",
    show_default=True,
    help="Format for the tabular artifact. The documents are always JSON.",
)
@click.option(
    "--fields",
    default=None,
    help="Comma-separated columns to emit. Defaults to all of them.",
)
@click.option(
    "--workers",
    type=click.IntRange(min=1),
    default=None,
    help="Concurrent collections. Defaults to min(repositories, 8).",
)
@click.option(
    "--recover-anonymous/--no-recover-anonymous",
    default=True,
    show_default=True,
    help=(
        "Recover contributors GitHub left anonymous but whose no-reply email "
        "names their account. Costs a page per hundred identities."
    ),
)
@click.option(
    "--strict",
    is_flag=True,
    help="Abort on the first bad input row instead of reporting all of them.",
)
@click.pass_context
# A command's parameters are its command-line surface, not a signature anyone
# calls: Click passes every one of them by keyword. The same reasoning as
# `SoftwareRow`'s attribute count - the shape is the contract, and collapsing
# flags into an options object to satisfy a counter would hide the interface
# from the place it is declared.
def scan_command(  # noqa: PLR0913, PLR0917
    ctx: click.Context,
    sources: tuple[str, ...],
    output: Path | None,
    output_format: str,
    fields: str | None,
    workers: int | None,
    recover_anonymous: bool,
    strict: bool,
) -> None:
    """Scan every repository SOURCES names and write its metrics.

    Each SOURCE is a slug (pypa/virtualenv), a GitHub URL, or a CSV inventory,
    and the three can be mixed. Check a list first with `validate`, which needs
    no token.

    One run produces two artifacts under one scan identity: githubmetrics.csv,
    one row per accepted reference in input order, and one JSON document per
    repository at <owner>/<repoid>.json. The document is the row plus its
    contributors, so the two join on identical keys.

    A repository that cannot be read still produces a row, carrying its
    identity and no measurements. It produces no document: an absent file
    says 'named, not measured', while a document with an empty contributor
    array would read as a repository that has none.

    Every contributor GitHub attributes to an account is collected, so a
    repository's cost rises with its contributor count. The pre-flight confirms
    the token can afford the run's *minimum* - two GraphQL points and one REST
    request per repository - which refuses an obviously impossible run but no
    longer promises that a run which starts will finish.
    """
    context: CliContext = ctx.obj
    root = _document_root(output)
    destination = _destination(root, output_format=output_format)
    columns = resolve_fields(split_selection(fields) if fields else None)

    try:
        resolved = resolve_sources(sources, strict=strict)
    except IngestError as exc:
        raise InputError(str(exc)) from exc

    scan = ScanIdentifier()
    LOGGER.info("Scan %s started at %s", scan.scan_id, scan.scan_date)
    started = time.monotonic()

    run = _collect(
        context,
        resolved.repositories,
        workers=workers,
        recover_anonymous=recover_anonymous,
    )
    outcomes = run.outcomes
    rows = [
        (
            build_row(outcome.reference, outcome.metadata, scan)
            if outcome.metadata is not None
            else build_empty_row(outcome.reference, scan)
        )
        for outcome in outcomes
    ]

    _emit(rows, destination, output_format=output_format, columns=columns)
    _write_documents(root, rows, outcomes, scan)
    _write_statistics(
        root,
        run,
        rows,
        scan,
        named=len(resolved.repositories),
        duration=time.monotonic() - started,
    )
    _report_failures(outcomes)

    # Severity-ordered, highest applicable wins: an unreadable repository is
    # worse news than a rejected input row, and both still produced a file.
    if any(not outcome.ok for outcome in outcomes):
        ctx.exit(EXIT_REPOSITORY_UNFETCHABLE)
    if resolved.issues:
        ctx.exit(EXIT_ROWS_REJECTED)


def _document_root(output: Path | None) -> Path:
    """Resolve and create the directory both artifacts go in.

    Done before collection for the same reason the budget check is: an
    unwritable destination discovered afterwards has already cost an hour of
    quota, and quota does not refill on request.

    Args:
        output: What ``--output`` named, or `None` for ``./githubmetrics``.

    Returns:
        The prepared directory.

    Raises:
        InputError: The directory cannot be created or is not a directory.
    """
    try:
        return prepare_root(output)
    except DocumentDirectoryError as exc:
        raise InputError(str(exc)) from exc


def _destination(root: Path, *, output_format: str) -> Path | None:
    """Resolve the tabular artifact's path, or `None` for the console."""
    if output_format == "console":
        return None
    try:
        return resolve_destination(root, json_format=output_format == "json")
    except OutputDestinationError as exc:
        raise InputError(str(exc)) from exc


def _collect(
    context: CliContext,
    references: Sequence[RepositoryRef],
    *,
    workers: int | None,
    recover_anonymous: bool = True,
) -> CollectionRun:
    """Check the budget, then collect. Nothing named means nothing to spend."""
    if not references:
        # Every source was refused. There is no quota to spend, and a
        # header-only file is still the honest answer.
        LOGGER.warning("No repositories to collect")
        return CollectionRun(outcomes=[])

    settings = context.settings()
    # One geocoder for the run, shared across the workers: its caches and its
    # one-request-per-second pace are properties of the run, and a geocoder
    # per repository would honour neither.
    #
    # The cache outlives the run, which is what makes unbounded contributor
    # collection affordable to repeat - geocoding is the slowest thing a scan
    # does, and a re-run should pay only for places never seen before. The CLI
    # builds it because `geo` may not parse a file format and `geocache` may
    # not open a socket; this is where the two meet.
    cache = GeocodeCache.load(settings.geocode_cache_path)
    geocoder = Geocoder(settings.geocoder_user_agent, cache=cache)

    try:
        with GitHubClient(settings) as client:
            budget = check_budget(client, len(references))
            LOGGER.info(
                "Budget: at least %d of %d GraphQL points and at least %d of %d "
                "REST requests for %d repositories",
                budget.required,
                budget.available,
                budget.requests_required,
                budget.requests_available,
                budget.repositories,
            )
            outcomes = collect_all(
                client,
                references,
                max_workers=workers,
                geocoder=geocoder,
                recover_anonymous=recover_anonymous,
            )
            return CollectionRun(
                outcomes=outcomes,
                # Spend is measured by difference against the API's own
                # counters rather than estimated from the cost model, so it
                # includes anything the model does not know about.
                budget=_spend(client, budget.available),
                geocoding=_geocoding(geocoder),
            )
    finally:
        # In a finally block because a run that failed still resolved
        # locations, and throwing that away would make the next attempt pay
        # for them again. Saving cannot raise; see `GeocodeCache.save`.
        cache.save()


def _spend(client: GitHubClient, graphql_before: int) -> BudgetStatistics:
    """Measure what the run cost, in the one currency that can be measured.

    GraphQL is measured by difference against its own `rateLimit` field,
    which is authoritative and free to read. That is also the binding
    budget - two points against one request per repository - so it is the
    figure that decides whether a run fits.

    The REST figures are left unmeasured on purpose; `BudgetStatistics`
    documents why, and it is not a gap that better plumbing here would
    close.

    An hourly reset during the run makes the difference negative. That is
    clamped to zero rather than published as a nonsense number, which also
    makes this a lower bound on any run long enough to cross a reset.
    """
    graphql_after = client.graphql_points_remaining()
    return BudgetStatistics(
        graphql_points_spent=max(0, graphql_before - graphql_after),
        graphql_remaining=graphql_after,
    )


def _geocoding(geocoder: Geocoder) -> GeocodingStatistics:
    """Read the geocoder's counters into the shape the artifact publishes."""
    return GeocodingStatistics(
        cache_loaded=geocoder.cache.loaded,
        cache_expired_on_load=geocoder.cache.expired_on_load,
        cache_hits=geocoder.cache_hits,
        lookups=geocoder.lookups,
        matched=geocoder.matched,
        unmatched=geocoder.unmatched,
        service_failures=geocoder.service_failures,
    )


def _write_statistics(
    root: Path,
    run: CollectionRun,
    rows: Sequence[SoftwareRow],
    scan: ScanIdentifier,
    *,
    named: int,
    duration: float,
) -> None:
    """Write the third artifact: what the other two are worth.

    Written even when nothing was collected. A statistics file reporting zero
    repositories is an answer; its absence is indistinguishable from the tool
    never having run.
    """
    per_repository = tuple(
        build_repository_statistics(
            row,
            outcome.contributors,
            collected=outcome.ok,
            documented=outcome.documented,
            # Free: the metrics query already carries it, measured at one
            # point whether the repository has 1,250 commits or 32,016.
            commits_total=outcome.metadata.commits if outcome.metadata else None,
            gaps=_gaps(outcome),
        )
        for row, outcome in zip(rows, run.outcomes, strict=True)
    )

    statistics = ScanStatistics(
        scan_id=scan.scan_id,
        scan_date=scan.scan_date,
        tool_version=__version__,
        duration_seconds=duration,
        repositories_named=named,
        budget=run.budget,
        geocoding=run.geocoding,
        repositories=per_repository,
        warnings=tuple(_warnings(run.outcomes)),
    )

    try:
        path = write_statistics(root, statistics)
    except DocumentDirectoryError as exc:
        # Reported, not fatal: the measurements are already on disk, and a run
        # that wrote them should not be failed for losing their bounds. The
        # warning is loud because the bounds are the point.
        click.echo(f"! statistics could not be written: {exc}", err=True)
        return
    click.echo(f"Wrote statistics to {path}")


def _gaps(outcome: Outcome) -> IdentityGaps:
    """Account for the identities that did not reach the document.

    Everything past GitHub's 500-author-email ceiling is anonymous - a name and
    an email, no account, no location.

    How much can be said about that tail depends on what was asked for. The
    census counts it in one request but cannot see its commits, so those stay
    `None`: a zero would claim the tail contributed nothing. Recovery walks the
    pages, so it reports both - and how many of those entries named an account
    through a no-reply address.
    """
    tally = outcome.anonymous
    identities = outcome.identities
    if identities is None and tally is not None:
        # Walking the tail counts it exactly, so a failed census is not fatal
        # to the denominator when recovery ran.
        identities = len(outcome.contributors) + tally.unrecoverable_people
    if identities is None:
        return IdentityGaps()

    if tally is None:
        missing = max(0, identities - len(outcome.contributors))
        return IdentityGaps(
            identities=identities,
            unrecoverable=(
                Exclusion(ExclusionReason.ANONYMOUS_NO_ACCOUNT, people=missing) if missing else None
            ),
        )

    # The pages were walked, so the tail's commits are measured rather than
    # unknown - the one thing the cheap census cannot report.
    return IdentityGaps(
        identities=identities,
        unrecoverable=(
            Exclusion(
                ExclusionReason.ANONYMOUS_NO_ACCOUNT,
                people=tally.unrecoverable_people,
                commits=tally.unrecoverable_commits,
            )
            if tally.unrecoverable_people
            else None
        ),
        recovered=tally.recovered_identities,
    )


def _warnings(outcomes: Sequence[Outcome]) -> list[str]:
    """Every degradation, in run order, machine-readable.

    The same facts the log carries, in the artifact, so a consumer reading only
    the files can see why a number is smaller than it looks.
    """
    found: list[str] = []
    for outcome in outcomes:
        if outcome.error is not None:
            found.append(f"{outcome.reference.full_name}: not collected: {outcome.error}")
        elif outcome.contributor_error is not None:
            found.append(
                f"{outcome.reference.full_name}: measured, but contributors could not "
                f"be read, so no document was written: {outcome.contributor_error}"
            )
    return found


def _emit(
    rows: Sequence[SoftwareRow],
    destination: Path | None,
    *,
    output_format: str,
    columns: Sequence[str],
) -> None:
    """Write the rows where the caller asked for them."""
    if destination is None:
        click.echo(render_console(rows, columns=columns))
        return

    # newline="" because the csv module writes its own line terminators.
    with destination.open("w", encoding="utf-8", newline="") as handle:
        if output_format == "json":
            write_json(rows, handle, columns=columns)
        else:
            write_csv(rows, handle, columns=columns)
    click.echo(f"Wrote {len(rows)} rows to {destination}")


def _write_documents(
    root: Path,
    rows: Sequence[SoftwareRow],
    outcomes: Sequence[Outcome],
    scan: ScanIdentifier,
) -> None:
    """Write one JSON document per repository that was fully collected.

    Rows and outcomes are positionally aligned - both come from the same
    input order - so a document is written for outcome *i* using row *i*.

    Args:
        root: The prepared output directory.
        rows: The finished rows, in input order.
        outcomes: What each reference produced, in the same order.
        scan: Identity of this run, stamped onto every contributor.
    """
    written = 0
    # strict: the two come from the same input order and must stay aligned,
    # so a length mismatch is a defect rather than something to truncate past.
    for row, outcome in zip(rows, outcomes, strict=True):
        if not outcome.documented:
            continue
        try:
            write_document(root, row, build_block(outcome.contributors, scan))
        except DocumentDirectoryError as exc:
            # One unwritable file does not abandon the rest; the CSV is
            # already written and the other documents are still worth having.
            click.echo(f"! {outcome.reference.full_name}: {exc}", err=True)
            continue
        written += 1

    click.echo(f"Wrote {written} documents to {root}")


def _report_failures(outcomes: Sequence[Outcome]) -> None:
    """Name the repositories that produced no measurements.

    On stderr, and by name. A file with empty rows says something went wrong;
    only this says which repositories, and why.
    """
    for outcome in outcomes:
        if outcome.error is not None:
            click.echo(f"! {outcome.reference.full_name}: {outcome.error}", err=True)


@main.command("rate-limit")
@click.pass_obj
def rate_limit_command(context: CliContext) -> None:
    """Show how many core API requests remain for the current token."""
    with GitHubClient(context.settings()) as client:
        click.echo(f"{client.rate_limit_remaining()} core requests remaining")


def _render_text(resolved: ResolvedSources) -> str:
    """Render a resolution as a short human-readable report."""
    lines = [f"{reference.full_name}" for reference in resolved.repositories]
    lines.extend(f"! {issue}" for issue in resolved.issues)
    lines.append(
        f"{resolved.accepted} repositories, {resolved.rejected} rejected, "
        f"from {len(resolved.files)} file(s)"
    )
    return "\n".join(lines)


def _render_json(resolved: ResolvedSources) -> str:
    """Render a resolution as JSON."""
    payload = {
        "repositories": [reference.to_dict() for reference in resolved.repositories],
        "issues": [
            {
                "code": issue.code,
                "message": issue.message,
                "line": issue.line,
                "source": issue.source,
            }
            for issue in resolved.issues
        ],
        "accepted": resolved.accepted,
        "rejected": resolved.rejected,
        "files": [str(path) for path in resolved.files],
    }
    return json.dumps(payload, indent=2, default=str)


@main.command("validate")
@click.argument("sources", nargs=-1, required=True)
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
def validate_command(
    ctx: click.Context,
    sources: tuple[str, ...],
    strict: bool,
    workers: int | None,
    output_format: str,
    output: Path | None,
) -> None:
    """Check what SOURCES name, without collecting anything.

    Each SOURCE is a slug (pypa/virtualenv), a GitHub URL, or a CSV inventory,
    and the three can be mixed in one command.

    This reports only. It performs no network access and collects no metrics,
    so it needs no GITHUB_TOKEN - which is the point: a list can be checked
    before any rate limit is spent on it, and by someone who has no token.

    Exit status is 0 when every reference was accepted, 3 when the sources were
    read but some references were rejected, and 2 when a file could not be read
    at all.
    """
    try:
        resolved = resolve_sources(sources, strict=strict, max_workers=workers)
    except IngestError as exc:
        raise InputError(str(exc)) from exc

    report = _render_json(resolved) if output_format == "json" else _render_text(resolved)

    if output is not None:
        output.write_text(report + "\n", encoding="utf-8")
        click.echo(f"Wrote {output}")
    else:
        click.echo(report)

    if resolved.issues:
        # A distinct status lets a pipeline tell "nothing loaded" from "loaded,
        # but the inventory needs fixing" without parsing the report.
        ctx.exit(EXIT_ROWS_REJECTED)


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
"""Every scoring table, by the name of the metric it scores.

The keys outlive the `closed-issues` and `releases` sub-commands v0.3.0
retired: a band table is published for every scored metric whether or not the
metric has a command of its own, which is what `L2-CLI-006` requires.
"""


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
