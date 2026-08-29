---
status: accepted
date: 2026-08-29
decision-makers: Joey
---

# Apply concurrency across inventory files, not within a single file

## Context and Problem Statement

Ingestion was asked to "leverage concurrency if possible". That phrasing hides
a real question, because there are two distinct places concurrency could go and
they are not equally worthwhile:

1. **Across files** — read several inventory CSVs at the same time
2. **Within a file** — parse or validate the rows of one CSV in parallel

Choosing both by default would be the flattering answer. It would also be
wrong, and expensively so: concurrency that does not pay for itself still costs
determinism, debuggability, and the ability to reason about failure ordering.

## Decision Drivers

* Concurrency must produce a measurable benefit, not a structural one
* The result must not depend on scheduling — an inventory that reorders itself
  between runs cannot be diffed or reproduced
* Failure reporting must stay deterministic when several inputs are bad
* The implementation must stay legible to a maintainer who did not write it

## Considered Options

* **Threads across files, sequential within each file**
* **Threads across files and parallel row validation within each file**
* **Process pool across files**
* **Fully sequential** — no concurrency at all

## Decision Outcome

Chosen option: **threads across files, sequential within each file**.

### Why across files

Reading a file is dominated by waiting on storage, and waiting is exactly what
threads overlap well. Ten inventory files on a normal disk finish in roughly
the time of the slowest rather than the sum of all ten, and the code stays a
single `ThreadPoolExecutor.map` call. The pool is bounded at
`min(len(files), 8)` because the benefit flattens early — past a handful of
concurrent reads the device is the limit, not the waiting — and a thread per
file would be pure overhead for a directory of hundreds.

### Why not within a file

This is the part worth writing down, because "parallelise the rows" sounds like
an obvious win and is not one.

**The work is not independent.** Parsing one file is a sequence of genuinely
dependent phases. The header determines which column index carries `owner`, so
no row can be interpreted before it is read. Duplicate detection depends on
every row that came before, so it is inherently ordered. What remains
genuinely parallel is per-row syntactic validation.

**That remainder is too small to pay for coordination.** Validating a row is a
length check and two regex matches over strings of a few dozen characters —
well under a microsecond. It is pure CPU work in Python, so under the GIL
threads cannot run it in parallel at all, and a process pool would have to
pickle every row across a boundary at a cost several orders of magnitude above
the work itself. Splitting a 5,000-row file into chunks would reliably make it
slower.

**And the cost is not only performance.** Concurrent row handling would make
the order of reported issues depend on scheduling, so the same broken file
would produce a differently-ordered report on each run. That is precisely the
property an analyst relies on when they fix the first three problems and re-run
to see what is left.

So the honest answer to "leverage concurrency if possible" is: yes across
files, where the wait is real, and no within a file, where there is nothing to
overlap. Concurrency is not applied here because it is available; it is applied
where the work is actually blocked.

### Why not processes

The work is I/O bound, so a process pool would add serialisation and startup
cost without removing a bottleneck. Recorded as non-requirement NR-004.

### Determinism obligations this creates

Concurrency is only acceptable because two properties are held explicitly, both
stated as requirements rather than left to the executor's defaults:

- **Results are returned in input order** (L2-CON-002). `Executor.map` preserves
  it; `as_completed` would not.
- **When several sources fail, the error raised belongs to the earliest source
  in input order**, not to whichever thread failed soonest. This is the half
  that is easy to overlook — it only becomes visible once a batch contains two
  bad files, and then it presents as a flaky error message rather than as a
  concurrency bug.

### Consequences

Good:

- Multi-file reads scale with the number of files, up to the pool bound
- Single-file behaviour is exactly as simple to read and debug as if no
  concurrency existed anywhere
- Results and errors are reproducible run to run

Bad:

- A single very large file gets no speedup. This is accepted: at the stated
  scale of a few thousand rows it is already fast, and a file large enough to
  matter would want streaming rather than parallelism.

Neutral:

- `max_workers` is exposed so an operator can pin it to 1, which is useful when
  diagnosing an I/O problem and wanting the reads serialised.

## More Information

Requirements L2-CON-001 through L2-CON-003 and their L3 derivations.
Verified by `tests/test_ingest.py`, including a test that 1, 2 and 16 workers
produce identical results, and one that the reported error is chosen by input
order rather than by completion order.
