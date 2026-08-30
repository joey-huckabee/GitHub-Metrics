# GitHub-Metrics — CLI Reference

Complete reference for the `github-metrics` command. For a task-oriented
introduction see [`USER-GUIDE.md`](USER-GUIDE.md).

The same entry point is available as `python -m github_metrics`.

## Synopsis

```
github-metrics [--env-file PATH] [--token TEXT | --token-file PATH]
               [--no-verify-token] [-V|--version] [-h|--help] COMMAND [ARGS]...
```

## Global options

| Option | Description |
|---|---|
| `--env-file PATH` | Read configuration from this `.env` instead of the nearest one. |
| `-V`, `--version` | Print the version and exit. |
| `-h`, `--help` | Show help and exit. Available on every subcommand. |
| `--token TEXT` | GitHub token, overriding `GITHUB_TOKEN`. See the warning below. |
| `--token-file PATH` | Read the token from this file. Safer than `--token`. |
| `--no-verify-token` | Skip the credential check. |

Configuration is resolved **lazily**: `--env-file` is recorded when the command
group starts, but the file is only required by commands that reach the GitHub
API. `ingest` therefore runs on a machine with no credentials at all.

### Credentials

Commands that reach the GitHub API need a token. It is taken from, in order of
precedence:

1. `--token` or `--token-file`
2. `GITHUB_TOKEN` in the environment
3. `GITHUB_TOKEN` in a `.env` file

> **A token passed as `--token` is visible to other processes on the machine
> and is written to your shell history.** Prefer the environment or a `.env`
> file wherever either will do. `--token` exists for the cases where neither
> is available, such as a CI step that already holds the value in a variable.

`--token-file` reads the token from a file and is the safer of the two: a path
is not a secret, so it can appear in a command line, a script, or a process
listing without disclosing anything. It suits a container secret mounted at a
path, or a file the operating system keeps at mode 600.

Surrounding whitespace is stripped, so a file written by `echo` works. A token
with a trailing newline is rejected by GitHub with the same 401 as an expired
one, which is a hard failure to read backwards from - hence the strip.

Supplying **both** `--token` and `--token-file` is a usage error (exit 2)
rather than a precedence rule. Two answers to one question can only be a
mistake, and guessing which was meant would be worse than saying so.

```console
$ github-metrics --token-file /run/secrets/github rate-limit
4983 core requests remaining

$ github-metrics --token-file /run/secrets/absent rate-limit
Error: could not read --token-file /run/secrets/absent: [Errno 2] No such file or directory
$ echo $?
2
```

Before doing any work, the tool confirms the token is accepted. The check calls
the rate-limit endpoint, which **does not count against the rate limit**, so it
costs nothing but one round trip and turns a mid-run authentication failure
into one message up front. `--no-verify-token` skips it.

`LOG_LEVEL=DEBUG` reports the token's source, kind, length, the scopes GitHub
returns, and the remaining budget on both APIs. **The token value is never
logged**, at any level, in whole or in part.

```console
$ github-metrics rate-limit
Error: [GM-CFG-001] no GitHub token available. Pass --token, set GITHUB_TOKEN in
the environment, or copy .env.example to .env and add one.
$ echo $?
7

$ github-metrics --token ghp_expired... rate-limit
Error: [GM-CFG-002] GitHub rejected the token (401). It may be expired, revoked,
or mistyped. Kind detected from its prefix: classic personal access token.
$ echo $?
8
```

## Configuration

Read from the environment, or from a `.env` file. See
[`../.env.example`](../.env.example).

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `GITHUB_TOKEN` | for API commands | — | Token for all API calls |
| `GITHUB_API_URL` | no | `https://api.github.com` | Point at GitHub Enterprise |
| `GEOCODER_USER_AGENT` | no | `github-metrics` | User-Agent sent to Nominatim |
| `LOG_LEVEL` | no | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

Diagnostics go to **stderr** at `LOG_LEVEL`. Data goes to **stdout**. The two
never mix, so `github-metrics ingest list.csv --format json | jq` works even at
`LOG_LEVEL=DEBUG`.

---

## `ingest`

