# GitHub-Metrics — CLI Reference

Complete reference for the `github-metrics` command. For a task-oriented
introduction see [`USER-GUIDE.md`](USER-GUIDE.md).

For what a scan *does* with the data, and where it can mislead you, see
[`SCAN-PROCESS.md`](SCAN-PROCESS.md); for the API ceilings behind those
caveats, [`API-LIMITS.md`](API-LIMITS.md).

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
| `GEOCODER_USER_AGENT` | no | `github-metrics` | User-Agent sent to Nominatim. **Set this**: the policy asks for one identifying you, and the penalty for a generic agent is being blocked |
| `GEOCODE_CACHE_PATH` | no | platform cache dir | Where resolved locations are remembered between runs. An **empty value** turns persistence off |
| `LOG_LEVEL` | no | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

Diagnostics go to **stderr** at `LOG_LEVEL`. Data goes to **stdout**. The two
never mix, so `github-metrics validate list.csv --format json | jq` works even at
`LOG_LEVEL=DEBUG`.

### The geocode cache

Resolved locations persist between runs, which is what makes a re-scan cheap:
measured, one repository took 186 s on a cold cache and 42 s on a warm one.

| | |
|---|---|
| Default location | `%LOCALAPPDATA%\github-metrics\geocode.json` on Windows, `$XDG_CACHE_HOME/github-metrics/geocode.json` (or `~/.cache/…`) elsewhere |
| Move it | `GEOCODE_CACHE_PATH=/some/path.json` |
| Turn it off | `GEOCODE_CACHE_PATH=` (empty) |
| Clear it | delete the file |

Deleting it costs time and **loses no measurement** — a cached answer is
identical to a fresh one, so nothing in the output depends on whether the cache
existed. A match is trusted for a year and a miss for thirty days; a lookup
that failed because the geocoder was unreachable is never cached at all, so an
outage costs one run's resolution rather than every future run's.

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

## `scan`

Collect metrics and contributors for every repository SOURCES names, and write
both artifacts. **Requires `GITHUB_TOKEN`.**

```
github-metrics scan [OPTIONS] SOURCES...
```

This is the release deliverable. `SOURCES` are the same slugs, URLs and CSV
inventories `validate` takes, mixed freely.

| Option | Default | Description |
|---|---|---|
| `--output DIR` | `./githubmetrics` | Directory both artifacts are written into. |
| `--format {csv,json,console}` | `csv` | Form of the tabular artifact. The documents are always JSON. |
| `--fields a,b,c` | all | Columns the tabular artifact emits, always in canonical order. |
| `--workers N` | `min(repositories, 8)` | Concurrent collections. |
| `--recover-anonymous` / `--no-recover-anonymous` | on | Link contributors GitHub left anonymous whose no-reply email names their account. Costs a page per hundred identities — 34 requests for a large repository against 4 — and raised one measured repository from 11.9% to 34.2% of contributors and 87.0% to 90.3% of commits. |
| `--strict` | off | Abort on the first bad input reference. |

### What one run produces

Two artifacts, in one directory, under one scan identity:

```
githubmetrics/
  githubmetrics.csv          one row per accepted reference, in input order
  pypa/
    virtualenv.json          that row, then its contributors
  urllib3/
    urllib3.json
```

**There is no flag governing which of the two is written.** Both, always. The
two carry the same `scan_id` and `scan_date`, which is the whole reason one
command produces them: two invocations would produce two identities, and a CSV
and a folder of documents collected minutes apart cannot be joined or grouped
by the run that made them. See
[ADR-0005](adr/0005-one-scan-command-and-per-repository-json.md).

**Every CSV column is a document key.** A document is the twenty columns, in
canonical order and complete, followed by the contributor block: `contributors`
and the five aggregates over it. So a consumer that can read one needs no
mapping to read the other. The aggregates are not CSV columns — they exist
only where a contributor list was read, which is exactly when a document is
written.

`--format` and `--fields` govern the **tabular** artifact only. `--format
console` prints it instead of writing it, and the documents are still written
— there is no console rendering of four hundred documents, and no CSV form of
a nested contributor array. A document with columns filtered out would stop
being the row it has to join with, so `--fields` does not reach it either.

### Where the documents go

```
<output>/<owner>/<repoid>.json
```

Lower-cased throughout, because GitHub names are case-insensitive and
`PyPA/virtualenv` and `pypa/virtualenv` are one repository. Nested by owner
rather than flattened to `<owner>-<repoid>.json`, because hyphens are legal in
both names — the flat form maps `foo-bar/baz` and `foo/bar-baz` onto one file,
which are two different repositories rather than a duplicate pair.

