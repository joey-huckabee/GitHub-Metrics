# GitHub-Metrics — Roadmap

What is planned, and what is deferred. Items permanently out of scope are
**not** here — they are non-requirements in [`L1.md`](L1.md).

This file records intent, not commitment. An item listed here has been thought
about and deliberately scheduled; that is different from being promised.

## Where things stand

**Merged:**

- Inventory ingestion from `owner,repoid` CSV, with full validation, a stable
  error taxonomy, lenient and strict modes, and concurrent multi-file reads
- `github_metrics.sources`: slugs, GitHub URLs and CSV inventories, mixed
  freely, resolved in input order with repetitions removed across all of them.
  URL input was scheduled for v0.2.0 and arrived early, because it fell out of
  giving every command the same inputs
- `github-metrics validate`, the offline check — formerly `ingest`
- `github-metrics scan`, the release deliverable: collection over an
  inventory, concurrently, with a rate-limit pre-flight, into
  `githubmetrics.csv` **and** one JSON document per repository
- Contributor collection, folded into `scan` rather than a command of its own.
  `github-metrics contributors` is retired
- A three-level requirements tree traced to tests, checked in CI

**Merged, output half of v0.1.0:** the `SoftwareRow` column definition, the CSV,
JSON and console renderers, field selection, destination resolution and the run
identity. These do not depend on how a metric is calculated - only on which
columns exist - so they were built while the definitions were being settled.

**Settled:** every column in [`METRICS.md`](METRICS.md) now reads Settled, and
the collection layer that was blocked on those definitions is built. The rule
that got it there still holds — nothing is implemented before it is defined
there.

**Not being adjusted for this release:** `prevalence_score` saturates at 20.0
for any mature repository, and `trusted_org_bonus` is 0 for nearly every row
given a three-entry list. Two of the six components therefore do little to
separate a portfolio of mature projects, and the ranking is carried by stars,
forks, maturity and last update. This is a known and accepted property of
v0.1.0, not an oversight; the levers are band boundaries and the length of the
trusted list, and neither is being pulled now.

---

## v0.1.0 — `githubmetrics.csv`

**Released 2026-08-30.** Kept here as the record of what that release covered
and why; v0.2.0 changes several of these decisions, and says so where it does.

### The `metrics` sub-command, renamed `scan` in v0.2.0

**Delivered.** Replaced `repo`, which was scaffolding collecting a different
set of fields. Collects metrics for every repository named by the input and
writes `githubmetrics.csv`.

It shipped as `metrics` and is `scan` from v0.2.0, because the per-repository
JSON turned out to be the same row plus a contributor block, so one command
produces both under one scan identity. See
[ADR-0005](adr/0005-one-scan-command-and-per-repository-json.md).

- **Input:** any number of sources, mixed — slugs, GitHub URLs and CSV
  inventories, exactly as `validate` takes them. Directories are not walked.
  The earlier plan restricted this to one CSV file, or `--owner` with
  `--repoid`; both were dropped in favour of one input vocabulary shared by
  every command, since the alternative makes the user remember which command
  wants which form.
- **Output:** one row per accepted input row, **in input order**.
- **Destination:** a file named by `--output`. When only a directory is given,
  the file is written there as `githubmetrics.csv`. With no `--output` at all,
  the report goes to the console.
- **Console format:** a **vertical** table, one block per input row. Twenty
  columns do not fit across a terminal, so the columns become rows.
- **Field selection:** the caller selects which columns to emit; selecting none
  emits all. Columns come out in canonical order whatever order they are asked
  for, so two runs wanting the same columns produce identical headers.

  This was specified as a rate-limit lever as well — selection would skip the
  API calls no selected column needed. It is a rendering filter only, and that
  is now the right answer rather than a shortfall: every column a row needs
  comes from **one** GraphQL query costing **one** point, so there is no call
  left to skip and no quota to save. The lever was designed when collection was
  assumed to be several REST calls per repository. Making selection cheaper
  than one point is not possible, so the promise is withdrawn rather than
  carried forward.
- **JSON:** an array of objects using the CSV column names, available both as a
  file and to the console.
- **Checking first:** `validate` is a command of its own rather than a
  `--dry-run` flag. Checking a 400-row inventory before spending any quota is
  worth keeping, and a separate command is the form that can be handed to
  someone who has no token at all.
- **Duplicates:** dropped, with a warning naming them.
- **Invalid rows:** rejected, as today.
- **Unfetchable repositories:** identity columns filled, metrics and scores
  empty, non-zero exit. See [`METRICS.md`](METRICS.md).
- **Exit codes:** severity-ordered 0–6, per
  [`adr/0004-exit-code-scheme.md`](adr/0004-exit-code-scheme.md).

### Package layout

The current flat module set grows into packages, shaped by where things are
headed rather than by what exists today:

```
github_metrics/
  sources/     where a repository gets named - slugs, URLs, CSV inventories
  model/       ScanIdentifier, RepoMetaData, the output row
  collect/     API access, rate limiting, concurrency
  analysis/    the scoring calculations
  output/      CSV, JSON and console rendering
```

`sources/` and `output/` are separate rather than one `csv_io` package because
they diverge immediately: `sources/` already reads URLs as well as CSV, and
v0.3.0 adds a database *destination*. Nothing named `csv_io` would still be
honest, and it stopped being honest sooner than expected.

### Rate limiting

Integrated with the per-repository concurrency, not bolted beside it.

**Delivered.**

- **On exhaustion: fail the run.** Deliberately blunt for this release.
  Configurable behaviour is v0.2.0.
