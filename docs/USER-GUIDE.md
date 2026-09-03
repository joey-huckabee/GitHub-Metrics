# GitHub-Metrics — User Guide

A task-oriented introduction. For the exhaustive option list see
[`CLI-REFERENCE.md`](CLI-REFERENCE.md).

## What this tool is for

You have a set of open source repositories you care about — a dependency
inventory, a portfolio, a research sample — and you want comparable numbers
about them.

The work splits in two, and so does the tool:

1. **Ingestion** turns a list of repositories into validated references. It is
   offline, fast, and needs no credentials.
2. **Collection** turns those references into metrics by calling the GitHub
   API. It needs a token and it spends rate limit.

This guide covers ingestion in full, because that is what ships today.
Collection currently works one repository at a time; see
[`ROADMAP.md`](ROADMAP.md).

## Install

```bash
poetry install
```

For ingestion, that is all you need. For the API commands, add a token:

```bash
cp .env.example .env
# then edit .env and set GITHUB_TOKEN
```

If a `.env` does not suit - a CI job, or a container with the secret mounted at
a path - use `--token-file`:

```bash
github-metrics --token-file /run/secrets/github rate-limit
```

There is also a `--token` flag, but prefer either of the other two where you
can. A value on the command line is visible to every other process on the
machine and is written to your shell history; a *path* is not a secret, so
`--token-file` gives up nothing.

Whichever you use, the tool checks the token before doing any work. The check
costs no rate limit, and it turns an authentication failure that would
otherwise appear halfway through a long run into one message up front:

```console
$ github-metrics rate-limit
Error: [GM-CFG-002] GitHub rejected the token (401). It may be expired, revoked,
or mistyped. Kind detected from its prefix: classic personal access token.
```

The token's value is never logged, at any level.

## Your first inventory

Create `inventory.csv`:

```csv
owner,repoid
urllib3,urllib3
bokeh,bokeh
pypa,virtualenv
```

Each row names the repository at `https://github.com/<owner>/<repoid>`. Read
it:

```console
$ github-metrics validate inventory.csv
urllib3/urllib3
bokeh/bokeh
pypa/virtualenv
3 repositories, 0 rejected, from 1 file(s)
```

### You do not have to use a file

A repository can be named directly, and the forms can be mixed:

```console
$ github-metrics validate pypa/virtualenv
$ github-metrics validate https://github.com/pypa/virtualenv
$ github-metrics validate inventory.csv cline/cline github.com/psf/requests
```

URLs are taken as they come — with or without a scheme, with `www.`, with a
trailing slash, with `.git`, and with whatever path was left over from browsing
the issues. The one thing that is not accepted is another host:
`gitlab.com/foo/bar` would reduce perfectly well to `foo/bar`, which is exactly
why it is refused rather than guessed at. On GitHub Enterprise, use the slug
and point `GITHUB_API_URL` at your instance.

**A repository named twice is collected once**, whether the repetition is
within a file, across two files, or between a file and the command line. The
first mention keeps its place and the second is reported. Collecting it twice
would spend the rate limit twice and put two identical rows in the output.

That is the whole contract. Two columns, one row per repository.

### Where the two columns come from

If you have URLs rather than columns, split them once:

| URL | `owner` | `repoid` |
|---|---|---|
| `https://github.com/urllib3/urllib3` | `urllib3` | `urllib3` |
| `https://github.com/pypa/virtualenv` | `pypa` | `virtualenv` |

The tool deliberately does not accept a URL column. The reasoning is in
[`adr/0001-two-column-csv-as-the-inventory-contract.md`](adr/0001-two-column-csv-as-the-inventory-contract.md),
but the short version is that `git@`, trailing `.git`, deep links and
enterprise hosts all make URL parsing ambiguous, and you can split them
reliably where we would have to guess.

## What your file may look like

Real inventories are messy. Most of that is fine.

**Extra columns are ignored**, so you can keep your own bookkeeping in the same
file rather than maintaining a stripped copy:

```csv
owner,repoid,steward,last_reviewed
pypa,virtualenv,alex,2026-01-04
bokeh,bokeh,sam,2026-02-11
```

**Header formatting does not matter.** Any casing, any order, surrounding
whitespace — ` RepoID , Owner ` is read the same as `owner,repoid`.

**Also accepted without complaint:** a UTF-8 byte-order mark (what Excel's
"Save as CSV UTF-8" writes), CRLF or LF line endings, blank lines, a trailing
`,,` row, and whitespace around values.

