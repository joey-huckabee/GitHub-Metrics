# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Repository inventory ingestion.** `github_metrics.ingest` reads an
  `owner,repoid` CSV into validated `RepositoryRef` values. It performs no
  network access and collects no metrics, so it needs no `GITHUB_TOKEN`.
  Tolerates a UTF-8 BOM, CRLF or LF endings, reordered/recased/padded headers,
  unknown columns, blank lines and padded values; rejects malformed headers,
  non-UTF-8 bytes, binary content, malformed rows, invalid GitHub names and
  duplicates.
- **`github_metrics.validation`** with the GitHub account and repository name
  grammars, returning the reason a name was rejected rather than a boolean.
- **`github_metrics.errors`** — a stable `GM-<AREA>-<NNN>` code taxonomy,
  separating file-level exceptions from row-level `RowIssue` records.
- **Concurrent multi-file reads** via `read_repository_csvs`, deterministic in
  both result order and error precedence.
- **`github-metrics ingest`** command, with `--strict`, `--workers`,
  `--format {text,json}` and `--output`, and distinct exit statuses (0 clean,
  3 rows rejected, 2 unreadable).
- **`github_metrics.sources`**, where a repository gets named. A source is a
  slug, a GitHub URL, or a CSV inventory, and every command that takes
  repositories takes all three, mixed freely in one invocation. URLs are
  accepted with or without a scheme, with `www.`, with a trailing slash, with
  `.git`, in the `git@` clone form, and with a leftover browsing path.
- **A URL on another host is refused rather than reduced** (`GM-ING-017`).
  `gitlab.com/foo/bar` reduces perfectly well to `foo/bar`, which is exactly
  the problem: the result is a plausible reference to a different repository,
  so a mistake would become a row instead of an error.
- **A repository named twice is collected once** (`GM-ING-015`), whether the
  repetition is within a file, across files, or between a file and the command
  line. The first mention keeps its position and the second is reported against
  the source that already had it. Collecting it twice would spend the rate
  limit twice and produce two rows no consumer could tell apart.
- **`github-metrics metrics`**, the release deliverable. Takes the same sources
  as `validate`, collects concurrently, scores, and writes `githubmetrics.csv`
  — one row per accepted reference, in input order. `--output` (a directory
  gets the default filename), `--format csv|json`, `--fields`, `--workers`,
  `--strict`. With no destination the rows render vertically on the console.
- **A rate-limit pre-flight.** Collection costs one GraphQL point per
  repository, so a run confirms the token can cover it before collecting
  anything (`GM-COL-004`, exit 5). A run that discovers exhaustion halfway has
  already spent what it had and leaves a file where the repositories at the end
  are indistinguishable from ones that could not be read. A reserve of ten
  points is held back so a run cannot leave the token at exactly zero.
- **`github-metrics contributors`**, replacing the other half of `repo`. Same
  sources, separate dataset. `metrics` never pays for contributor pages, which
  are the expensive half of the request budget and produce columns
  `githubmetrics.csv` does not have. Its own columns are not settled, so it
  emits JSON.
- **Documentation set** under `docs/`: user guide, CLI reference, error
  catalog, architecture, maintainer guide, roadmap, three levels of
  requirements (`L1.md`, `L2.md`, `L3.md`) and three ADRs.
- **`scripts/build-trace-matrix.py`**, which generates `docs/TRACE-MATRIX.md`
  from the requirement documents and `@pytest.mark.requirement` markers. CI
  runs it with `--check` so the matrix cannot drift.
- **Byte-exact CSV fixtures** under `tests/data/`, exempted from line-ending
  normalisation so a CRLF fixture keeps testing CRLF.

- **Metric collection over GraphQL.** `github_metrics.collect` gathers closed
  issues, releases and tags, and repository timestamps. `collect.repository`
  fetches every value a row needs in **one query costing one point**, so an
  inventory of any size fits a 5,000-point hourly budget.
- **Owner and organisation.** A row records the owner the inventory supplied
  and the owner GitHub reports. `organization` carries the owning
  organisation's login, and is empty when the repository belongs to an
  individual account - which is the answer, not a gap. A repository GitHub
  redirects to a new owner (`tiangolo/fastapi` to `fastapi/fastapi`) is
  reported, since the inventory entry still resolves but no longer matches.
