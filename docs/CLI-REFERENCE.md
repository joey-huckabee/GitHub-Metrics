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
API. `validate` therefore runs on a machine with no credentials at all.

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
never mix, so `github-metrics validate list.csv --format json | jq` works even at
`LOG_LEVEL=DEBUG`.

---

## `validate`

Report what SOURCES name, without collecting anything.

```
github-metrics validate [OPTIONS] SOURCES...
```

Validates and reports only. **No network access, no metric collection, no
`GITHUB_TOKEN` required.**

### Arguments

| Argument | Description |
|---|---|
| `SOURCES...` | One or more sources, in any mix. At least one is required. Directories are not walked — expand a glob in your shell. |

A **source** is one of three things, and every command that takes repositories
takes all three:

| Form | Example |
|---|---|
| slug | `pypa/virtualenv` |
| GitHub URL | `https://github.com/pypa/virtualenv`, `github.com/pypa/virtualenv/tree/main`, `git@github.com:pypa/virtualenv.git` |
| CSV inventory | `inventory.csv` |

URLs are accepted with or without a scheme, with `www.`, with a trailing slash,
with `.git`, and with a leftover path from browsing. A URL naming a host other
than GitHub is **refused rather than reduced**: `gitlab.com/foo/bar` would
reduce perfectly well to `foo/bar`, which is exactly the problem. On GitHub
Enterprise, name the repository as a slug and point `GITHUB_API_URL` at the
instance.

Whether an argument is a path or a repository is decided by four rules, in
order:

1. It looks like a URL.
2. It exists on disk as a file.
3. It ends in `.csv`.
4. Otherwise it is a slug.

Rule 3 earns its place by diagnosis: without it a mistyped `inventroy.csv` is
read as a slug and refused for having no `/`, which is true and useless. The
cost is that a repository whose name ends in `.csv` has to be given as a URL.

**A repository named twice is collected once.** The repetition is reported
against the source that named it first, and the first mention is the one that
keeps its position. Collecting it twice would spend the rate limit twice and
produce two identical rows.

### Options

| Option | Default | Description |
|---|---|---|
| `--strict` | off | Abort on the first bad row instead of reporting all of them. |
| `--workers N` | `min(files, 8)` | Threads used to read multiple CSV files. Has no effect on the result. |
| `--format {text,json}` | `text` | Report format. |
| `--output PATH` | stdout | Write the report here instead. |

### Exit status

| Code | Meaning |
|---|---|
| `0` | Every reference was accepted. |
| `3` | The sources were read, but at least one reference was rejected. |
| `6` | A source could not be read, or `--strict` abandoned the read. |

`3` is the one worth wiring into automation: it means the inventory loaded and
is usable, but is degraded. Collapsing it into `0` hides that; collapsing it
into failure discards a result that is usually still worth having.

```bash
github-metrics validate inventory.csv
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
github-metrics validate inventory.csv

# A repository named directly, in either form
github-metrics validate pypa/virtualenv
github-metrics validate https://github.com/pypa/virtualenv

# Mixed, in one command
github-metrics validate inventory.csv cline/cline github.com/psf/requests

# Several files at once, read concurrently
github-metrics validate teams/*.csv

# Machine-readable, for the next stage
github-metrics validate inventory.csv --format json --output inventory.json

# Fail the pipeline on any defect
github-metrics validate inventory.csv --strict

# Serialise the reads while diagnosing an I/O problem
github-metrics validate teams/*.csv --workers 1

# Per-reference detail
LOG_LEVEL=DEBUG github-metrics validate inventory.csv
```

### Output

Text (default) — the references, then the issues, then one summary line:

```
$ github-metrics validate inventory.csv cline/cline
urllib3/urllib3
bokeh/bokeh
pypa/virtualenv
cline/cline
4 repositories, 0 rejected, from 1 file(s)
```

A refused reference names its source, and its line when it had one:

```
$ github-metrics validate inventory.csv https://gitlab.com/a/b
urllib3/urllib3
! <argument>: [GM-ING-017] gitlab.com is not GitHub. On GitHub Enterprise, name the repository as owner/repoid and point GITHUB_API_URL at the instance
! inventory.csv:5: [GM-ING-015] pypa/virtualenv already appears on line 4; keeping the first occurrence
1 repositories, 2 rejected, from 1 file(s)
```

JSON (`--format json`) — **one document for the run**, not one per file. The
sources are an input detail; a consumer wants the references in the order they
were asked for:

```json
{
  "repositories": [
    { "owner": "urllib3", "repoid": "urllib3", "source_line": 2 },
    { "owner": "cline", "repoid": "cline", "source_line": null }
  ],
  "issues": [],
  "accepted": 2,
  "rejected": 0,
  "files": ["inventory.csv"]
}
```

`source_line` is the 1-based physical line the row occupied, so a later failure
can be traced back to the row that requested it. It is `null` for a repository
named on the command line, which has no line to point at.

---

---

## `metrics`

Collect metrics for every repository SOURCES names and write
`githubmetrics.csv`. **Requires `GITHUB_TOKEN`.**

