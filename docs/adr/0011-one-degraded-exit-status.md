---
status: accepted
date: 2026-09-06
decision-makers: Joey
---

# One degraded exit status, and the retirement of exit 9

## Context and Problem Statement

Two faults in the exit-code scheme, found together.

**A run that lost every document reported success.** A repository whose
contributor list failed is measured: its row is complete and correct, and it
produces no document. Nothing in the exit logic looked at `contributor_error`,
so a scan where *every* repository's contributors failed wrote a full CSV, zero
documents, and exited **0**. A pipeline branching on status saw a clean run, and
a consumer reading the directory could not tell a repository with no
contributors from one whose contributors could not be read.

That gap survived six releases. It was recorded as deficiency 4 in
`SCAN-PROCESS.md` and never closed.

**Exit 9 broke the scheme's own contract.** [ADR-0004](0004-exit-code-scheme.md)
promises two tests, and repeats them in `CLI-REFERENCE.md` and `USER-GUIDE.md`:

    $? -ge 3    something was wrong
    $? -ge 5    nothing usable came out

v0.6.0 added exit 9 for a run stopped by `--on-exhaustion partial`. That run
produces a **perfectly usable file** - every named repository has a row, the
unreached ones marked - and yet `$? -ge 5` classifies it as unusable. A caller
following the published advice would discard results it should have kept.

The second fault is the reason the first cannot be fixed by adding a code.

## Decision Drivers

* The `$? -ge 5` boundary must keep meaning what three documents say it means
* "Which kind of incompleteness" must remain answerable, somewhere
* Exit codes are a contract; retiring one is not free

## Considered Options

* **Keep 9, amend the boundary rule** to `$? -ge 5 && $? -le 8`
* **Keep 9, and add 10 for contributor failures**, replacing the boundary with
  a precedence table
* **Retire 9; one degraded status covering every incomplete outcome** - chosen

## Decision Outcome

Chosen: **exit 4 for every incomplete outcome, and exit 9 retired.**

`EXIT_REPOSITORY_UNFETCHABLE` becomes `EXIT_DEGRADED`, and the run exits it
when any repository was not attempted, could not be read, **or** produced no
document because its contributor list failed.

| Code | Meaning | File written? |
|---|---|---|
| `0` | Everything named was collected completely | yes |
| `3` | Some input rows were rejected | yes |
| `4` | **Degraded: a usable file, with something missing from it** | yes |
| `5`–`8` | Aborted | partial or none |

Both published tests survive unchanged, which is the point.

### Why not a third degraded code

The degraded band is two codes wide - 3 and 4 - because 5 begins the aborted
range and 1 and 2 belong to click. A third degraded code has nowhere to go but
above 8, which is what exit 9 did, and it breaks `$? -ge 5` for everyone
whether it is called 9 or 10. Option two makes that worse rather than better:
with 3, 4, 9 and 10 all degraded, no threshold works at all and every caller
needs an explicit set.

Amending the boundary to a range was the closest alternative and was rejected
because it moves the cost onto every caller, for ever, to preserve a
distinction that belongs in the data.

### The distinction is not lost, it moves

`statistics.json` already carries it per repository, and more precisely than a
status could:

```json
{"attempted": false, "collected": false, "documented": false}   // never reached
{"attempted": true,  "collected": false, "documented": false}   // unreadable
{"attempted": true,  "collected": true,  "documented": false}   // no document
```

plus `budget.incomplete_because_exhausted` at run level. A caller that needs to
tell them apart reads the artifact; one that only needs "is this complete"
reads the status. That is the right split: a single byte was always going to be
the wrong place for a three-way distinction that is really per repository.

## Consequences

* Good: `$? -ge 3` and `$? -ge 5` mean exactly what they are documented to mean
* Good: a run that lost its documents can no longer report success
* Good: one rule to reason about instead of a precedence table
* Bad: **exit 9 is retired one release after it shipped.** Any caller keying on
  it must move to 4. It is recorded here with its condition and is never reused
* Bad: a caller wanting to distinguish "unreadable" from "undocumented" must
  read `statistics.json` rather than branch on the status

## More Information

* [ADR-0004](0004-exit-code-scheme.md) for the scheme and the boundary
* [ADR-0009](0009-rate-limit-exhaustion-policy.md), which introduced exit 9
* `SCAN-PROCESS.md` deficiency 4, closed by this
