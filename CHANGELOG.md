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