Read one or more `owner,repoid` CSV files and report what they contain.

```
github-metrics ingest [OPTIONS] SOURCES...
```

Validates and reports only. **No network access, no metric collection, no
`GITHUB_TOKEN` required.**

### Arguments

| Argument | Description |
|---|---|
| `SOURCES...` | One or more CSV paths. At least one is required. Directories are not walked — expand a glob in your shell. |

### Options

| Option | Default | Description |
|---|---|---|
| `--strict` | off | Abort on the first bad row instead of reporting all of them. |
| `--workers N` | `min(files, 8)` | Threads used to read multiple files. Has no effect on the result. |
| `--format {text,json}` | `text` | Report format. |
| `--output PATH` | stdout | Write the report here instead. |

### Exit status

| Code | Meaning |
|---|---|
| `0` | Every data row was accepted. |
| `3` | The sources were read, but at least one row was rejected. |
| `6` | A source could not be read, or `--strict` abandoned the read. |

`3` is the one worth wiring into automation: it means the inventory loaded and
is usable, but is degraded. Collapsing it into `0` hides that; collapsing it
into failure discards a result that is usually still worth having.

```bash
github-metrics ingest inventory.csv
case $? in
  0) echo "clean" ;;
  3) echo "loaded, but rows need fixing" ;;
  2) echo "could not read the file"; exit 1 ;;
esac
```

### Input format

A header naming `owner` and `repoid`, then one row per repository:

```csv
owner,repoid
urllib3,urllib3
bokeh,bokeh
pypa,virtualenv
```

Each row denotes `https://github.com/<owner>/<repoid>`.

**Accepted without complaint:**

- Either column order
- Any casing or surrounding whitespace in header names
- Extra columns, which are ignored
- A UTF-8 byte-order mark (what Excel writes)
- CRLF or LF line endings
- Blank lines, and a trailing `,,` row
- Whitespace around values
- Quoted fields, per normal CSV rules

**Rejected**, with a code from [`ERROR-CATALOG.md`](ERROR-CATALOG.md):

- A header not naming both required columns, or naming one twice
- Bytes that are not UTF-8; content that is binary
- Rows with too few fields, an empty cell, or a name that is not valid GitHub
  syntax
- Repeated repositories, matched case-insensitively — the first is kept

### Examples

```bash
# Read one inventory
github-metrics ingest inventory.csv

# Several at once, read concurrently
github-metrics ingest teams/*.csv

# Machine-readable, for the next stage
github-metrics ingest inventory.csv --format json --output inventory.json

# Fail the pipeline on any defect
github-metrics ingest inventory.csv --strict

# Serialise the reads while diagnosing an I/O problem
github-metrics ingest teams/*.csv --workers 1

# Per-row detail
LOG_LEVEL=DEBUG github-metrics ingest inventory.csv
```

### Output

Text (default) — one line per file, then its repositories, then its issues:

```
inventory.csv: 3 repositories, 0 rejected
  urllib3/urllib3
  bokeh/bokeh
  pypa/virtualenv
```

With several files, a combined total is appended.

JSON (`--format json`) — an array with one object per source:

```json
[
  {
    "source": "inventory.csv",
    "repositories": [
      { "owner": "urllib3", "repoid": "urllib3", "source_line": 2 }
    ],
    "issues": [
      {
        "code": "GM-ING-015",
        "line": 5,
        "message": "pypa/virtualenv already appears on line 4; keeping the first occurrence",
        "source": "inventory.csv"
      }
    ],
    "rows_read": 2
  }
]
```

`source_line` is the 1-based physical line the row occupied, so a later failure
can be traced back to the row that requested it.

---

---

## `closed-issues`

Report closed-issue counts and the score they produce, for one repository.
**Requires `GITHUB_TOKEN`.**

```
github-metrics closed-issues [OPTIONS] OWNER/REPOID
```

A probe for one metric at a time. Each metric gets one of these as it is
defined, so a definition can be checked against real repositories before it is
wired into a full collection run.

Counts **exclude pull requests** and cost one GraphQL point, regardless of how
many issues the repository has.

| Argument | Description |
|---|---|
| `OWNER/REPOID` | The repository, e.g. `pypa/virtualenv`. A value without a `/` is a usage error. |

