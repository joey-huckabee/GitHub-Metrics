---
status: accepted
date: 2026-09-06
decision-makers: Joey
---

# No results database; the artifacts are the store

## Context and Problem Statement

`ROADMAP.md` carried **v0.7.0 - Persistence** from the earliest drafts: capture
results in SQLite behind an interface that could be swapped for PostgreSQL,
with one schema covering the row, the contributor block and the run statistics,
and the geocode cache folded in.

The question asked before that work started was whether it was needed at all.
The tool is functional; it already persists the one thing a re-run genuinely
depends on, which is the geocode cache; and the roadmap section had been
rewritten twice as v0.6.0 changed what the schema would have to hold.

The case for a store rested on three claims - that it would be **faster**, that
it would make the data **queryable**, and that it would give the metrics a
**history**. Each was checked against measured numbers rather than argued from
first principles. None of the three survives, and the two real problems hiding
underneath them are both smaller than a database.

## Decision Drivers

* The scarce resource in a scan is **GitHub API budget**, not disk, memory or
  CPU - so "faster" has to mean "spends fewer points", or it means nothing
* `METRICS.md` still carries **TBD** entries, and `contribution_total` has
  already meant three different populations across v0.4.1, v0.5.0 and a
  `--deep-attribution` run. A schema designed now freezes a shape still moving
* A second storage mechanism has to earn its keep against artifacts that
  already exist and are already a documented contract
* What is *not* built must be recorded as a decision, or it gets re-proposed
  every few releases as though it were an oversight

## Considered Options

* **SQLite for rows, contributors and run statistics**, as v0.7.0 scoped it
* **A store limited to run-level statistics**, for trend analysis only, leaving
  documents as files
* **No results database.** Files remain the artifact; the two problems a store
  would have solved get solved directly - chosen

## Decision Outcome

Chosen: **no results database.** `githubmetrics.csv`, the per-repository
documents and `statistics.json` are the store. The work that was queued behind
v0.7.0 is split into three smaller releases, none of which is a database.

### The performance claim fails outright

Nothing in a scan is disk-bound. From the measured `NousResearch/hermes-agent`
run in [API-LIMITS.md](../API-LIMITS.md): **186 s cold, 42 s warm.** That
144-second gap is the **geocode cache**, which already exists and is already
JSON. The rest is network, and Nominatim's mandatory one-request-per-second
pacing.

Against that, writing a document is not measurable: the largest in the
conformance suite is **138 KB** for 161 contributors, and it is written once.
There is no scan-time win available from changing where results are put,
because putting them anywhere is not where the time goes.

A store would only be faster in the sense that matters - fewer API points - if
it let a run **skip work it had already done**. That is a real saving, and it
is addressed below without a schema.

### The query claim fails, and the artifacts are why

The claim was that cross-repository questions need a database. They do not,
because the artifacts are uniform enough to query as a set. Verified against
every conformance route - default, `--deep-attribution`, anonymous recovery,
and a budget-exhausted partial run:

| Property | Result |
|---|---|
| Documents compared | 4, across 4 different collection routes |
| Distinct top-level key sets | **1** |
| Distinct key *orders* | **1** (26 keys) |
| Distinct CSV headers | **1** |

That invariance is not luck. The column set is derived from `SoftwareRow`'s
field order rather than from a parallel list, and `--fields` deliberately does
not filter documents, so a document is the same shape whatever produced it. A
directory of scans is therefore a table already, and an engine that reads a
glob of CSV or JSON - DuckDB being the obvious one - answers cross-repository
questions over it directly, with no schema written here and no import step.

Scale is not the objection either: a 2,000-repository inventory is on the order
of **700 MB** of JSON and CSV (estimated from the 138 KB document, at roughly
860 bytes per contributor), which is unremarkable for a file-based engine.

Writing those queries down is [v0.8.0](../ROADMAP.md). Until they are written
and run, this section's claim rests on the artifacts' shape, which is measured,
and not on the queries themselves, which are not.

### The history claim is the strongest, and still loses

"Is this project growing or dying" needs more than one point in time, and that
is the best argument for a store. `scan_id` and `scan_date` exist precisely so
that stored rows can be grouped by the run that produced them.