- **Scoring.** `github_metrics.analysis` scores closed issues, releases,
  prevalence, last update, maturity, stars, forks and the trusted-organisation
  bonus. Every band table is data rather than an `if`/`elif` chain, is total
  over its domain, and renders itself for the documentation and the CLI.
- **Elapsed time anchored to `scan_date`.** `analysis.elapsed` measures age and
  time-since-update against the single instant recorded for the run, so rows in
  one file stay comparable and re-running an inventory in a different order
  cannot change its numbers.
- **`github-metrics closed-issues`** and **`github-metrics releases`**, probe
  commands reporting one metric with `--explain` and `--format json`.
- **`github-metrics bands [METRIC]`** prints the scoring tables, rendered from
  the objects the scorer reads, needing neither token nor network.
- **Credential handling.** `--token`, `--token-file`, and a free up-front check
  against the rate-limit endpoint, with exit 7 for no token and exit 8 for a
  token GitHub rejects. `LOG_LEVEL=DEBUG` reports the token's source, kind,
  length and scopes; the value itself is never logged, at any level.
- **`github_metrics.analysis.trusted_orgs`** with a replaceable registry of
  trusted owners and a 10-point bonus.
- **`stars` and `forks` are settled**: GraphQL `stargazerCount`, and
  `forkCount` for **direct forks only** rather than the whole fork network.
  A fork taken from another fork measures that fork's visibility, not the
  original project's.
- **`github_metrics.analysis.total`** sums the six components. The ceiling of
  **85.0** is computed from their weights rather than clamped, so a component
  that drifts past its own share shows up as a total that overshoots — and is
  reported with the offending component named, instead of being flattened back
  to 85.0 with nothing to say why.

- Initial project scaffolding: Poetry packaging, CLI entry point, tooling
  configuration (black, isort, ruff, pylint, mypy, vulture, pytest), pre-commit
  hooks, and GitHub Actions CI.
- `github-metrics repo OWNER/NAME` collects point-in-time repository metrics.
- `github-metrics rate-limit` reports remaining API budget.
- `github_metrics.logger` with `LogLevels`, a re-runnable `reset_logger()`, and a
  deprecated `Logger` wrapper kept for older callers.
- `-V` as a short alias for `--version`, the version in the `--help` banner, and a
  `tool_version` field on every `RepositoryMetrics` snapshot.

### Changed

- **`ingest` is now `validate`, and `ingest.py` is now `sources/csv_inventory.py`.**
  The old name described what the command did to the file rather than what it
  did for the user, and it sat oddly beside `metrics` and `contributors`, which
  are the commands that ingest anything. `validate` says what it is for:
  checking a list before any rate limit is spent on it, on a machine with no
  token at all.
- **`validate --format json` emits one document per run**, not one per file.
  The sources are an input detail; a consumer wants the references in the order
  they were asked for. `source_line` is `null` for a repository named on the
  command line, which has no line to point at.
- **A `RowIssue` may carry no line number** — a reference typed as an argument
  has no line to point at, and the rendered message drops the part it cannot
  fill rather than printing `line None`.
- **`repo` is gone**, replaced by `metrics` and `contributors` rather than
  renamed to either: it was scaffolding, and it collected a different set of
  fields from both.
- The CLI resolves configuration lazily, so a command that needs no credentials
  no longer fails when none are configured.
- Python 3.14 is now a required CI target rather than `continue-on-error`; it passed
  on the first run.
- Bumped `actions/checkout` to v7, `actions/setup-python` to v7,
  `actions/upload-artifact` to v7, and `github/codeql-action` to v4.
- The CLI configures logging through `reset_logger()` instead of
  `logging.basicConfig()`.

- **Counts come from GraphQL, not REST.** REST cannot count closed issues
  correctly at any price: `open_issues_count` includes pull requests, the
  issues endpoint returns pull requests with no server-side filter, and since
  that endpoint moved to cursor pagination PyGithub's `totalCount` returns
  **1** for every repository, silently. GraphQL returns an exact total,
  excludes pull requests by construction, and costs one point.
