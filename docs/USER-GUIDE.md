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

## In a pipeline

Use `--strict` when any defect should stop the run before a later stage treats
a partial inventory as complete:

```bash
github-metrics validate inventory.csv --strict || exit 1
```

Strict mode stops at the *first* problem and returns nothing. When it fires,
re-run without `--strict` to see the full list.

## Using it as a library

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

## What ingestion does not tell you

A reference that passes validation is **plausible**, not confirmed. Ingestion
performs no network access, so it cannot know whether a repository exists, is
public, or was renamed. That is deliberate: it is what makes ingestion instant,
credential-free, and safe to run before spending any API quota.

Existence is established by the collection stage, and a well-formed reference
to a repository that no longer exists will surface there as a 404.

## Collecting metrics

Today, one repository at a time, and this needs `GITHUB_TOKEN`:

```bash
github-metrics repo python/cpython
github-metrics rate-limit
```

Feeding an ingested inventory into collection is the next milestone. See
[`ROADMAP.md`](ROADMAP.md).