- **Pre-flight budgeting is on by default.** One point per repository against
  the quota remaining, refusing a run that cannot finish. It is a comparison
  rather than an estimate, because the per-repository cost is exactly one.
  Making it optional is v0.2.0.
- **No reserve buffer.** The budget runs to zero, so a full hourly quota
  collects exactly 5,000 repositories.
- **Fixed concurrency**, eight workers. Quota-adaptive counts are v0.2.0.

### Library use

The package must be usable as a dependency, so that a larger project can
collect metrics in the background rather than shelling out to the CLI.

No separate facade or wrapper API: a caller imports the classes and functions
directly and instantiates them. The CLI becomes one caller among others rather
than the only way in, which means every capability the CLI has must be reachable
without it — including output selection, rate-limit configuration and the
scan identity.

Collection is synchronous. A caller wanting it in the background runs it in
their own thread or task; the package does not impose a concurrency model on
the program embedding it.

### Documentation

- [`METRICS.md`](METRICS.md) — field definitions and scoring bands
- [`USER-GUIDE.md`](USER-GUIDE.md) — worked examples of **every** sub-command
  and parameter
- Library usage documented alongside the CLI

---

## v0.2.0 — contributor collection, and rate-limit tuning

Two bodies of work. The contributor dataset is the one being built now; the
rate-limit knobs were already scheduled here and keep their place.

### Contributor collection

**Built.** The shape is [`example.json`](example.json): the twenty
`SoftwareRow` columns, then the contributor block. One JSON document per
repository, at `githubmetrics/<owner>/<repoid>.json` unless `--output` says
otherwise, lower-cased throughout, and written only for a repository that was
fully collected. See
[ADR-0005](adr/0005-one-scan-command-and-per-repository-json.md) for why each
of those is what it is.

**Merged:**

- `repo_name` renamed `name`, and `url` added as a column after `name`, `owner`
  and `organization`. The CSV and the documents share one set of key names, so
  the two artifacts join without a translation table
- `metrics` renamed `scan`, so one run is one scan and the command shares a
  word with the `scan_id` and `scan_date` it stamps
- `contributors` removed, and folded into `scan`. **No flag governs which
  artifacts a run writes** - it writes both, always. The flagged design was
  considered and rejected: it buys one state nothing else expresses, and in
  exchange what a run produced stops being readable from the command
- The five contribution aggregates added to the document, not to the CSV,
  which stays at twenty columns. Promoting them was considered and rejected:
  they exist only for a repository whose contributor list was read, which is
  exactly the set that produces a document, so as columns they would be empty
  for precisely the rows with no document to explain the gap
- Contributor collection itself: the ranked list from REST, every account's
  detail from one aliased GraphQL document, and locations resolved to a
  fourteen-field address through Nominatim
- `check_budget` extended to both currencies. A scan costs 2 GraphQL points and
  1 REST request per repository, so GraphQL binds first at 2,500 repositories
  an hour
- `--geocode` and `--contributors N` removed. Geocoding is unconditional; the
  limit is `DEFAULT_CONTRIBUTOR_LIMIT`

**Blocked on definitions**, per the rule that nothing is implemented before
`METRICS.md` defines it:

- `foreign` — foreign to the United States. The rule that applies it is coming
  from Joey as code
- `adversarial` — no agreed rule yet. Also coming as code

Neither has a definition anywhere in the repository today, and both attach a
judgement to a named person, so neither is computed. Both are **emitted as
`null`**, along with the four aggregates that depend on them
(`foreign_contribution`, `adversarial_contribution`, `foreign_percent`,
`adversarial_percent`), so that the shape does not change when the definitions
land. Reserving a column is not implementing the metric: a `null` publishes no
number and makes no claim.

**Open, and worth revisiting once there is a real run to look at:**

- **The cost of the default.** Every scan now pays for contributor pages and
  geocodes. That was the thing the v0.1.0 command split existed to avoid, and
  it is accepted here as the price of one identity per run. If it hurts, the
  lever is `DEFAULT_CONTRIBUTOR_LIMIT`, not a flag
- **Geocoding throughput.** Nominatim permits one request per second, so a
  first run over a large inventory is measured in hours. The per-run cache
  makes the cost the number of distinct locations rather than of contributors,
  but a cache that survived between runs would make re-runs nearly free. That
  belongs with v0.3.0's store
- **The contributor-detail query cost is calculated, not measured.** The
  metrics query's one point was confirmed against the live API; the aliased
  detail query's has not been. The repository's own convention is that a cost
  is measured rather than assumed
- **The probe commands.** `closed-issues` and `releases` exist to check a
  metric definition against real repositories before wiring it in, and every
  metric now reads Settled. Whether they are retired is still open

### Delivered early: GitHub URL input

This was the headline of v0.2.0 and shipped in v0.1.0 instead. It was scheduled
here on the assumption that URL handling was a feature of its own; it turned
out to be a consequence of giving every command the same inputs, which is a
smaller change than a separate URL mode would have been.

What was planned is what was built: a deep link into a file or a line range
still resolves, and a URL that cannot be read as a repository carries its own
code — two of them, `GM-ING-016` for the shape and `GM-ING-017` for a host that
is not GitHub.

The reasoning in
[ADR-0001](adr/0001-two-column-csv-as-the-inventory-contract.md) held up: URL
parsing is kept out of the CSV contract, in one clearly-marked place with its
own codes, rather than spread across every row of an inventory.

Naming several repositories on the command line came with it, since a source
list is a list.

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

The geocoding path already resolves contributor locations to
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