## When something is wrong

Rejected rows do not stop the read. You get every problem at once:

```bash
$ github-metrics validate messy.csv
messy.csv: 1 repositories, 5 rejected
  bokeh/bokeh
  ! messy.csv:3: [GM-ING-010] expected at least 2 fields but found 1: 'onlyonefield'
  ! messy.csv:4: [GM-ING-011] owner is empty in ',virtualenv'
  ! messy.csv:5: [GM-ING-012] repoid is empty in 'pypa,'
  ! messy.csv:6: [GM-ING-013] invalid owner 'https://github.com/pypa': may only contain letters, digits and hyphens
  ! messy.csv:7: [GM-ING-014] invalid repoid 'virtualenv.git': may not end in '.git'
$ echo $?
3
```

Each line gives you the file, the line number, a stable code and the offending
value — enough to fix the row without opening the file to work out what
happened. Every code is explained in
[`ERROR-CATALOG.md`](ERROR-CATALOG.md).

Exit status `3` means "loaded, but degraded". `0` means clean; `6` means the
file could not be read at all.

### The two mistakes almost everyone makes

**A pasted URL in the owner column** (`GM-ING-013`). The owner is only the
account segment: for `https://github.com/pypa/virtualenv`, that is `pypa`.

**A `.git` suffix on the repository name** (`GM-ING-014`). This is what stripping
the host off a clone URL leaves behind. GitHub rejects such a name, so we
reject it here where the message can explain it, rather than later as an
unexplained 404. Only the suffix is a problem — a repository genuinely named
`gitignore` is fine.

## Duplicates

A repeated repository is kept once and reported:

```
inventory.csv:5: [GM-ING-015] pypa/virtualenv already appears on line 3; keeping the first occurrence
```

Matching is case-insensitive, because GitHub is: `PyPA/virtualenv` and
`pypa/virtualenv` are the same repository. Nothing is lost — it is still
analysed, once. It is reported because a duplicate usually tells you something
about how the list was assembled, and because counting it twice would inflate
every aggregate you go on to compute.

## Several files at once

Pass as many as you like. They are read concurrently:

```bash
$ github-metrics validate teams/*.csv
teams/data.csv: 12 repositories, 0 rejected
  ...
teams/platform.csv: 31 repositories, 1 rejected
  ...
total: 43 repositories from 2 files
```

Results always come back in the order you listed the files, never in the order
the reads finished, so the output is diffable between runs. `--workers N` tunes
the thread count; it cannot change the answer.

## Handing the inventory to something else

```bash
github-metrics validate inventory.csv --format json --output inventory.json
```

Diagnostics go to stderr and data to stdout, so piping works even with logging
turned all the way up:

```bash
LOG_LEVEL=DEBUG github-metrics validate inventory.csv --format json | jq '.repositories[].owner'
```

Each reference carries `source_line`, the physical line it came from, so a
failure three stages later can still be traced back to the row that asked for
it.

## Checking a single metric

Metrics are defined one at a time, and each gets a command of its own so a
definition can be checked against real repositories before it is wired into a
full run.

```console
$ github-metrics closed-issues pypa/virtualenv
pypa/virtualenv
  closed issues      1429
  open issues           0
  tracker         enabled
  weight              1.0
```

The weight is a 0.0-1.0 multiplier, not a final score. To see where it came
from, ask:

```console
$ github-metrics closed-issues pypa/virtualenv --explain
...
closed-issue bands:
  <20    -> 0.1
  <50    -> 0.2
  <100   -> 0.3
  <150   -> 0.4
  <300   -> 0.6
  <400   -> 0.8
  <500   -> 0.9
  >=500  -> 1.0
```

The bands are deliberately uneven. The informative range is at the low end: the
difference between 10 and 100 closed issues says a great deal about whether a
project is maintained, while the difference between 3,000 and 4,000 says almost
nothing.

### Two things worth knowing about this number

**Pull requests are excluded.** GitHub's REST API models pull requests as
issues, so the obvious route counts both. For `cline/cline` that is 3,770
closed issues against 7,001 closed pull requests - a combined figure nearly
triples the number and measures development throughput rather than issue
triage. This tool asks GraphQL, where the two are separate.

**Zero has two meanings.** A repository with its issue tracker turned off
reports zero closed issues, but that describes its configuration, not its
maintenance - the project may track work on a mailing list or another forge.
The command says so rather than leaving you to guess:

```console
  tracker        DISABLED
  note: the issue tracker is off, so zero is a configuration fact rather than a maintenance one
```

