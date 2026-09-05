---
status: proposed
date: 2026-09-05
decision-makers: Joey
---

# A third artifact: `statistics.json`

## Context and Problem Statement

`githubmetrics.csv` is a comparable table and the per-repository documents are
detail records. Neither can say **how good the data in them is**.

The v0.5.0 live run made that concrete. One repository reported 396
contributors and a `contribution_total` of 27,828. Both numbers are correct and
both are misleading on their own: the repository actually has 3,310 contributor
identities and 32,005 commits, GitHub linked only the first 500 author email
addresses to accounts, three of the 396 are bots holding 289 commits between
them, and only 175 of the 396 published a location at all.

None of that appears in either artifact. A repository truncated at GitHub's
ceiling is **indistinguishable from one with 396 contributors**, and a
percentage computed over the collected set implies a census it is not.

The requirement is therefore not "more metrics". It is: **every number this
tool publishes should be accompanied by the bounds within which it is true**,
and those bounds should be comparable across repositories so significance can
be judged across a portfolio.

## Decision Drivers

* A consumer must be able to tell a complete measurement from a truncated one
* Exclusions must be counted **and given reasons**, not silently absent
* Cross-repository comparison must be possible without re-deriving anything
* The CSV's twenty-column contract must not change
* The downstream residency stage is a separate project; this must feed it
  without depending on it

## Considered Options

* **Add columns to `githubmetrics.csv`** — rejected: the counts are per
  repository but the reasons are a nested list, which has no representation at
  the table's grain, and it breaks a fixed contract.
* **Add keys to each per-repository document** — rejected on its own: the
  run-level facts (budget spent, cache behaviour, repositories refused) have no
  home there, and a cross-repository question would mean opening N files.
* **A third artifact, one per scan** — chosen.
* **Both: a third artifact *and* per-repository blocks** — rejected as
  duplication; the third artifact carries a per-repository array instead.

## Decision Outcome

Chosen: **one `statistics.json` per scan**, written beside `githubmetrics.csv`,
carrying the same `scan_id`, with a run-level section and a per-repository
array.

The CSV stays at twenty columns. The documents keep their shape. Nothing that
exists changes meaning.

### `contribution_total` is not adjusted for bots

Bots are identified and their commits are counted, but **`contribution_total`
keeps them**, and `statistics.json` publishes `contribution_excluding_bots`
beside it.

This was a deliberate choice against the alternative. Changing the total would
be the third redefinition of that field in two releases, would make v0.5.0 and
v0.6.0 documents silently incomparable, and would bake one judgement — that a
bot's commits "don't count" — into a raw measurement. Publishing both leaves
the judgement to the analysis, which is where it belongs, and keeps the
document a record of what GitHub reported.

## What goes in it, and why each item earns its place

### Run level

| Field | Why |
|---|---|
| `scan_id`, `scan_date`, `tool_version` | joins to the other artifacts; **`tool_version` appears here first**, closing a gap `ROADMAP.md` has carried |
| `repositories_named` / `_collected` / `_documented` / `_failed` | the three counts differ, and today only stderr says so |
| `duration_seconds` | planning large runs |
| `budget.*` — points and requests spent, remaining, policy, whether exhausted | the pre-flight is a floor; this is where a run says whether the floor held |
| `geocoding.*` — cache hits, lookups, matches, misses, service failures | distinguishes "no location" from "geocoder was down", which the documents deliberately cannot |
| `warnings[]` | every degradation, machine-readable, in one place |

### Per repository

**Completeness — the reason the file exists**

| Field | Example |
|---|---|
| `commits.total_on_default_branch` | 32,005 — one extra GraphQL point, `history { totalCount }` |
| `commits.attributed_to_collected` | 27,828 |
| `commits.coverage_percent` | **87.0** |
| `contributors.total_identities` | 3,310 — from an `anon=1` count |
| `contributors.collected` | 396 |
| `contributors.coverage_percent` | 12.0 |

`commits.coverage_percent` is the single most important number in the file. It
is the difference between "87% of this project's work is characterised" and an
implied census.

**Exclusions, with reasons** — the taxonomy the user asked for