**A repository that could not be fully collected gets no document.** Its CSV
row is still there, carrying whatever was known. The asymmetry is deliberate:
a CSV row is positional, so omitting one would shift what every later row
means, while a directory has no positions and an absent file says "named, not
measured" on its own.

### What it costs, and when it refuses

**Two GraphQL points and one REST request per repository**, checked from both
budgets **before anything is collected**:

```console
$ github-metrics scan huge-inventory.csv
Error: [GM-COL-004] 6000 repositories need 12000 GraphQL points but only 4983
remain (short by 7017). Wait for the hourly reset, or collect fewer
repositories per run
```

The check is the point. A run that discovers exhaustion halfway has already
spent what it had and produced a file that is part measurement and part
absence, with nothing to distinguish the two — the repositories at the end of
the inventory look exactly like ones that could not be read. Refusing costs one
request that does not count against the limit.

No reserve is held back, so the budgets run to zero and a full hourly quota
collects **at most** 2,500 repositories — GraphQL binds first, at two points
against REST's one request. Keeping points back would buy a convenience by
refusing a run the token could actually have finished.

**The pre-flight is a floor, not a guarantee.** Since every contributor is
collected, a repository's real cost rises with its contributor list and nothing
knows that count until the list is read. The check refuses a run that cannot
afford the minimum; it does not promise that a run which starts will finish.

**Geocoding, not the API, sets the pace of a large run.** Nominatim permits one
request per second, and the tool enforces it: exceeding the policy gets the
user agent blocked, which fails every later run rather than the one that
misbehaved. The cost is therefore the number of *distinct* locations rather
than of contributors — and, because resolved locations are cached to disk
between runs, the number of distinct locations **never seen before**. A first
run over a large inventory still takes hours; a second over the same inventory
does not. `GEOCODE_CACHE_PATH` moves or disables the cache file.

### Rows

One row per accepted reference, **in input order**. Results are collected
concurrently but assembled in the order asked for, so two runs of one inventory
produce byte-identical files and a diff shows changed data rather than
reordered rows.

A repository that could not be read still produces a row:

```csv
name,owner,organization,url,scan_date,scan_id,stars,...
virtualenv,pypa,pypa,https://github.com/pypa/virtualenv,2026-08-31 01:18:40+00:00,ae32d273-...,5041,...
definitely-not-real,ghost,,https://github.com/ghost/definitely-not-real,2026-08-31 01:18:40+00:00,ae32d273-...,,...
```

Identity from the input, measurements empty. **Empty, not zero** — zero is a
legitimate score for a repository that was measured and found wanting.

The failures are also named on stderr, because a file with empty rows says
something went wrong and only this says what:

```
! ghost/definitely-not-real: [GM-COL-001] Could not resolve to a Repository
! tiangolo/fastapi: [GM-COL-003] tiangolo/fastapi has been moved to fastapi/fastapi; update the inventory
```

A repository whose *contributors* could not be read is a lesser case
(`GM-COL-005`): the row keeps every measurement it collected — it is a
complete row, because no column of it is derived from contributors — and no
document is written. The absent file is the only record that the contributor
half failed, which is why the run also warns.

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
# Both artifacts into ./githubmetrics/
github-metrics scan inventory.csv

# Both artifacts into a directory of your choosing
github-metrics scan inventory.csv --output ./results/

# Mixed sources
github-metrics scan inventory.csv cline/cline github.com/psf/requests

# Print the table instead of writing it; documents are still written
github-metrics scan pypa/virtualenv --format console

# Only the columns a dashboard needs, in the CSV
github-metrics scan inventory.csv --fields owner,name,total_score

# The tabular artifact as JSON
github-metrics scan inventory.csv --format json
jq '.[].total_score' githubmetrics/githubmetrics.json

# One repository's contributors
jq '.contributors[].name' githubmetrics/pypa/virtualenv.json
```

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
| `4` | Degraded: a repository could not be read, or has moved | yes | `scan` |
| `5` | Aborted: API budget exhausted | partial | reserved |
| `6` | Aborted: the input could not be read | no | `validate` |
| `7` | Aborted: no GitHub token supplied | no | API commands |
| `8` | Aborted: GitHub rejected the token | no | API commands |

The 3-4 against 5-6 split is the load-bearing part: **3 and 4 still produced a
usable result; 5 and 6 did not.** A caller can test `$? -ge 3` for "something
was wrong" and `$? -ge 5` for "nothing usable came out".