For JSON, add `--format json`. Diagnostics go to stderr as always, so the
output pipes cleanly:

```console
$ github-metrics closed-issues cline/cline --format json | jq .closed_issues
3770
```

Exit status is 0 when the repository was read and **4** when it could not be -
deleted, renamed, or private. Syntactic validation at ingestion cannot detect
any of those, so this is where a stale inventory entry finally surfaces.

### Release cadence

`releases` is the same shape of probe for how often a project ships:

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

Three things to read out of that.

**The scored number is 285, not 383.** Publishing a GitHub Release creates a
tag, so every release is already among the tags and adding the two counts it
twice. The overstatement is not a constant either - it grows with how
consistently a project uses the Releases feature, so summing would have
rewarded a project's tooling rather than its release cadence.

**187 tags have no release.** That is normal and is not a defect. Plenty of
projects tag every version and publish release notes for only the notable ones,
and a tag is still a shipped version.

**The weight has saturated.** Anything at or above 80 distinct versions scores
1.0, so this metric stops separating projects above that line. If your
inventory is mostly mature software, expect this column to be 1.0 nearly
everywhere and do your ranking elsewhere.

A repository that has never tagged anything scores 0.0 - a project with nothing
at all scores nothing, rather than collecting the lowest non-zero band for
existing.

### Seeing the whole scoring model

Every band table can be printed without a token and without touching the
network:

```console
$ github-metrics bands              # all five
$ github-metrics bands maturity     # just one
maturity bands (on age in days):
  <0.25 years (   91.2 days) -> 0.0
  <0.5  years (  182.5 days) -> 0.2
  <1.0  years (  365.0 days) -> 0.4
  <2.0  years (  730.0 days) -> 0.6
  <3.0  years ( 1095.0 days) -> 0.8
  <4.0  years ( 1460.0 days) -> 0.9
  >=4.0 years ( 1460.0 days) -> 1.0
```

These are rendered from the same tables the scoring code reads, so they cannot
drift from it. Reviewing the model here before starting a collection run is
cheaper than discovering a disagreement about a boundary once the results are
in a spreadsheet.

## Collecting metrics

This is what the tool is for.

```console
$ github-metrics scan inventory.csv --output githubmetrics.csv
Wrote 3 rows to githubmetrics.csv
```

The sources are the same ones `validate` takes, so a list you have checked is a
list you can collect:

```bash
github-metrics scan inventory.csv cline/cline github.com/psf/requests
```

With no `--output`, the rows go to the console as vertical blocks — twenty
columns do not fit across a terminal, and a truncated metric is worse than no
metric:

```console
$ github-metrics scan pypa/virtualenv
name               virtualenv
owner              pypa
organization       pypa
url                https://github.com/pypa/virtualenv
scan_date          2026-08-31 01:18:25.560138+00:00
scan_id            0beab6d7-0b71-45e6-8826-841e05d0a3bd
stars              5041
forks              1114
age_days           5656.447900001597
last_update_hours  13.896266705
closed_issues      1429
releases           285
prevalence_score   20.0
stars_score        10.0
forks_score        15.0
maturity_score     15.0
last_update_score  15.0
trusted_org_bonus  0.0
total_score        75.0
is_trusted_org     false
```

### It checks the budget before it starts

Collection costs one GraphQL point per repository, so a 400-row inventory
spends 400 of the 5,000 available each hour. The run confirms that before
collecting anything:

```console
$ github-metrics scan huge-inventory.csv
Error: [GM-COL-004] 6000 repositories need 6000 GraphQL points but only 4983
remain. Short by 1017. Wait for the hourly reset, or collect fewer repositories
per run
```

Nothing was collected and nothing was spent. That is better than the
alternative: a run that runs out halfway has already spent its quota and left
you a file where the repositories at the end are indistinguishable from
repositories that could not be read.

### A repository that fails still gets a row

The file has one row per accepted reference, always, so a failure cannot
silently change what the file means:

```csv
definitely-not-real,ghost,,2026-08-31 01:18:40+00:00,ae32d273-...,,,,,,,,,,,,,,
```

Identity from your list, measurements empty. **Empty rather than zero**,
because zero is a real score for a project that was measured and found wanting
— scoring an unreadable repository as zero would drag every average you
compute.

The run names them on stderr and exits **4**:

```
! ghost/definitely-not-real: [GM-COL-001] Could not resolve to a Repository
! tiangolo/fastapi: [GM-COL-003] tiangolo/fastapi has been moved to fastapi/fastapi; update the inventory
```

