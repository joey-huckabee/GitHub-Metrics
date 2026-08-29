# GitHub-Metrics — Roadmap

What is deferred, and why. Items permanently out of scope are **not** here —
they are non-requirements in [`L1.md`](L1.md).

This file records intent, not commitment. An item listed here has been thought
about and deliberately postponed; that is different from being promised.

## Where things stand

**Shipping:**

- Inventory ingestion from `owner,repoid` CSV, with full validation, a stable
  error taxonomy, lenient and strict modes, and concurrent multi-file reads
- The `github-metrics ingest` command, offline and credential-free
- Single-repository collection via `github-metrics repo`
- A three-level requirements tree traced to tests, checked in CI

**The obvious gap:** ingestion and collection do not yet meet. You can validate
an inventory of four hundred repositories, and you can collect metrics for one
repository, but not the first thing followed by the second. Closing that gap is
the next milestone and everything else waits behind it.

---

## Next: inventory-driven collection

**`github-metrics collect inventory.csv`** — read an inventory, collect metrics
for every repository in it, write one result set.

This is the feature the tool exists for. It is not merely a loop over `repo`,
because operating on four hundred repositories raises problems that operating
on one does not:

**Rate limiting.** The core limit is 5,000 requests/hour authenticated. A
repository costs several requests, so a large inventory can exhaust the budget
mid-run. The tool needs to know its budget before starting, report what a run
will cost, and stop cleanly rather than failing 380 repositories in.

**Partial failure.** A 404 for one repository must not lose the other 399. The
same lenient/strict split ingestion already has applies here, and the
`RowIssue` shape is likely the right model — which is part of why ingestion was
built that way.

**Resumability.** A run interrupted at repository 380 should not restart from
zero. This needs a durable intermediate result, which is a format decision
worth an ADR.

**Concurrency.** Collection is I/O bound on the network, so it benefits far
more than ingestion did. It also interacts with rate limiting in ways ingestion
never had to: parallel requests exhaust a shared budget faster, and secondary
rate limits penalise bursts. The concurrency-across-files reasoning in
[`adr/0002-concurrency-across-files-not-within-a-file.md`](adr/0002-concurrency-across-files-not-within-a-file.md)
does not transfer unchanged.

**Traceability.** `RepositoryRef.source_line` already exists so that a failure
here can name the inventory row that caused it. That field is currently unused
downstream; this is what it is for.

---

## Later

### Output formats for analysis

JSON is the handoff format today. CSV and Parquet output would let results go
straight into pandas or a spreadsheet without a conversion step. Deferred until
the metric set stabilises — a schema that changes every release is worse than
no schema.

### More metrics

The current set is deliberately small: stars, forks, watchers, open issues,
contributors, commit activity, license, primary language, and optional
contributor geography. Candidates, each of which needs a definition before it
needs an implementation:

- Release cadence and time since last release
- Issue and pull request response latency
- Bus factor / contributor concentration
- Dependency counts from the dependency graph API
- Security policy and advisory presence

The hard part is not collection but definition. "Time to first response"
requires deciding what counts as a response and whose response counts, and a
number without a documented definition is not comparable across repositories.

### Historical snapshots

Metrics are point-in-time. Trends need storage, a schema, and a decision about
what changes are worth recording. `collected_at` and `tool_version` already
exist on every snapshot so that stored results stay interpretable.

### Caching between runs

Repeated runs over a stable inventory re-fetch data that has not changed.
Conditional requests (ETag / `If-None-Match`) do not count against the rate
limit when they return 304, which would make re-runs nearly free. Waits for
inventory-driven collection, since caching one repository is not worth the
machinery.

### GitHub Enterprise validation

`GITHUB_API_URL` already points collection at an enterprise instance, but the
name grammar in `validation.py` encodes github.com's rules. An enterprise
instance may differ. Not addressed until someone needs it, because guessing at
another deployment's constraints would be inventing requirements.

### Non-GitHub forges

GitLab, Codeberg and sourcehut host FOSS too. This would be a substantial
change: `owner,repoid` is a GitHub-shaped identifier, and the inventory
contract would need a forge column. It is listed to acknowledge the question,
not because it is planned.

---

## Deliberately not scheduled

Recorded so that "why hasn't this been done" has an answer.

**A configuration file.** Everything configurable today is a handful of
environment variables and a few command-line flags. A config file would add a
precedence order to reason about for no current benefit.

**A plugin system for metrics.** Premature while the metric set is still small
enough to read in one sitting.

**A web UI or dashboard.** This is a CLI and a library. Presentation belongs to
whatever consumes its JSON.

**Async I/O.** Threads are sufficient for the current workload and are far
easier to reason about. If inventory-driven collection shows threads to be the
bottleneck, that measurement — not the fashion — is what would justify
revisiting it.
