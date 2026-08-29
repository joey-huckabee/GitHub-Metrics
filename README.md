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

```bash
# Metrics for a single repository, as JSON on stdout
poetry run github-metrics repo python/cpython

# Resolve contributor locations to coordinates and save the result
poetry run github-metrics repo python/cpython --geocode --output cpython.json

# How much API budget is left on the current token?
poetry run github-metrics rate-limit
```

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

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md). In short: `poetry install --with dev`
then `make check`.

## License

[Apache License 2.0](LICENSE)
