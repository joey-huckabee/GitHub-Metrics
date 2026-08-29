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
