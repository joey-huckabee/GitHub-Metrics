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

* A large inventory must be able to complete unattended
* A partial result must be **unmistakably** partial, in the output and in the
  exit status, never only in a log line
* Today's behaviour must not change silently
* Waiting must be a choice, not a surprise — an hour of sleeping is not
  something a tool should decide on a user's behalf

## Considered Options

* **Keep failing** — rejected; it is the capability gap being reported.
* **Always wait** — rejected as a *default*: a run could block for hours
  without having been asked to. Offered as a mode.
* **Always emit partial results** — rejected as a default: it turns a hard stop
  into a quiet incompleteness, which is worse than failing.
* **A policy flag with all three** — chosen.

## Decision Outcome

Chosen: **`--on-exhaustion {fail,wait,partial}`, defaulting to `fail`.**

`fail` is today's behaviour, so no existing invocation changes meaning. The
other two are opt-in, and both announce themselves up front.

| Mode | On exhaustion | Exit | Artifacts |
|---|---|---|---|
| `fail` *(default)* | stop immediately | 5 | whatever was written before the stop |
| `wait` | sleep to the hourly reset, continue | 0–4 as usual | complete |
| `partial` | stop collecting, write everything gathered | **9** (new) | complete for what was collected |

### The pre-flight changes shape rather than being bypassed

Today the pre-flight *refuses*. Under `wait` or `partial` it must not, because
refusing is the thing being opted out of. It becomes a **warning that names the
consequence** before anything is spent:

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

### Why `wait` is not the default

Completing is usually the intent, which is a real argument for it. But a
default that can block for hours is a default that surprises someone, and the
surprise is unbounded: a 20,000-repository inventory would sleep through four
resets. `fail` surprises nobody, and the warning tells a user which flag they
want the first time they hit the ceiling.

## Consequences

* Good: an inventory of any size can be scanned to completion
* Good: partial results become a first-class, self-describing outcome instead
  of a truncated file
* Good: exit 9 lets a pipeline branch on "incomplete but usable"
* Bad: `wait` can run for many hours; the warning and a periodic INFO line
  saying when it will resume are the mitigation
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