| Option | Default | Description |
|---|---|---|
| `--explain` | off | Append the scoring bands, so a surprising weight can be traced to a band. |
| `--format {text,json}` | `text` | Report format. `json` includes the bands only with `--explain`. |

### Exit status

| Code | Meaning |
|---|---|
| `0` | The repository was read |
| `2` | Usage error, including a slug with no `/` |
| `4` | The repository could not be read - deleted, renamed, or private |
| `7` | No token supplied |
| `8` | GitHub rejected the token |

### Examples

```console
$ github-metrics closed-issues pypa/virtualenv
pypa/virtualenv
  closed issues      1429
  open issues           0
  tracker         enabled
  weight              1.0

$ github-metrics closed-issues urllib3/urllib3 --explain
urllib3/urllib3
  closed issues      1241
  open issues         135
  tracker         enabled
  weight              1.0

closed-issue bands:
  <20    -> 0.1
  <50    -> 0.2
  <100   -> 0.3
  <150   -> 0.4
  <300   -> 0.6
  <400   -> 0.8
  <500   -> 0.9
  >=500  -> 1.0

$ github-metrics closed-issues cline/cline --format json
{
  "owner": "cline",
  "repoid": "cline",
  "closed_issues": 3770,
  "open_issues": 691,
  "issues_enabled": true,
  "weight": 1.0,
  "bands": null
}

$ github-metrics closed-issues ghost/definitely-not-real
Error: [GM-COL-001] closed issues for ghost/definitely-not-real: Could not resolve to a Repository
$ echo $?
4
```

A repository with its issue tracker turned off is called out, because zero
closed issues then describes its configuration rather than its maintenance:

```console
$ github-metrics closed-issues some-org/mirror
some-org/mirror
  closed issues         0
  open issues           0
  tracker        DISABLED
  weight              0.1
  note: the issue tracker is off, so zero is a configuration fact rather than a maintenance one
```

---

## `releases`

Report release and tag counts, and the score they produce, for one repository.
**Requires `GITHUB_TOKEN`.**

```
github-metrics releases [OPTIONS] OWNER/REPOID
```

The scored value is the **distinct version count**, not releases plus tags.
Publishing a GitHub Release creates a tag, so a release is already counted
among the tags; adding the two together counts every release twice. Measured
across a sample of repositories the sum overstated by between 1.3x and 2x, and
the inflation grew with how diligently a project used Releases - so the metric
rewarded the tooling rather than the release cadence. See
[`METRICS.md`](METRICS.md) for the derivation.

| Argument | Description |
|---|---|
| `OWNER/REPOID` | The repository, e.g. `pypa/virtualenv`. A value without a `/` is a usage error. |

| Option | Default | Description |
|---|---|---|
| `--explain` | off | Append the scoring bands. |
| `--format {text,json}` | `text` | Report format. `json` includes the bands only with `--explain`. |

Costs one GraphQL point, whatever the length of the release history.

### Exit status

| Code | Meaning |
|---|---|
| `0` | The repository was read |
| `2` | Usage error, including a slug with no `/` |
| `4` | The repository could not be read - deleted, renamed, or private |
| `7` | No token supplied |
| `8` | GitHub rejected the token |

### Examples

```console
$ github-metrics releases pypa/virtualenv
pypa/virtualenv
  releases                  98
  tags                     285
  distinct versions        285
  weight                   1.0
  tags with no release     187
  note: releases + tags would report 383 (1.34x), counting every release twice
  note: at or above 80 versions the weight is capped at 1.0, so this project is indistinguishable from any other above that line
```

Both notes are diagnostics rather than warnings. The first quantifies what the
double count would have added for this repository; the second says the score
has saturated, which matters when a portfolio of mature projects all land on
1.0 and the metric stops separating them.

```console
$ github-metrics releases cline/cline --format json
{
  "owner": "cline",
  "repoid": "cline",
  "releases": 398,
  "tags": 717,
  "distinct_versions": 717,
  "legacy_sum": 1115,
  "weight": 1.0,
  "bands": null
}
```

