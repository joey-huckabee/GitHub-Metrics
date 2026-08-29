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

[Unreleased]: https://github.com/joey-huckabee/GitHub-Metrics/compare/8feb637...HEAD
