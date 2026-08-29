---
status: accepted
date: 2026-08-29
decision-makers: Joey
---

# Ingest leniently by default, with strict mode as an opt-in

## Context and Problem Statement

A repository inventory is maintained by hand, so some of its rows will be
wrong. The tool has to decide what a bad row means for the rest of the file.

The two obvious answers are opposites. Reject the file, and a single stale
entry blocks an analysis over four hundred good repositories. Skip the row
silently, and the analysis quietly covers 399 repositories while reporting as
though it covered 400 — which is worse, because the number is still plausible.

## Decision Drivers

* An analyst fixing a list needs every problem in one pass
* An automated pipeline must not consume a silently incomplete inventory
* A row that was dropped must never be invisible
* One default must be right for the common case; the other must be reachable

## Considered Options

* **Lenient by default, strict on request**
* **Strict by default, lenient on request**
* **Lenient only**, with problems reported but never fatal
* **Strict only** — any defect fails the read

## Decision Outcome

Chosen option: **lenient by default, strict on request**, with rejected rows
always reported and never silent.

The two modes exist because there are two users with genuinely opposed needs,
not because the choice was hard to make.

An **analyst** cleaning a hand-maintained list wants the whole list of problems
at once. Strict-by-default turns a file with eleven bad rows into eleven
edit-run cycles, each revealing exactly one more problem. That is the single
most common workflow for this tool, so it is the default.

A **pipeline** wants the opposite. Any defect at all should stop the run before
a downstream stage treats a partial inventory as complete. It gets `--strict`,
which raises on the first issue and returns no result — deliberately, so a
caller cannot use a partial answer by accident.

Lenient-only was rejected because it leaves automation with no way to fail.
Strict-only was rejected because it makes the tool hostile to its primary
audience.

### What makes lenient safe

Leniency is only defensible because nothing is dropped quietly. Three
properties carry that weight together:

1. **Every rejected row produces a `RowIssue`** carrying its code, its physical
   line number, and the offending value. It is reported, not merely counted.
2. **The result exposes the arithmetic** — `rows_read`, `accepted` and
   `rejected` — so a caller can tell 400-of-400 from 389-of-400 without
   inspecting the issue list.
3. **The CLI exits 3, not 0**, when any row was rejected. A pipeline that
   checks only for a zero exit still notices.

Remove any one of those and lenient becomes silent, which is the failure mode
this decision exists to prevent.

### Why duplicates count as issues

A repeated repository is not malformed, and it would be defensible to drop it
without comment. It is reported anyway, because a duplicate usually means the
inventory was assembled from two overlapping sources — which is something the
analyst wants to know about the *list*, even though it says nothing about the
row.

### Consequences

Good:

- The common workflow is one pass: run, read the report, fix, re-run
- Automation gets an unambiguous stop signal
- A partial inventory can never be mistaken for a complete one, at any layer

Bad:

- Two code paths through the same reader, which must stay in step. A test
  asserts that a clean file produces an identical result under both modes,
  which is what keeps them honest.

Neutral:

- Strict mode reports only the *first* problem. That is the point of it, but it
  means a pipeline failure gives less information than an analyst run over the
  same file would. The remedy is to re-run leniently, which the error message
  does not currently suggest and probably should.

## More Information

Requirements L1-ING-003, L2-ERR-002, L2-ERR-003 and L2-CLI-004. Codes for every
row-level rejection are catalogued in [`../ERROR-CATALOG.md`](../ERROR-CATALOG.md).