`legacy_sum` is reported so a value from the previous definition can be
recognised in an older spreadsheet, not because anything consumes it.

A repository that has never tagged anything scores 0.0 rather than the lowest
non-zero band - a project with nothing at all scores nothing.

---

## `bands`

Print the scoring bands for one metric, or for every metric. **Needs no token
and no network.**

```
github-metrics bands [METRIC]
```

| Argument | Description |
|---|---|
| `METRIC` | One of `closed-issues`, `last-update`, `maturity`, `popularity`, `releases`. Omit it to print all five. |

The tables *are* the scoring model - the command renders the same objects the
scoring code reads, so it cannot drift from the implementation the way a
transcribed table in a document can. It is how a surprising score gets
explained without opening source, and how the model gets reviewed before a
collection run is worth starting.

```console
$ github-metrics bands releases
release bands (on distinct versions):
  <1     -> 0.0
  <5     -> 0.1
  <10    -> 0.2
  <20    -> 0.3
  <40    -> 0.4
  <50    -> 0.5
  <60    -> 0.6
  <70    -> 0.7
  <80    -> 0.8
  >=80   -> 1.0

$ github-metrics bands popularity
star and fork bands (weight for a count below each bound):
   bound   stars   forks
  <5         0.0     0.0
  <10        0.1     0.1
  <20        0.2     0.2
  <30        0.3     0.3
  <40        0.4     0.4
  <50        0.5     0.5
  <70        0.6     0.6
  <90        0.7     0.7
  <110               0.8
  <150       0.8     0.9
  <300       0.9
   above     1.0     1.0
```

Stars and forks share one table because they are the same measurement of
attention at different scales: forks are rarer, so their bounds are tighter.
A blank cell means that bound belongs to only one of the two.

### Exit status

| Code | Meaning |
|---|---|
| `0` | The tables were printed |
| `2` | Usage error - an unrecognised metric name. The message lists the valid ones. |


## `repo`

Collect metrics for a single repository. **Requires `GITHUB_TOKEN`.**

```
github-metrics repo [OPTIONS] FULL_NAME
```

| Argument | Description |
|---|---|
| `FULL_NAME` | `owner/name`, e.g. `python/cpython`. |

| Option | Default | Description |
|---|---|---|
| `--geocode` | off | Resolve contributor locations to coordinates via Nominatim. |
| `--contributors N` | `25` | Maximum contributors to inspect. |
| `--output PATH` | stdout | Write JSON here instead. |

```bash
github-metrics repo python/cpython
github-metrics repo python/cpython --geocode --output cpython.json
```

Every snapshot carries a `tool_version` field recording which release produced
it, so archived results stay attributable.

> **Note.** `repo` takes one repository at a time and does not yet accept an
> ingested inventory. Wiring ingestion to collection is the next milestone; see
> [`ROADMAP.md`](ROADMAP.md).

---

## `rate-limit`

Report the core API requests remaining for the current token. **Requires
`GITHUB_TOKEN`.**

```
github-metrics rate-limit
```

---

## Exit status summary

Severity-ordered; the highest applicable code wins. Codes 1 and 2 belong to
click and are listed for completeness rather than chosen. See
[`adr/0004-exit-code-scheme.md`](adr/0004-exit-code-scheme.md).

| Code | Meaning | Output produced? | Commands |
|---|---|---|---|
| `0` | Success | yes | all |
| `1` | Configuration error, e.g. a missing token | no | all |
| `2` | Usage error - malformed command line | no | all |
| `3` | Degraded: some input rows were rejected | yes | `ingest` |
| `4` | Degraded: a repository could not be read | yes | `closed-issues`, `releases` |
| `5` | Aborted: API budget exhausted | partial | reserved |
| `6` | Aborted: the input could not be read | no | `ingest` |
| `7` | Aborted: no GitHub token supplied | no | API commands |
| `8` | Aborted: GitHub rejected the token | no | API commands |

The 3-4 against 5-6 split is the load-bearing part: **3 and 4 still produced a
usable result; 5 and 6 did not.** A caller can test `$? -ge 3` for "something
was wrong" and `$? -ge 5` for "nothing usable came out".
