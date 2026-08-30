# GitHub-Metrics — Metric Definitions and Scoring

The authoritative definition of every column in `githubmetrics.csv`: where the
value comes from, how it is calculated, and how it is scored.

**This document is under construction.** Rows marked **TBD** are not yet
decided and are being worked through. Nothing marked TBD is implemented, and
nothing is implemented that is not defined here first — a metric with an
undocumented definition is not comparable across repositories, which is the
whole point of collecting it.

## Output shape

One row per accepted input row, in input order. Column order is fixed and is
the order below.

```csv
client_name,owner,organization,scan_date,scan_id,stars,forks,age_days,last_update_hours,closed_issues,releases,prevalence_score,stars_score,forks_score,maturity_score,last_update_score,trusted_org_bonus,total_score,is_trusted_org
```

Reference row (the worked example this document is calibrated against):

```csv
cline,cline,cline,2026-07-12 20:33:07.254804+00:00,ca219015-79a4-4bd6-b37e-272fa74bd8c2,64574,6900,736.5466017006597,8.10177526,0,825,20.0,10.0,15.0,12.0,15,0,72.0,false
```

---

## Identity and provenance

| Column | Type | Source | Definition | Status |
|---|---|---|---|---|
| `client_name` | `str` | input | The `owner` value from the input row. | **Settled** |
| `owner` | `str` | input | The `owner` value from the input row, verbatim. | **Settled** |
| `organization` | `str` | ? | Equal to `owner` in the reference row. Unclear whether this is the org login from the API, the input owner echoed, or blank for a user account. | **TBD** |
| `scan_date` | `datetime` | run | One timestamp for the entire run, identical in every row. UTC, rendered as Python `str(datetime)` — `2026-07-12 20:33:07.254804+00:00`. | **Settled** |
| `scan_id` | `UUID` | run | One UUID4 for the entire run, identical in every row. | **Settled** |

`scan_date` and `scan_id` are per-**run**, not per-repository. Two rows from
the same invocation always carry the same pair, which is what makes a stored
result set groupable later.

## Raw metrics

| Column | Type | Source | Definition | Status |
|---|---|---|---|---|
| `stars` | `int` | API | Presumed `stargazers_count`. Not yet confirmed. | **TBD** |
| `forks` | `int` | API | Presumed `forks_count`. Not yet confirmed. Whether this counts direct forks only or the whole network is undecided. | **TBD** |
| `age_days` | `float` | derived | Elapsed days since repository creation. The anchor (`scan_date` vs. wall clock) and the end point are undecided. Reference value `736.5466017006597` is full float precision. | **TBD** |
| `last_update_hours` | `float` | derived | Elapsed hours since the repository was last updated. Undecided whether "updated" means `pushed_at` (last commit) or `updated_at` (any metadata change, including a star). These differ materially. | **TBD** |
| `closed_issues` | `int` | API | Count of closed issues. The reference row shows `0` for a repository that has many, so the definition is narrower than "all closed issues" — possibly a time window, possibly excluding pull requests, possibly something else. Also the most expensive value to collect, so its definition drives the rate-limit budget. | **TBD** |
| `releases` | `int` | API | Count of releases. Reference value `825`. Undecided whether drafts and prereleases count, and whether tags without releases count. | **TBD** |

## Scores

`total_score` is the sum of the six components. Confirmed against the
reference row:

```
prevalence_score  20.0
stars_score       10.0
forks_score       15.0
maturity_score    12.0
last_update_score 15
trusted_org_bonus  0
                 -----
total_score       72.0
```

All six components are `float`. See the rendering conflict below.