```
github-metrics metrics [OPTIONS] SOURCES...
```

This is the release deliverable. `SOURCES` are the same slugs, URLs and CSV
inventories `validate` takes, mixed freely.

| Option | Default | Description |
|---|---|---|
| `--output PATH` | console | Where to write. A directory receives `githubmetrics.csv`. |
| `--format {csv,json}` | `csv` | Output format, for a file or the console. |
| `--fields a,b,c` | all | Columns to emit, always rendered in canonical order. |
| `--workers N` | `min(repositories, 8)` | Concurrent collections. |
| `--strict` | off | Abort on the first bad input reference. |

### What it costs, and when it refuses

One GraphQL point per repository, checked **before anything is collected**:

```console
$ github-metrics metrics huge-inventory.csv
Error: [GM-COL-004] 6000 repositories need 6000 GraphQL points but only 4983
remain. Short by 1017. Wait for the hourly reset, or collect fewer repositories
per run
```

The check is the point. A run that discovers exhaustion halfway has already
spent what it had and produced a file that is part measurement and part
absence, with nothing to distinguish the two {EM} the repositories at the end of
the inventory look exactly like ones that could not be read. Refusing costs one
request that does not count against the limit.

No reserve is held back, so the budget runs to zero and a full hourly quota
collects exactly 5,000 repositories — the largest run the tool can do. Keeping
points back would buy a convenience by refusing a run the token could actually
have finished.

### Rows

One row per accepted reference, **in input order**. Results are collected
concurrently but assembled in the order asked for, so two runs of one inventory
produce byte-identical files and a diff shows changed data rather than
reordered rows.

A repository that could not be read still produces a row:

```csv
repo_name,owner,organization,scan_date,scan_id,stars,...
virtualenv,pypa,pypa,2026-08-31 01:18:40+00:00,ae32d273-...,5041,...
definitely-not-real,ghost,,2026-08-31 01:18:40+00:00,ae32d273-...,,...
```

Identity from the input, measurements empty. **Empty, not zero** {EM} zero is a
legitimate score for a repository that was measured and found wanting.

The failures are also named on stderr, because a file with empty rows says
something went wrong and only this says what:

```
! ghost/definitely-not-real: [GM-COL-001] Could not resolve to a Repository
! tiangolo/fastapi: [GM-COL-003] tiangolo/fastapi has been moved to fastapi/fastapi; update the inventory
```

### Exit status

| Code | Meaning |
|---|---|
| `0` | Every reference was collected |
| `3` | An input reference was rejected; the rest were collected |
| `4` | A repository could not be collected, or has moved |
| `5` | The remaining budget could not cover the run; nothing was collected |
| `6` | A source could not be read |
| `7`, `8` | No token, or a token GitHub rejected |

Severity-ordered, highest applicable wins. `4` beats `3`: both still wrote a
usable file, and an unreadable repository is the worse news.

### Examples

```bash
# Console, vertical, one block per repository
github-metrics metrics pypa/virtualenv

# The deliverable
github-metrics metrics inventory.csv --output githubmetrics.csv

# A directory picks the filename
github-metrics metrics inventory.csv --output ./results/

# Mixed sources
github-metrics metrics inventory.csv cline/cline github.com/psf/requests

# Only the columns a dashboard needs
github-metrics metrics inventory.csv --fields owner,repo_name,total_score

# JSON, to a file or a pipe
github-metrics metrics inventory.csv --format json | jq '.[].total_score'
```

---

## `contributors`

Collect contributor detail for every repository SOURCES names. **Requires
`GITHUB_TOKEN`.**

```
github-metrics contributors [OPTIONS] SOURCES...
```

A **separate dataset** from `githubmetrics.csv`, and deliberately a separate
command: `metrics` never pays for contributor pages, which are the expensive
half of the request budget and produce columns `githubmetrics.csv` does not
have.

| Option | Default | Description |
|---|---|---|
| `--contributors N` | `25` | Maximum contributors to inspect per repository. |
| `--geocode` | off | Resolve contributor locations to coordinates via Nominatim. |
| `--output PATH` | stdout | Write JSON here instead. |

> **Its columns are not settled.** The output is JSON until they are, and the
> shape may change. `metrics` is the stable contract.

Unlike `metrics`, this walks contributor pages, so its cost is **not** one
point per repository and it is not covered by the pre-flight budget check.

```bash
github-metrics contributors pypa/virtualenv
github-metrics contributors inventory.csv --geocode --output contributors.json
```

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
| `3` | Degraded: some input rows were rejected | yes | `validate` |
| `4` | Degraded: a repository could not be read, or has moved | yes | `closed-issues`, `releases` |
| `5` | Aborted: API budget exhausted | partial | reserved |
| `6` | Aborted: the input could not be read | no | `validate` |
| `7` | Aborted: no GitHub token supplied | no | API commands |
| `8` | Aborted: GitHub rejected the token | no | API commands |

The 3-4 against 5-6 split is the load-bearing part: **3 and 4 still produced a
usable result; 5 and 6 did not.** A caller can test `$? -ge 3` for "something
was wrong" and `$? -ge 5` for "nothing usable came out".
