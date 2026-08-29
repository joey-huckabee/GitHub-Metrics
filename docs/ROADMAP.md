# GitHub-Metrics — Roadmap

What is planned, and what is deferred. Items permanently out of scope are
**not** here — they are non-requirements in [`L1.md`](L1.md).

This file records intent, not commitment. An item listed here has been thought
about and deliberately scheduled; that is different from being promised.

## Where things stand

**Merged:**

- Inventory ingestion from `owner,repoid` CSV, with full validation, a stable
  error taxonomy, lenient and strict modes, and concurrent multi-file reads
- Single-repository collection via `github-metrics repo`
- A three-level requirements tree traced to tests, checked in CI

**In progress — see [`METRICS.md`](METRICS.md):** the metric definitions and
scoring bands that produce `githubmetrics.csv`. Nothing is implemented until it
is defined there.

---

## v0.1.0 — `githubmetrics.csv`

The current release target. Everything below is in scope now.

### The `metrics` sub-command

Replaces `ingest`. Collects metrics for every repository named by the input and
writes `githubmetrics.csv`.

- **Input:** exactly one CSV file (multiple files are not accepted), or the
  `--owner` / `--repoid` parameters for a single repository. Directories are
  not walked.
- **Output:** one row per accepted input row, **in input order**.
- **Destination:** a file named by `--output`. When only a directory is given,
  the file is written there as `githubmetrics.csv`. With no `--output` at all,
  the report goes to the console.
- **Console format:** a formatted table, one per input row.
- **Field selection:** the caller may select which fields to emit. Selecting
  none returns all of them.
- **JSON:** available both as a file and to the console.
- **Duplicates:** dropped, with a warning naming them.
- **Invalid rows:** rejected, as today.
- **Unfetchable repositories:** a 404 or a private repository still has to be
  accounted for — see the open question in [`METRICS.md`](METRICS.md).

### Rate limiting

Integrated with the per-repository concurrency, not bolted beside it.

- **On exhaustion: fail the run.** Deliberately blunt for this release.
  Configurable behaviour is v0.2.0.
- **Pre-flight budgeting is on by default**: estimate the requests a run needs
  against the quota remaining, and refuse to start a run that cannot finish.
  Making it optional is v0.2.0.
- **No reserve buffer.** The budget runs to zero.
- **Fixed concurrency.** Quota-adaptive worker counts are v0.2.0.

### Library use

The package must be usable as a dependency, so that a larger project can
collect metrics in the background rather than shelling out to the CLI. The CLI
becomes one caller of the library rather than the only way in.

### Documentation

- [`METRICS.md`](METRICS.md) — field definitions and scoring bands
- [`USER-GUIDE.md`](USER-GUIDE.md) — worked examples of **every** sub-command
  and parameter
- Library usage documented alongside the CLI

---

## v0.2.0 — URLs and rate-limit tuning

### GitHub URL input

The CLI accepts a GitHub URL in place of `--owner` / `--repoid`. Anything after
the parts needed to identify the repository is ignored, so a deep link into a
file or a line range still resolves. A URL that cannot be parsed into an
owner and repository returns a distinct error code rather than a generic
failure.

This is why URL parsing was kept out of the CSV contract in
[ADR-0001](adr/0001-two-column-csv-as-the-inventory-contract.md): the ambiguity
is real, and it belongs in one clearly-marked place with its own error code
rather than spread across every row of an inventory.

### Rate-limit behaviour made configurable

Each of these is deferred from v0.1.0, where the behaviour is fixed:

- **Exhaustion policy** — currently always fail. Add: wait until reset, or emit
  partial results for what was collected.
- **Pre-flight budgeting** — currently always on. Make it possible to turn off.
- **Secondary rate limits.** GitHub returns 403 with `Retry-After` for burst
  and abuse detection, which is a different mechanism from the primary hourly
  quota. Needs its own backoff with a retry cap.
- **Worker count.** Currently fixed. Open question is whether workers should
  throttle down as remaining quota falls, or hold a fixed concurrency behind a
  shared limiter.
- **Conditional requests.** `ETag` / `If-None-Match` returning 304 does not
  count against the rate limit, which would make re-runs over a stable
  inventory nearly free. Worth having once there is something to re-run.

---

## v0.3.0 — Persistence

Capture results in **SQLite**, behind an interface that allows the store to be
swapped for **PostgreSQL** later without changing the collection code.

The schema has to be designed before it is written, not after. `scan_id` and
`scan_date` already exist as per-run identifiers precisely so that stored rows
can be grouped by the run that produced them, and `tool_version` already rides
on every snapshot so an archived row stays interpretable when the metric
definitions change.

Points to settle:

- Whether history is append-only (every scan retained) or last-value-wins
- Whether the CSV output remains primary, becomes an export of the database, or
  both
- How a metric definition change is recorded, so old and new rows are not
  silently compared

---

## v0.4.0 — Contributor metadata

Collect the contributor information the analysis needs. The metric set and its
definitions get the same treatment as [`METRICS.md`](METRICS.md): defined
first, implemented second.

Contributors are substantially more expensive to collect than repository
metadata — the request cost scales with the number of contributors rather than
being constant per repository — so the rate-limit work from v0.1.0 and v0.2.0
is a prerequisite rather than a nicety.

The existing `--geocode` path already resolves contributor locations to
coordinates and is the obvious seam to build on.

---

## v0.5.0 — Contributor persistence

A store for contributor metadata, following whatever pattern v0.3.0 settles on.

---

## Cross-cutting

**Library usage documentation.** Using GitHub-Metrics as a dependency rather
than a CLI needs worked examples, not just an API listing. Begins in v0.1.0 and
grows with each release.

---

## Later, unversioned

### Output formats for analysis

Parquet, and CSV shapes other than `githubmetrics.csv`, would let results go
straight into pandas without a conversion step. Deferred until the metric set
stabilises — a schema that changes every release is worse than no schema.

### More metrics

Candidates, each of which needs a definition in [`METRICS.md`](METRICS.md)
before it needs an implementation:

- Release cadence and time since last release
- Issue and pull request response latency
- Bus factor / contributor concentration
- Dependency counts from the dependency graph API
- Security policy and advisory presence

The hard part is definition, not collection. "Time to first response" requires
deciding what counts as a response and whose response counts, and a number
without a documented definition is not comparable across repositories.

### GitHub Enterprise validation

`GITHUB_API_URL` already points collection at an enterprise instance, but the
name grammar in `validation.py` encodes github.com's rules. An enterprise
instance may differ. Not addressed until someone needs it, because guessing at
another deployment's constraints would be inventing requirements.

### Non-GitHub forges

GitLab, Codeberg and sourcehut host FOSS too. This would be a substantial
change: `owner,repoid` is a GitHub-shaped identifier, and the inventory
contract would need a forge column. Listed to acknowledge the question, not
because it is planned.

---

## Deliberately not scheduled

Recorded so that "why hasn't this been done" has an answer.

**A configuration file.** Everything configurable today is a handful of
environment variables and command-line flags. A config file would add a
precedence order to reason about for no current benefit. This may change at
v0.2.0, when the rate-limit knobs arrive.

**A plugin system for metrics.** Premature while the metric set is still small
enough to read in one sitting.

**A web UI or dashboard.** This is a CLI and a library. Presentation belongs to
whatever consumes its output.

**Async I/O.** Threads are sufficient for the current workload and are far
easier to reason about. If collection at scale shows threads to be the
bottleneck, that measurement — not the fashion — is what would justify
revisiting it.