That second one is worth knowing about. GitHub redirects a renamed or
transferred repository, so the entry still works — which is exactly why it is
refused. Collecting it would give you a row of correct numbers about a
repository your inventory does not name, and nothing in the file would say so.

### Only the columns you want

```bash
github-metrics scan inventory.csv --fields owner,name,total_score
```

Columns come out in canonical order whatever order you ask for them, so two
runs wanting the same columns produce identical headers.

### Contributors come with every scan

There is no separate command and no flag. `scan` writes the CSV and one JSON
document per repository, and the document is that repository's row followed by
its contributors:

```bash
github-metrics scan inventory.csv
jq '.contributors[].name' githubmetrics/pypa/virtualenv.json
```

They come together because both artifacts carry `scan_id` and `scan_date`, and
those are assigned once per run. Two commands would produce two identities, and
a CSV and a folder of documents collected minutes apart could not be joined or
grouped by the run that measured them — which is the only reason those columns
exist.

The price is that every run now pays for contributor pages and for geocoding.
Nominatim permits one request per second, so a first run over a few hundred
repositories takes hours; locations are cached within a run, and they repeat
heavily, so the cost is the number of *distinct* locations rather than of
contributors.

## In a pipeline

Use `--strict` when any defect should stop the run before a later stage treats
a partial inventory as complete:

```bash
github-metrics validate inventory.csv --strict || exit 1
```

Strict mode stops at the *first* problem and returns nothing. When it fires,
re-run without `--strict` to see the full list.

## Using it as a library

The package is usable as a dependency, so a larger project can collect metrics
without shelling out to the CLI. There is no facade: you import the same
functions the CLI imports.

### Reading a list

```python
from github_metrics.sources import read_repository_csv

result = read_repository_csv("inventory.csv")

for reference in result.repositories:
    print(reference.full_name, reference.url)

for issue in result.issues:
    print(issue)          # path:line: [CODE] message

print(f"{result.accepted} of {result.rows_read} rows accepted")
```

Reading several files concurrently:

```python
from github_metrics.sources import read_repository_csvs

for result in read_repository_csvs(["teams/data.csv", "teams/platform.csv"]):
    print(result.source, result.accepted, result.rejected)
```

Or take whatever the command line takes — slugs, URLs and files together,
resolved in the order given, with repetitions removed across all of them:

```python
from github_metrics.sources import resolve_sources

resolved = resolve_sources(
    ["inventory.csv", "pypa/virtualenv", "https://github.com/psf/requests"]
)

for reference in resolved.repositories:
    print(reference.full_name)

print(f"{resolved.accepted} accepted, {resolved.rejected} rejected")
```

To read a single reference without touching the file system, `parse_reference`
returns either a `RepositoryRef` or a `RowIssue` saying why not:

```python
from github_metrics.sources import parse_reference

print(parse_reference("github.com/pypa/virtualenv/tree/main"))
# RepositoryRef(owner='pypa', repoid='virtualenv', source_line=None)
```

Catching failures — one base class covers every problem the tool reports, while
leaving genuine bugs free to propagate:

```python
from github_metrics.errors import IngestError

try:
    result = read_repository_csv("inventory.csv")
except IngestError as exc:
    print(f"could not read the inventory: {exc}")
```

### Collecting, as a library

The CLI is one caller among others. Every capability it has is reachable
without it, including the scan identity, the budget check, the worker count and
the column selection.

```python
import io

from github_metrics.analysis.row import build_empty_row, build_row
from github_metrics.client import GitHubClient
from github_metrics.collect.budget import check_budget
from github_metrics.collect.runner import collect_all
from github_metrics.config import Settings
from github_metrics.model.scan import ScanIdentifier
from github_metrics.output import write_csv
from github_metrics.sources import resolve_sources

resolved = resolve_sources(
    ["pypa/virtualenv", "https://github.com/urllib3/urllib3", "ghost/nope"]
)

scan = ScanIdentifier()          # one identity for the whole run
settings = Settings.from_env()   # GITHUB_TOKEN, GITHUB_API_URL

with GitHubClient(settings) as client:
    check_budget(client, len(resolved.repositories))     # refuses if it will not fit
    outcomes = collect_all(client, resolved.repositories, max_workers=4)

rows = [
    build_row(o.reference, o.metadata, scan) if o.metadata else build_empty_row(o.reference, scan)
    for o in outcomes
]

buffer = io.StringIO()
write_csv(rows, buffer, columns=["owner", "name", "stars", "total_score"])
print(buffer.getvalue())

failed = [o.reference.full_name for o in outcomes if not o.ok]
```