| Column | Type | Driven by | Bands | Status |
|---|---|---|---|---|
| `prevalence_score` | `float` | ? | Reference value `20.0`. The driving input is not yet identified — it is separate from stars and forks, which have their own components. | **TBD** |
| `stars_score` | `float` | `stars` | `64574` → `10.0` | **TBD** |
| `forks_score` | `float` | `forks` | `6900` → `15.0` | **TBD** |
| `maturity_score` | `float` | `age_days` | `736.5466017006597` → `12.0` | **TBD** |
| `last_update_score` | `float` | `last_update_hours` | `8.10177526` → `15` | **TBD** |
| `trusted_org_bonus` | `float` | `is_trusted_org` | `false` → `0` | **TBD** |
| `total_score` | `float` | the six above | Arithmetic sum. | **Settled** |
| `is_trusted_org` | `bool` | ? | Rendered lowercase (`false`, not Python's `False`). The source of the trusted list, and whether it matches on `owner` or `organization`, are undecided. | **TBD** |

### Resolved: all six components are floats

The reference row writes `last_update_score` and `trusted_org_bonus` without a
decimal point (`...,12.0,15,0,72.0,false`) while every other score has one.
That was raised and **decided in favour of the types**: all six components are
`float`, and all six render with a decimal point.

The reference row above is therefore superseded in those two columns. Current
output for the same repository is:

```csv
cline,cline,cline,2026-07-12 20:33:07.254804+00:00,ca219015-79a4-4bd6-b37e-272fa74bd8c2,64574,6900,736.5466017006597,8.10177526,0,825,20.0,10.0,15.0,12.0,15.0,0.0,72.0,false
```

Every other column is unchanged. A test asserts this exact line, so the
rendering cannot drift silently.

### An observation worth preserving

The two reference data points for stars and forks run in opposite directions to
naive intuition:

- 64,574 stars → **10.0**
- 6,900 forks → **15.0**

Far more stars scored *lower* than far fewer forks. So the two components are
not on a shared scale, and neither is a simple monotonic function of raw count
with the same banding. Any proposed formula has to reproduce both of these
points before it is worth discussing.

### Open scoring questions

1. Are the bands **step functions** (thresholds, e.g. `>= 1000 → 15.0`) or
   **continuous** (e.g. logarithmic with a cap)?
2. Is each component individually capped? What is the maximum `total_score`?
3. Which components are integers and which are floats? The reference row shows
   `12.0` but `15` and `0`, which suggests the types genuinely differ rather
   than being a formatting artifact.
4. What score does a repository that could not be fetched receive — zeros, or
   is the row excluded from scoring entirely?

## Formatting rules

| Concern | Rule | Status |
|---|---|---|
| Booleans | Lowercase `true` / `false`, not Python's `True` / `False` | **Settled** |
| `scan_date` | Python `str(datetime)`, UTC, microsecond precision | **Settled** |
| Floats | Full precision, no rounding (`736.5466017006597`) | **Settled** |
| Score columns | All six components are `float` and render with a decimal point | **Settled** |
| Unfetchable repositories | Identity columns filled, everything else empty | **Settled** |
| Unknown vs. zero | Empty field in CSV, `null` in JSON — never `0` | **Settled** |

## Unfetchable repositories

A repository that 404s, is private, or otherwise cannot be read still produces
a row — the output has one row per accepted input row regardless of what
happened to it.

Filled: `client_name`, `owner`, `organization`. Empty: every metric and every
score, written as an empty field rather than a zero. Zero is a legitimate
value — a repository really can have zero releases — so using it for "not
known" would make the two indistinguishable in the output.

```csv
cline,cline,cline,<scan_date>,<scan_id>,,,,,,,,,,,,,,
```

The run exits with a **non-zero** status; see
[`adr/0004-exit-code-scheme.md`](adr/0004-exit-code-scheme.md).

`scan_date` and `scan_id` **are** filled on such a row. They are per-run
values, assigned before any repository is fetched, so they are known regardless
of what happened to this one — and a row that cannot be attributed to a run is
of little use once results are stored. How the run identity is represented in
the v0.3.0 schema, where it is likely a foreign key rather than a repeated
column, is a separate question deferred to that design.

## Field selection

The caller may choose which columns to emit; selecting none emits all of them.

Selection drives collection, not just rendering: **if no selected column needs
a given API call, that call is not made.** On a large inventory this is the
difference between fitting inside the token's budget and not.

The dependency is not one-to-one, because scores derive from raw metrics. A
selection of `stars_score` alone still requires the repository fetch that
provides `stars`; a selection that excludes everything derived from releases
skips the release request entirely. The mapping from selected column to
required request is the table below, and it cannot be finalised until the
metric definitions are.

## JSON output

An array of objects, one per row, using the same field names as the CSV
columns. `scan_date` and `scan_id` repeat in every object, exactly as they
repeat in every CSV row — the two formats carry the same information in the
same shape, so a consumer can switch between them without a mapping layer.

## Rate-limit cost per repository

Every metric that needs its own API call adds to the per-repository request
cost, which multiplies across the inventory and determines whether a run fits
inside the token's budget. This table is what the pre-flight check will be
computed from, and it cannot be completed until the definitions above are.

| Metric | Endpoint | Requests per repository | Status |
|---|---|---|---|
| `stars`, `forks`, `organization`, dates | `GET /repos/{owner}/{repo}` | 1 | **TBD — confirm** |
| `closed_issues` | depends on the definition | ? | **TBD** |
| `releases` | depends on whether a count needs pagination | ? | **TBD** |

A repository with 825 releases cannot be counted from one page, so `releases`
alone may cost several requests unless a cheaper route exists. This is the kind
of thing that turns a 400-repository run from feasible into impossible, so it
is being settled before implementation rather than after.

## Related documents

- [`ERROR-CATALOG.md`](ERROR-CATALOG.md) — every error code
- [`CLI-REFERENCE.md`](CLI-REFERENCE.md) — how to produce this file
- [`ROADMAP.md`](ROADMAP.md) — what is deferred and to which version
- [`L1.md`](L1.md), [`L2.md`](L2.md), [`L3.md`](L3.md) — requirements