| `reason` | Recoverable | Meaning |
|---|---|---|
| `anonymous_recoverable_noreply` | **yes** | `NNN+login@users.noreply.github.com`; id and login are recoverable, and v0.6.0 does recover them |
| `anonymous_no_account` | no | real email; GitHub exposes no email-to-user lookup |
| `account_unresolvable` | no | login present, GraphQL returned `NOT_FOUND` (deleted or suspended) |
| `bot` | n/a | resolved as a `Bot`, or a `[bot]` login suffix |
| `no_location_published` | n/a | collected; published no location, so never geocoded |
| `location_unresolved` | n/a | published a location no gazetteer recognises |
| `geocoder_unavailable` | **retryable** | the lookup failed for a reason unrelated to the location |

Each carries `people` and `commits`. The last three are not exclusions from
collection but exclusions from *geographic* analysis, and separating them is
what lets a `foreign_percent` carry an honest denominator.

**Bots**

`count`, `commits`, `contribution_excluding_bots`, the detection methods used,
and the list itself. Measured example: 3 bots, 289 commits.

**Concentration and distribution** — cheap, and directly answers "where does
this project's work come from"

| Field | Why |
|---|---|
| `top_1_share`, `top_5_share`, `top_10_share` | one repository measured 50% of commits in its top contributor |
| `bus_factor` | contributors needed to reach 50% of commits |
| `gini` | inequality of contribution, comparable across repositories |
| `countries` | commits and people per `country_code` |
| `distinct_countries` | 41 in the measured run |
| `commits_with_unknown_location_percent` | **the error bar on every geographic claim** |

That last field is the one the downstream stage needs most: it is the maximum
possible error in any `foreign_percent` it computes.

### What is deliberately **not** in it

`foreign` and `adversarial`, and anything derived from them. That determination
happens in a separate project. This file supplies the **denominators and the
unknown-share** that stage needs to bound its own percentages, and asserts
nothing about any person.

## Consequences

* Good: a truncated repository announces itself; percentages can carry bounds
* Good: cross-repository significance is answerable from one file
* Good: `tool_version` finally appears in an artifact
* Bad: **two extra API calls per repository** — `history { totalCount }` (1
  GraphQL point) and an `anon=1` count. The count is the expensive one at ~34
  REST pages for a large repository; it may need to be opt-out for very large
  inventories
* Bad: a third artifact is a third thing to keep consistent; it is generated
  from the same in-memory outcomes as the other two, which is the mitigation

## Maintainer coverage is deliberately not reported

An earlier draft proposed a `maintainers` block answering "are the maintainers
and their work fully captured". **It is dropped**, because no route to the
answer is consistent enough to publish:

- `GET /repos/{o}/{r}/collaborators` **requires push access**, which a
  read-only token on a third-party repository does not have. Not viable at all.
- `CODEOWNERS` is public and authoritative *where it exists*, and most
  repositories do not have one. A field populated for a minority and empty for
  the rest is not comparable across a portfolio, which is the only reason to
  collect it.
- Public organisation membership is opt-in and is not maintainership.
- Top-N contributors by commits would always work, and is a **proxy, not a
  fact**. Labelling it "maintainers" is exactly the kind of invented value this
  project refuses elsewhere — the same defect as reverse-geocoding a country
  centroid into a county.

The choice was therefore between a field that is absent for most repositories
and a field that is a guess. Both are worse than not answering, so
`statistics.json` does not answer it and does not reserve a key for it. If a
consistent public source appears, this decision can be revisited with an ADR of
its own.

What *is* reported serves the underlying question better anyway: the
concentration figures — `top_1_share`, `top_5_share`, `bus_factor` — describe
where a project's work is concentrated without needing to name anyone a
maintainer.

## More Information

* [`SCAN-PROCESS.md`](../SCAN-PROCESS.md), "Known deficiencies" — items 1, 2, 3
  and 7 are what this file exists to quantify
* [`API-LIMITS.md`](../API-LIMITS.md) §2 and §3.2 for the measured ceiling and
  the no-reply recovery
* [ADR-0006](0006-collect-every-contributor.md) for why the contributor list is
  unbounded and why the budget is a floor
* [ADR-0010](0010-optional-commit-history-attribution.md) for the opt-in deep
  dive, and the threshold at which this file recommends it
