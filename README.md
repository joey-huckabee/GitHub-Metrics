# GitHub-Metrics

Calculate GitHub Metrics for FOSS Analysis.

A small CLI and library for pulling point-in-time health metrics out of the GitHub
API — stars, forks, contributor counts, commit activity, licensing, and optionally
the geographic distribution of contributors.

## Requirements

- Python 3.10 – 3.14
- [Poetry](https://python-poetry.org/) 2.0+
- A GitHub personal access token

## Install

```bash
poetry install
cp .env.example .env      # then set GITHUB_TOKEN
```

## Usage

### Read an inventory

Describe the repositories you care about in a CSV naming `owner` and `repoid`:

```csv
owner,repoid
urllib3,urllib3
bokeh,bokeh
pypa,virtualenv
```

Each row denotes `https://github.com/<owner>/<repoid>`.

```bash
# Validate and report; no network access, no GITHUB_TOKEN required
poetry run github-metrics validate inventory.csv

# Several files at once, read concurrently
poetry run github-metrics validate teams/*.csv

# Machine-readable, for the next stage
poetry run github-metrics validate inventory.csv --format json --output inventory.json
```

Rejected rows do not stop the read. Every problem is reported with a stable
code, a line number and the offending value:

```
inventory.csv:7: [GM-ING-014] invalid repoid 'virtualenv.git': may not end in '.git'
```

Exit status is `0` for a clean read, `3` when rows were rejected, and `2` when
a file could not be read at all.

### Collect metrics

```bash
# The deliverable: one row per repository, in input order
poetry run github-metrics scan inventory.csv --output githubmetrics.csv

# Sources mix freely - slugs, URLs and inventories
poetry run github-metrics scan inventory.csv cline/cline github.com/psf/requests

# Just the columns a dashboard needs
poetry run github-metrics scan inventory.csv --fields owner,name,total_score

# Contributor detail, a separate dataset
poetry run github-metrics contributors inventory.csv --geocode --output contributors.json

# Print the scoring bands - no token, no network
poetry run github-metrics bands

# How much API budget is left on the current token?
poetry run github-metrics rate-limit

# Tool version (-V is an alias)
poetry run github-metrics --version
```

Collection costs **one GraphQL point per repository**, and the run confirms the
token can cover it before collecting anything. A repository that cannot be read
still produces a row, carrying its identity with every measurement empty, and
the run exits 4 naming which ones failed.

Logs go to **stderr**, which keeps stdout clean —
`github-metrics scan inventory.csv --format json | jq '.[].total_score'`
works even at `LOG_LEVEL=DEBUG`.

The same entry point is available as `python -m github_metrics`.

### Configuration

All configuration is read from the environment, or from a `.env` file
(see [`.env.example`](.env.example)):

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `GITHUB_TOKEN` | yes | — | Token used for all API calls |
| `GITHUB_API_URL` | no | `https://api.github.com` | Point at GitHub Enterprise |
| `GEOCODER_USER_AGENT` | no | `github-metrics` | User-Agent sent to Nominatim |
| `LOG_LEVEL` | no | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

### Logging

`github_metrics.logger.reset_logger()` configures the package logger and can be
called repeatedly without duplicating handlers:

```python
from github_metrics.logger import LogLevels, reset_logger

logger = reset_logger(LogLevels.DEBUG)   # defaults to sys.stderr
```

The `Logger` wrapper class is deprecated — use `logging.getLogger(__name__)` in new
code and let `reset_logger()` own the configuration.

## Library use

```python
from github_metrics.client import GitHubClient
from github_metrics.config import Settings
from github_metrics.metrics import collect_repository_metrics

settings = Settings.from_env()
with GitHubClient(settings) as client:
    metrics = collect_repository_metrics(client, "python/cpython")

print(metrics.to_json(indent=2))
```

## Documentation

Full documentation is in [`docs/`](docs/README.md):

- [User guide](docs/USER-GUIDE.md) — task-oriented introduction
- [CLI reference](docs/CLI-REFERENCE.md) — every flag and exit code
- [Error catalog](docs/ERROR-CATALOG.md) — every `GM-*` code explained
- [Architecture](docs/ARCHITECTURE.md) — how the pieces fit, and why
- [Maintainer guide](docs/MAINTAINER-GUIDE.md) — working on the code
- [Roadmap](docs/ROADMAP.md) — what is deferred, and why

Requirements are specified at three levels ([L1](docs/L1.md), [L2](docs/L2.md),
[L3](docs/L3.md)) and traced to tests in
[TRACE-MATRIX.md](docs/TRACE-MATRIX.md), which is generated rather than
maintained by hand. Architecture decisions are recorded in
[`docs/adr/`](docs/adr/).

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md). In short: `poetry install --with dev`
then `make check`.

## License

[Apache License 2.0](LICENSE)
