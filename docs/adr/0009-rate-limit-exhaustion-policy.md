---
status: proposed
date: 2026-09-05
decision-makers: Joey
---

# `--on-exhaustion`: finishing a run larger than one hour's quota

## Context and Problem Statement

A scan cannot currently exceed the hourly budget. The pre-flight refuses a run
whose *minimum* does not fit, and a run that exhausts the budget partway
through fails outright.

v0.5.0 made that worse in two ways. Collecting every contributor means a real
repository costs far more than the floor — measured, 9 GraphQL points against a
floor of 2, a 4.5x understatement — so **mid-run exhaustion is now reachable
from a run the pre-flight accepted.** And 5,000 points no longer buys 2,500
repositories; for repositories the size of the one measured it buys about 550.

An inventory larger than that cannot be scanned at all today, which is the
capability being asked for: *"I need to be able to complete the run even if the
run will exceed the 5000/hour."*

There is a second problem underneath it. When exhaustion does happen, the run
stops and the rows already written stay on disk. Nothing in the output says the
file is partial. The repositories at the end of an inventory look exactly like
repositories that could not be read — which is the failure the pre-flight was
built to prevent, arriving by a different door.

## Decision Drivers

* A large inventory must be able to complete unattended, **without the caller
  having to know in advance that it is large**
* A partial result must be **unmistakably** partial, in the output and in the
  exit status, never only in a log line
* A run that is going to pause must say so before it pauses, not while it is
  paused

## Considered Options

* **Keep failing by default** — rejected. It is the capability gap being
  reported, and it makes the common case the one that needs a flag: an
  inventory large enough to matter is exactly the one a user wants to finish.
  Kept as a mode.
* **Wait by default** — chosen.
* **Emit partial results by default** — rejected: it turns a hard stop into a
  quiet incompleteness, which is worse than either failing or waiting. Kept as
  a mode.
* **No flag, one behaviour** — rejected; all three are legitimate in different
  contexts (unattended batch, CI with a time limit, exploratory run).

## Decision Outcome

Chosen: **`--on-exhaustion {fail,wait,partial}`, defaulting to `wait`.**

| Mode | On exhaustion | Exit | Artifacts |
|---|---|---|---|
| `wait` *(default)* | sleep to the hourly reset, continue | 0–4 as usual | complete |
| `fail` | stop immediately | 5 | whatever was written before the stop |
| `partial` | stop collecting, write everything gathered | **9** (new) | complete for what was collected |

**This changes the default behaviour of `scan`**, and that is deliberate rather
than incidental. A run that used to fail now finishes; no run that used to
succeed behaves differently, because a run inside the budget never reaches the
policy at all. The change is therefore only visible on runs that previously
produced nothing usable.

The alternative — defaulting to `fail` so that nothing changed — was written
into a draft of this ADR and rejected on review. It optimises for a caller who
already knows their inventory exceeds an hour's quota, which is precisely the
caller who does not need the protection: they can pass a flag. Defaulting to
`fail` means the *first* time anyone scans a large inventory they get a refusal
and have to discover a flag, and the tool's job is the batch.

### The pre-flight changes shape rather than being bypassed

Today the pre-flight *refuses*. Under `wait` or `partial` it must not, because
refusing is the thing being opted out of — and since `wait` is the default,
**the refusal is no longer the default outcome**. It becomes a **warning that
names the consequence** before anything is spent:

```
WARNING  4,120 repositories need at least 8,240 GraphQL points; 5,000 remain.
WARNING  --on-exhaustion=wait: this run will pause for the hourly reset at
         least once and may take 2h or more.
```

and for `partial`:

```
WARNING  --on-exhaustion=partial: this run is expected to stop early. Roughly
         2,500 of 4,120 repositories will be collected; the rest will be
         reported as unmeasured and the run will exit 9.
```

"Roughly" is deliberate. The estimate uses the floor, which understates, so the
message must not promise a number it cannot hold.

### A partial run is marked in the data, not only in the log

This is the part that matters more than the flag. A run that stopped early
records it **in three places**, so no consumer can miss it:

1. **Exit status 9**, distinct from every existing code.
2. **`statistics.json`** — `budget.exhausted: true`,
   `budget.incomplete_because_exhausted: true`, and the count of repositories
   never attempted ([ADR-0008](0008-statistics-json.md)).
3. **A row for every named repository**, including the ones never attempted,
   carrying identity and no measurements — the same shape an unreadable
   repository already produces.

Point 3 is the one that prevents the silent failure. Without it a partial CSV
is simply shorter, and a shorter file is indistinguishable from a shorter
inventory. With it, the row count always equals the accepted reference count
and the empty rows say which repositories were not reached.

### What makes waiting safe enough to be the default

A default that can block for hours needs to be defensible, and three things
make it so:

1. **It announces itself before it blocks.** The pre-flight already knows the
   run does not fit and says so, with an estimate of how long the run will take
   and how many pauses it expects, before spending anything.
2. **It is interruptible and loses nothing.** Ctrl-C during a wait leaves the
   rows already collected on disk and the geocode cache saved, because the
   cache is written in a `finally` block. A cancelled `wait` degrades to
   roughly what `partial` would have produced.
3. **It reports progress while paused.** An INFO line naming the resume time,
   repeated as the wait continues, so a run that looks hung can be told from
   one that is.

Without all three, `fail` would be the better default. With them, the case for
refusing a run the token could have finished is weak.

## Consequences

* Good: an inventory of any size can be scanned to completion, by default
* Good: partial results become a first-class, self-describing outcome instead
  of a truncated file
* Good: exit 9 lets a pipeline branch on "incomplete but usable"
* Bad: **the default can now run for many hours.** A CI job with a step
  timeout will hit it rather than failing fast, and `--on-exhaustion fail` is
  what such a job should pass. This is called out in the CLI reference and the
  user guide rather than left to be discovered
* Bad: a default behaviour change is a contract change, recorded in the
  changelog as such
* Bad: a new exit code is a change to a documented contract, and codes are
  permanent once issued
* Bad: `wait` holds a token idle across a reset; a token used elsewhere
  meanwhile may make the resumed run exhaust again. It re-checks rather than
  assuming

## Implementation notes

* Exhaustion is detected from the response, not predicted: GitHub reports
  remaining budget on every call, and the runner already reads it.
* Under `wait`, sleep to `X-RateLimit-Reset` plus a small margin, then
  re-check. Never busy-poll.
* **Secondary rate limits are a different mechanism** (403 with `Retry-After`,
  for burst and abuse detection) and are *not* covered by this flag. They need
  their own backoff with a retry cap, and remain deferred.
* Concurrency should fall to one worker while waiting, so a resumed run does
  not immediately burst into the fresh quota.

## More Information

* [`API-LIMITS.md`](../API-LIMITS.md) §5 for what a run of *N* repositories costs
* [ADR-0004](0004-exit-code-scheme.md) for the severity ordering exit 9 joins
* [ADR-0006](0006-collect-every-contributor.md) for why the pre-flight became a
  floor, which is what makes this necessary
