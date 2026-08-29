# Contributing

Thanks for helping improve **GitHub-Metrics**.

## Getting set up

```bash
poetry install --with dev
poetry run pre-commit install
cp .env.example .env      # then add a real GITHUB_TOKEN
```

## Day-to-day

| Task | Command |
| --- | --- |
| Auto-format | `make format` |
| Lint | `make lint` |
| Type check | `make types` |
| Tests + coverage | `make test` |
| Unused-code scan | `make dead` |
| Everything CI runs | `make check` |

Without `make`, run the underlying commands directly, e.g. `poetry run pytest`.

## Editor setup (VS Code)

`.vscode/` is checked in. Accept the recommended extensions when prompted and
the workspace is configured for you:

- Every linter is pinned to this project's **100-column** line length. Black,
  isort, Ruff, and Pylint default to 79 or 88 columns, so without these settings
  you would see spurious `line too long` errors.
- Format-on-save runs black, and imports are organised with isort.
- `Run and Debug` ships configurations for the CLI (`repo`, `repo --geocode`,
  `rate-limit`), each loading `.env`, plus pytest for the whole suite or the
  current file.
- The test explorer is wired to pytest with the `integration` marker excluded.

If you still see a `line too long` warning, check which extension is reporting
it. `ms-python.flake8` and `ms-python.autopep8` are listed as unwanted for this
workspace precisely because they bring their own defaults.

## Conventions

- **Formatting** is owned by `black` (line length 100) and `isort` (black profile).
  Don't hand-format; run `make format`.
- **Typing** is strict. New code needs annotations, and `mypy` must pass clean.
- **Tests** live in `tests/` and mirror the module they cover (`tests/test_config.py`
  covers `github_metrics/config.py`). Anything that hits the live GitHub API must be
  marked `@pytest.mark.integration` so it is excluded from CI.
- **Secrets** never go in the repo. `.env` is git-ignored; add new variables to
  `.env.example` with a placeholder value.

## Pull requests

Open a PR against `main`. CI runs lint, type checks, and the test matrix
(Python 3.10–3.14 on Linux, plus 3.12 on Windows and macOS) — all of it must be
green before review.
