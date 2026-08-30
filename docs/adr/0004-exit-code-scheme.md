---
status: accepted
date: 2026-08-29
decision-makers: Joey
---

# Use small ordered exit codes rather than sysexits.h

## Context and Problem Statement

`github-metrics metrics` can end in more than two ways, and a pipeline needs to
tell them apart without parsing the report:

- everything collected cleanly
- the file loaded but some input rows were rejected
- the repositories were valid but some could not be fetched (404, private)
- the run was abandoned because the API budget ran out
- the input file could not be read at all

The question was whether an existing standard covers this.

## Decision Drivers

* A caller must distinguish "degraded but usable" from "produced nothing"
* Codes must not collide with values the shell or the CLI framework already own
* An operator reading a code in a log should not need a lookup table for the
  common cases
* The scheme has to survive new failure modes being added in later versions

## What standards actually exist

There is no universal standard beyond "0 is success". What exists:

**`sysexits.h`** (BSD, 1987) is the closest thing to a real standard:
`EX_USAGE` 64, `EX_DATAERR` 65, `EX_NOINPUT` 66, `EX_UNAVAILABLE` 69,
`EX_SOFTWARE` 70, `EX_TEMPFAIL` 75, `EX_CONFIG` 78. It was written for
sendmail's delivery agents and is used by parts of the BSD userland. Outside
that lineage it is rare, and even its own author's project treats it as
advisory.

**Reserved ranges** are the one hard constraint. POSIX shells use `126` for
"found but not executable", `127` for "not found", and `128+N` for "killed by
signal N" — so `130` is Ctrl-C. Exit status is also truncated to 8 bits, so
`256` becomes `0`. **Anything at or above 126 is unusable.**

**Common practice** in widely-used tools is small ad-hoc codes with documented
meanings, not sysexits: `diff` uses 0/1/2 (same / differ / trouble), `grep` the
same shape (match / no match / error), `git` uses 0/1/128, `rsync` has its own
0–35 table.

**Click owns 1 and 2 regardless of what we choose.** `ClickException.exit_code`
is 1 and `UsageError.exit_code` is 2, and those fire before our code runs — a
malformed command line exits 2 whatever scheme we adopt.

## Considered Options

* **Small ordered codes (0–6), severity-ranked**
* **`sysexits.h` values (64–78)**
* **Two codes only (0 / non-zero)**

## Decision Outcome

Chosen option: **small ordered codes, ranked by severity**.

`sysexits` was rejected for two reasons. Its categories do not fit — nothing in
it expresses "succeeded but degraded", which is the distinction this tool most
needs. And adopting it would produce a *mixed* scheme regardless, because click
still exits 2 for a usage error; a table containing both `2` and `64` for
usage-shaped problems is worse than either alone.

Two codes only was rejected because it collapses exactly the case that
motivated this: an inventory that loaded with eleven bad rows is not the same
outcome as one that could not be opened.

### The scheme

| Code | Meaning | Output produced? |
|---|---|---|
| `0` | Every row collected cleanly | yes |
| `1` | Configuration error, e.g. a missing token | no |
| `2` | Usage error — malformed command line | no |
| `3` | Degraded: some input rows were rejected | yes |
| `4` | Degraded: some repositories could not be fetched | yes |
| `5` | Aborted: API budget exhausted, or pre-flight refused the run | partial or none |
| `6` | Aborted: the input could not be read | no |

`1` and `2` are click's and are listed for completeness rather than chosen.

**Codes are ordered by severity, and the highest applicable code wins.** A run
that both rejected input rows and failed to fetch a repository exits `4`. This
gives a single rule to reason about instead of a precedence table, and it means
a caller can write `[ $? -ge 5 ]` to mean "nothing usable came out" and
`[ $? -ge 3 ]` to mean "something was wrong".

The 3/4 versus 5/6 split is the load-bearing part: **3 and 4 still produced a
usable file; 5 and 6 did not.** A pipeline that treats any non-zero status as
fatal will discard results it could have used, and one that treats every status
as fine will consume a truncated inventory. The boundary at 5 exists so neither
mistake is necessary.

### Consequences

Good:

- A caller can act on degraded-but-usable without parsing the report
- Room to add codes 7 and above as new failure modes arrive, keeping the
  severity ordering intact
- Nothing collides with the shell's reserved range

Bad:

- This changes the codes the shipped `ingest` command used, where `2` meant
  "unreadable input" — now `6`. `ingest` is being replaced by `metrics` in the
  same release, so the break happens once, in the release that renames the
  command, rather than silently later.

Neutral:

- Codes 1 and 2 keep click's meanings. Documenting them as inherited rather
  than pretending they were chosen keeps the table honest.

## More Information

The conditions behind each code are catalogued in
[`../ERROR-CATALOG.md`](../ERROR-CATALOG.md). The command surface is in
[`../CLI-REFERENCE.md`](../CLI-REFERENCE.md).