```csv
name,owner,stars,total_score
virtualenv,pypa,5041,75.0
urllib3,urllib3,4054,75.0
nope,ghost,,
```

Four things that are the same as on the command line, because they are the same
code:

- **Columns come out in canonical order**, not the order you asked for. The
  example asked for `owner` first and got `name` first.
- **A repository that could not be read still produces a row**, carrying its
  identity with the measurements empty. `outcomes` tells you which, and why:
  `o.error` is the exception rather than a string.
- **`check_budget` refuses before anything is spent.** Call it or do not, but
  the CLI's guarantee comes from this call and nothing else.
- **`ScanIdentifier()` is created once.** Creating one per repository would
  stamp each row with a different run and make the result set ungroupable.

Collection is synchronous. To run it in the background, put it in your own
thread or task — the package does not impose a concurrency model on the
program embedding it.

## What ingestion does not tell you

A reference that passes validation is **plausible**, not confirmed. Ingestion
performs no network access, so it cannot know whether a repository exists, is
public, or was renamed. That is deliberate: it is what makes ingestion instant,
credential-free, and safe to run before spending any API quota.

Existence is established by the collection stage: a well-formed reference to a
repository that was deleted, renamed or made private surfaces there, and
`scan` exits 4 saying which ones.

## Every command and flag

One line each, for when you know what you want and need the spelling. Full
detail is in [`CLI-REFERENCE.md`](CLI-REFERENCE.md).

### Global

| Flag | Example |
|---|---|
| `--env-file` | `github-metrics --env-file ci.env metrics inventory.csv` |
| `--token` | `github-metrics --token ghp_xxx rate-limit` |
| `--token-file` | `github-metrics --token-file /run/secrets/github rate-limit` |
| `--no-verify-token` | `github-metrics --no-verify-token metrics inventory.csv` |
| `-V`, `--version` | `github-metrics -V` |
| `-h`, `--help` | `github-metrics scan --help` |

`LOG_LEVEL=DEBUG` on any of them turns on per-repository detail. Diagnostics go
to stderr, so a pipe stays clean.

### `validate` — check a list, no token needed

| Flag | Example |
|---|---|
| *(none)* | `github-metrics validate inventory.csv` |
| *(sources mix)* | `github-metrics validate inventory.csv cline/cline github.com/psf/requests` |
| `--strict` | `github-metrics validate inventory.csv --strict` |
| `--workers` | `github-metrics validate teams/*.csv --workers 1` |
| `--format` | `github-metrics validate inventory.csv --format json` |
| `--output` | `github-metrics validate inventory.csv --format json --output report.json` |

### `scan` — the deliverable

| Flag | Example |
|---|---|
| *(none)* | `github-metrics scan pypa/virtualenv` |
| `--output` | `github-metrics scan inventory.csv --output ./results/` |
| `--format` | `github-metrics scan inventory.csv --format json` |
| `--format console` | `github-metrics scan pypa/virtualenv --format console` |
| `--fields` | `github-metrics scan inventory.csv --fields owner,name,total_score` |
| `--workers` | `github-metrics scan inventory.csv --workers 4` |
| `--strict` | `github-metrics scan inventory.csv --strict` |

### Probes and diagnostics

| Command | Example |
|---|---|
| `closed-issues` | `github-metrics closed-issues pypa/virtualenv --explain` |
| `closed-issues` | `github-metrics closed-issues cline/cline --format json` |
| `releases` | `github-metrics releases pypa/virtualenv --explain` |
| `releases` | `github-metrics releases cline/cline --format json` |
| `bands` | `github-metrics bands` |
| `bands METRIC` | `github-metrics bands maturity` |
| `rate-limit` | `github-metrics rate-limit` |

The probes cost one GraphQL point each. `bands` costs nothing and needs no
token.

## Exit codes at a glance

| Code | Meaning | Was a file written? |
|---|---|---|
| `0` | Success | yes |
| `1` | Configuration error | no |
| `2` | Usage error | no |
| `3` | Some input references were rejected | yes |
| `4` | A repository could not be collected, or has moved | yes |
| `5` | The budget could not cover the run | no |
| `6` | A source could not be read | no |
| `7` | No token supplied | no |
| `8` | GitHub rejected the token | no |

Test `$? -ge 3` for "something was wrong" and `$? -ge 5` for "nothing usable
came out".