- **Distinct versions are counted once.** The release metric scored
  `releases + tags`, but publishing a release creates a tag, so every release
  was counted twice - by between 1.3x and 2x across a sample, and the
  overstatement grew with how consistently a project used the Releases feature.
  The scored value is now the distinct version count.
- **Prevalence combines the stronger of its two signals** rather than falling
  back from one to the other, and excludes the issue signal for a repository
  whose tracker is disabled.
- **A project with no evidence at all scores 0.0**, rather than collecting the
  lowest non-zero band for existing.
- **`client_name` is renamed `repo_name` and holds what it always held.** The
  original header was misleading and this tool read it, wrongly, as a second
  copy of the owner — a mistake only possible because the reference row is
  `cline/cline`, where the owner and the repository are spelled the same. It is
  the repository's name, and together with `owner` it is what makes a row
  identifiable at all. Still nineteen columns.
- **`repo_name` is verified against the API rather than echoed from the input.**
  The name comes from the query already being sent, so verification costs
  nothing.
- **A repository that has been renamed or transferred is refused, not
  collected** (`GM-COL-003`, exit 4). GitHub redirects both silently, so a
  stale inventory entry still resolves and returns correct numbers — about a
  repository the inventory does not name, with nothing in the output to say so.
  The row is emitted with its identity columns and no measurements, and the
  warning names the current `owner/name` so the list can be fixed by copying
  it. Case is not a difference, since GitHub names are case-insensitive.
- **`organization` is empty for an unfetchable repository**, alongside the
  metrics, rather than being treated as an always-known identity column. It
  reads like identity but only the API can report it, and a repository that
  could not be read reported nothing.
- **Collection and scoring narrate at DEBUG rather than INFO.** Twenty log
  lines moved. Every one of them fires once per repository, so on an inventory
  of four hundred they were four hundred copies each — enough to bury the one
  line an operator was looking for. The detail is unchanged and one level down;
  warnings are untouched. The only INFO line left in the package is ingestion's
  per-file summary, which is per run rather than per repository.

### Fixed

- `cli.py` and `metrics.py` declared a module `LOGGER` and never used it.
  `cli.py` no longer declares one (its output goes through `click.echo`), and
  `metrics.py` now logs: a debug line when a license lookup fails, so an API
  error is distinguishable from a repository that simply has no license, and an
  info line when the contributor list is truncated by `--contributors`.
- Tests restore the `github_metrics` logger after each case. `reset_logger()`
  sets `propagate = False` on a process-wide singleton, so a test that ran the
  CLI could stop `caplog` from seeing later tests' records.
- Line endings are normalised to LF and pinned by `.gitattributes`.
- A shared `.vscode/` workspace pins every linter to this project's 100-column
  line length, removing spurious `line too long` warnings.

- **A gap in the closed-issue bands.** The chain ended `< 500 -> 0.9` and
  `> 500 -> 1.0`, so a count of exactly 500 matched no branch and returned the
  initial `0` - a plausible number rather than an error. Band tables cannot
  have that gap and are tested across their whole domain.
- **A units mismatch in the maturity score.** The first branch compared an age
  in *days* against a threshold in *years*, so only a repository under six
  hours old counted as too young and everything from six hours to three months
  was credited 0.2 - three points of maturity for a repository created
  yesterday.
- **A weight lost from the last-update bands.** A boundary intended as 0.9 was
  written as 0.05 and, being below every other weight, decided nothing: it was
  inert across the whole 0-40,000 hour range.
- **A negative elapsed time is reported and clamped to zero** instead of being
  accepted as "just updated" and scored at full marks. A repository can be
  updated while a long scan runs, and clocks disagree.
- **GraphQL failures are no longer read as success.** The API reports errors
  with HTTP 200 and an `errors` array, so a path that checks only the status
  sees success and then reads a null repository. `NOT_FOUND` is classified
  separately: a deleted or renamed repository is an expected outcome of a valid
  reference, not a defect.
- **`scripts/build-trace-matrix.py` and `github_metrics/errors.py` are pure
  ASCII.** Vulture reads source with the locale encoding, so on a Windows
  console an em-dash made a file unparseable and it was **silently skipped** -
  the linter passed without having looked at it.

[Unreleased]: https://github.com/joey-huckabee/GitHub-Metrics/compare/8feb637...HEAD
