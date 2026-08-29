# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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