But a directory of dated scans **is** that history, and it is queryable as a
time series by the same glob. What a schema would add is not the capability -
it is indexes and joins, which matter at a scale this project has not reached.
Scanning 2,000 repositories weekly for two years is roughly 100 scans and
**~70 GB** (estimated), and that is the point at which files stop being the
right answer and PostgreSQL starts being it.

The argument for waiting until then is not laziness. It is that a schema
designed at that point would be designed by someone who knows which queries are
actually run, against metric definitions that have stopped moving. Neither is
true today: `METRICS.md` has open **TBD** entries, and a stored row would need
to record its metric-definition version, its tool version and its attribution
method to be comparable with a later one - three columns whose contents are
still being decided. Designing that now buys a migration per decision.

### What a store would really have bought

Two things, and neither needs one.

**Resuming an interrupted run** is the only genuine API saving in the whole
proposal. A 2,000-repository inventory spans several rate-limit windows, and a
run that dies in the third hour currently re-collects everything. But the
output directory already records what finished: **a document exists only where
the repository was fully collected**, and it carries `scan_id` and `scan_date`,
so its freshness is checkable. Resuming is a directory listing, not a schema.
It becomes [v0.7.0](../ROADMAP.md).

**The geocode cache outgrowing a JSON file** is a real, measured problem - load
time scales with the whole file whether a run needs forty locations or all of
them - and SQLite is the right answer to it. But that is a **cache** concern,
not a results concern: it wants random access by key with expiry, where results
want to be an append-only export. v0.7.0 fused the two, and
[ADR-0007](0007-persistent-geocode-cache.md) deferred SQLite partly *because*
the store was coming. That reasoning is now withdrawn; the conclusion is not.
It becomes [v0.9.0](../ROADMAP.md), gated on a measurement rather than a date.

### Rejected on inspection: conditional requests

Worth recording, because it is the idea that sounds most like a free win, and
`API-LIMITS.md` had it queued as pairing "naturally with the persistence work".

`ETag` / `If-None-Match` returning **304 does not count against the REST rate
limit**, so re-scanning an unchanged repository could pay zero REST requests
instead of the measured ~5. It fails on the binding constraint. One repository
of the measured size spends 9 GraphQL points and about 5 REST requests, so an
hour's quota buys roughly **555** repositories on GraphQL against **1,000** on
REST. **GraphQL binds first, and GraphQL has no conditional requests at all.**
Making the non-scarce resource cheaper does not let one additional repository
be scanned.

It also carries a cost this project would feel. A 304 says "unchanged"; it does
not say what the value is, so the response body has to be stored and replayed.
That is a tool whose entire discipline is refusing to publish numbers it cannot
stand behind, serving numbers it did not just measure.

## Consequences

* Good: no schema is designed against metric definitions that are still open
* Good: the three claims are recorded with the numbers that defeated them, so
  re-proposing a store takes new evidence rather than new enthusiasm
* Good: the two real problems are fixed in v0.7.0 and v0.9.0 at a fraction of
  the cost, and the API saving arrives sooner than it would have
* Bad: cross-repository analysis depends on an **external** tool. That is a
  documented dependency rather than a shipped one, and v0.8.0 is what keeps it
  from being an exercise for the reader
* Bad: **ADR-0007's stated reason for choosing JSON is withdrawn.** It chose
  JSON partly because v0.7.0's store would be the permanent home, and there is
  no such home now. That decision stands on its measured size and load figures
  instead, which were always the load-bearing half
* Bad: at genuine longitudinal scale this has to be revisited. The trigger is
  named above - roughly 70 GB, or two years of weekly scans at 2,000
  repositories - so that revisiting it is a threshold being crossed rather than
  an argument being reopened

## More Information

* [ADR-0007](0007-persistent-geocode-cache.md), whose JSON-over-SQLite
  reasoning this amends
* [ADR-0005](0005-one-scan-command-and-per-repository-json.md) for why the
  artifacts have the shape that makes them queryable
* [API-LIMITS.md](../API-LIMITS.md) for every cost figure quoted here
* `ROADMAP.md`, v0.7.0 through v0.9.0, for what replaced Persistence
