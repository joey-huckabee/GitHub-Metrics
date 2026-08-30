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
| `closed_issues` | `int` | API | Closed issues, all time, **excluding pull requests**. See [Closed issues](#closed-issues) below. | **Settled** |
| `releases` | `int` | API | Distinct versions, which is the tag count. See [Releases](#releases). Counting settled; bands pending. | **Partial** |

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
| `prevalence_score` | `float` | closed issues, releases | `20 × weight`. See [Prevalence score](#prevalence-score). Formula known; the combination rule is being decided. | **Partial** |
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

| Metric | Route | Cost per repository | Status |
|---|---|---|---|
| `closed_issues` | GraphQL `issues(states: CLOSED) { totalCount }` | 1 point | **Settled** |
| `stars`, `forks`, `organization`, dates | GraphQL, same query | 0 additional | **TBD — confirm** |
| `releases` | GraphQL `releases { totalCount }` | 0 additional | **TBD** |

Because GraphQL bills per query rather than per field, folding every count into
one document costs **one point per repository** regardless of how many metrics
are collected. That is 5,000 repositories per hour, against roughly 1,600 for
the three-request REST equivalent - which would still be wrong.

A repository with 825 releases cannot be counted from one page, so `releases`
alone may cost several requests unless a cheaper route exists. This is the kind
of thing that turns a 400-repository run from feasible into impossible, so it
is being settled before implementation rather than after.

---

## Closed issues

**Definition.** The total number of issues in state `CLOSED`, all time,
**excluding pull requests**. Collected in one GraphQL request per repository.

```graphql
repository(owner: $owner, name: $name) {
  hasIssuesEnabled
  closedIssues: issues(states: CLOSED) { totalCount }
  openIssues:   issues(states: OPEN)   { totalCount }
}
```

### Why not REST

The REST API cannot answer this question correctly at any price. Three separate
obstacles, each verified against the live API:

1. **The repository object has no closed-issue count.** It exposes only
   `open_issues_count`, and that number *includes pull requests*. For
   `cline/cline` it reads `1148`, which is 691 open issues plus 457 open pull
   requests.
2. **The issues endpoint returns pull requests too**, with no server-side
   filter. For `cline/cline` that is 3,770 closed issues against 7,001 closed
   pull requests, so a combined count nearly triples the figure and measures
   development throughput rather than issue triage.
3. **Counting by pagination no longer works.** GitHub moved the issues endpoint
   to cursor pagination, so responses carry `rel="next"` but no `rel="last"`.
   Any total derived from the last-page link now returns **1** for every
   repository. This was confirmed directly: PyGithub's
   `repo.get_issues(state="closed").totalCount` returns `1` for `cline/cline`
   and for `pypa/virtualenv` alike.

Obstacle 3 is silent. It produces a plausible small integer rather than an
error, which is why the reference row's `closed_issues` value cannot be trusted
and why this metric is verified against live counts rather than against it.

### Cost

One GraphQL point per repository, out of 5,000 per hour. Only `totalCount` is
requested; asking for `nodes` would page through every issue and make the cost
proportional to the repository's history.

| Route | Requests/repo | Ceiling | Correct? |
|---|---|---|---|
| REST repo object | 1 | 5,000/hr | No - no closed count exists |
| REST issues pagination | 1+ | 5,000/hr | No - includes PRs, and now returns 1 |
| Search API (`type:issue`) | 1 | **30/min = 1,800/hr** | Yes, with index lag |
| **GraphQL** | **1** | **5,000/hr** | **Yes** |

Search was measured within 1 of GraphQL on three repositories - index lag, not
error - but its 30-requests-per-minute ceiling would be the binding constraint
on any inventory over a few hundred repositories.

### Issues disabled versus no issues

`hasIssuesEnabled` is collected alongside the counts because zero closed issues
has two very different causes. A repository with its tracker turned off reports
zero, but that is a fact about its configuration, not about its maintenance -
the project may track its work in a mailing list or another forge entirely.
Scoring the two identically would penalise the second unfairly, so the flag is
carried and a disabled tracker is logged at WARNING.

### Scoring bands

The weight is a 0.0-1.0 multiplier. How it combines into `prevalence_score` is
settled separately.

| Closed issues | Weight |
|---------------|--------|
| 0 - 19        | 0.1    |
| 20 - 49       | 0.2    |
| 50 - 99       | 0.3    |
| 100 - 149     | 0.4    |
| 150 - 299     | 0.6    |
| 300 - 399     | 0.8    |
| 400 - 499     | 0.9    |
| 500 or more   | 1.0    |

The edges are deliberately uneven: the informative range is at the low end. The
gap between 10 and 100 closed issues says a great deal about whether a project
is maintained; the gap between 3,000 and 4,000 says almost nothing.

A count of zero scores 0.1 rather than 0.0, preserving the original behaviour -
having no closed issues costs most of the weight without zeroing the component.

### Defects corrected from the original implementation

Two, both silent, both in `analysis/prevalence.py`:

1. **A count of exactly 500 matched no branch.** The chain ended
   `< 500 -> 0.9` and `> 500 -> 1.0`, so 500 itself returned the initial `0`.
   The bands are now an ordered table with no fallthrough by construction: the
   last band's bound is 500 and anything not below it takes the maximum weight,
   which is exactly `>= 500 -> 1.0`. Every boundary is tested individually.
2. **The count itself was wrong**, per obstacles 2 and 3 above.

Both yield a plausible number rather than an error, which is why the band table
is now data rather than control flow and why the whole domain is swept in tests.

> An earlier revision of this document also listed a misspelled local
> (`cloased_issue_weight`) that would have made the function return `0.0` for
> every input. That was a transcription slip when the original was quoted into
> a conversation, not a defect in the code, and the claim has been withdrawn.

---

---

## Releases

**Status: settled.** Counting, bands and cost are all decided.

### Definition

Two numbers are collected, in one GraphQL query costing one point:

```graphql
repository(owner: $owner, name: $name) {
  releases { totalCount }
  tags: refs(refPrefix: "refs/tags/") { totalCount }
}
```

The scored value is **distinct versions**, which is the tag count.

### Why not `releases + tags`

The original implementation summed them:

```python
repo_meta_data.releases = get_releases(repo) + get_tags(repo)
```

This is what produced the reference row's `825` - a figure matching neither
the release count nor the tag count on its own, which is why it could not be
reconciled earlier.

**Creating a GitHub Release requires a tag**, so releases are a subset of tags.
Measured directly by comparing tag names:

| Repository | Releases | Tags | Release tags present in tag list | Missing |
|---|---|---|---|---|
| `urllib3/urllib3` | 58 | 108 | 58 | **0** |
| `pypa/virtualenv` | 98 | 285 | 98 | **0** |

Adding the two therefore counts every release twice. Worse, the inflation is
not a constant that could be divided out:

| Repository | Releases | Tags | Sum | Distinct | Inflation |
|---|---|---|---|---|---|
| `cline/cline` | 398 | 717 | 1115 | 717 | **1.56x** |
| `urllib3/urllib3` | 58 | 108 | 166 | 108 | **1.54x** |
| `pypa/virtualenv` | 98 | 285 | 383 | 285 | **1.34x** |
| `bokeh/bokeh` | 0 | 151 | 151 | 151 | **1.00x** |
| `torvalds/linux` | 0 | 943 | 943 | 943 | **1.00x** |

The inflation tracks how many tags carry a release, which is a **publishing
workflow preference**, not a measure of how established a project is. Under
the sum, `cline/cline` is rewarded over `torvalds/linux` partly for using
GitHub's release feature.

### Why tags rather than releases

Counting releases alone is the other obvious option, and it fails badly on
real repositories: **`torvalds/linux` has 0 releases and 943 tags**, and
`bokeh/bokeh` has 0 and 151. Both would score zero for a metric meant to
capture how much a project has shipped.

Tags have a second advantage. Draft releases are visible only to a token with
push access, so a release count can differ between two people scanning the
same repository. Tag counts are the same for everyone, which makes the metric
reproducible.

### What is still collected

Both numbers are kept even though only one is scored. The release count
distinguishes a project that publishes formal artifacts from one that only
tags, which is worth having available, and it costs nothing extra to collect.

`legacy_sum` is retained solely so a log line can state what the previous
definition would have reported. A stored score that changes should be
explainable.

### Consequence for the bands

Changing from the sum to the distinct count **lowers every input**, by between
0% and 36% depending on the repository. Whatever bands are chosen have to be
set against the new scale; carrying over thresholds calibrated on the inflated
figures would systematically under-score every project that publishes
releases.

### Scoring bands

| Distinct versions | Weight |
|-------------------|--------|
| 0                 | 0.0    |
| 1 - 4             | 0.1    |
| 5 - 9             | 0.2    |
| 10 - 19           | 0.3    |
| 20 - 39           | 0.4    |
| 40 - 49           | 0.5    |
| 50 - 59           | 0.6    |
| 60 - 69           | 0.7    |
| 70 - 79           | 0.8    |
| 80 or more        | 1.0    |

Unlike the closed-issue chain, this one is **total as written** - the final
branch is `>= 80`, so no input falls between branches. There was no gap to fix.

Two asymmetries are carried over deliberately rather than by oversight, and
both are asserted by tests so a later reader does not "fix" them unknowingly:

- **Zero scores 0.0**, where zero closed issues scores 0.1. A project that has
  never cut a version gets no floor.
- **0.9 never occurs.** The step from 0.8 to 1.0 is double every other step.

### It saturates, and that matters

The top band starts at **80 distinct versions**, which every established
project clears comfortably:

| Repository | Distinct versions | Release weight |
|---|---|---|
| `urllib3/urllib3` | 108 | 1.0 |
| `bokeh/bokeh` | 151 | 1.0 |
| `pypa/virtualenv` | 285 | 1.0 |
| `cline/cline` | 717 | 1.0 |
| `torvalds/linux` | 943 | 1.0 |

The smallest measured is 108, comfortably above the 80 needed for full marks.
Combined with the closed-issue weight, which saturates at 500, this makes
`prevalence_score` a constant for mature projects - see
[Prevalence score](#prevalence-score) for what that does to the component.

## Prevalence score

**Status: the formula is known; how the two signals combine is being decided.**

`prevalence_score` is 20 points multiplied by a 0.0-1.0 weight. The original
rule picks *which* signal supplies that weight:

```python
if closed_issues == 0:
    prevalence_score = 20 * score_releases(releases)
else:
    prevalence_score = 20 * score_closed_issues(closed_issues)
```

Closed issues are the primary signal; releases are a fallback used only when
there are no closed issues at all.

This also explains the reference row. It carries `closed_issues = 0` and
`releases = 825`, so the fallback branch ran, and `prevalence_score = 20.0`
means `score_releases(825)` returned `1.0`.

### The discontinuity

Selecting on `closed_issues == 0` puts a cliff at exactly one closed issue.
Using the reference row's own numbers, where the release weight is 1.0:

| closed issues | original | `max()` |
|---|---|---|
| 0 | **20.0** | 20.0 |
| 1 | **2.0** | 20.0 |
| 19 | 2.0 | 20.0 |
| 50 | 6.0 | 20.0 |
| 400 | 18.0 | 20.0 |
| 500+ | 20.0 | 20.0 |

**A project's score drops from 20.0 to 2.0 when it closes its first issue.**
That is not a judgement anyone would defend on purpose; it is what "fallback"
semantics produce when the fallback is richer than the primary signal.

The property being violated is worth naming, because it should hold for every
component: **the score must be non-decreasing in every input.** Closing an
issue, cutting a release, or gaining a star must never lower a project's
score. Any replacement has to satisfy that.

### Options

**A. Take the higher of the two.**

```python
prevalence_score = 20 * max(score_closed_issues(closed), score_releases(releases))
```

Monotone in both inputs, no cliff, no branch to get wrong, and it preserves
the reference row's 20.0. The cost is saturation: a project strong in either
signal scores full marks, and the other signal then contributes nothing. How
much that matters depends entirely on where `score_releases` saturates - if a
handful of releases reaches 1.0, prevalence becomes 20.0 for almost everything
and stops discriminating.

**B. Fall back only when the signal is unavailable.**

```python
if not issues_enabled:
    prevalence_score = 20 * score_releases(releases)
else:
    prevalence_score = 20 * score_closed_issues(closed)
```

Keeps the original intent - issues are primary - and removes the cliff, since
0 and 1 closed issues now both score 2.0. It uses `issues_enabled`, which is
already collected, and it draws the line where it belongs: a disabled tracker
means the signal is *missing*, whereas zero closed issues is a *measurement*.
The cost is that release activity is invisible for the great majority of
repositories.

**C. Blend both, issues dominant.**

```python
prevalence_score = 20 * (0.75 * score_closed_issues(closed) + 0.25 * score_releases(releases))
```

Monotone, no cliff, no saturation by a single signal, and both always
contribute. The costs are two: the weights need justifying, and it does not
reproduce the reference row - that row would become 6.5 rather than 20.0, so
previously collected data would not be comparable with new data.

### Recommendation

**The saturation concern is now measured, and it is worse than a concern.**

Both signals reach 1.0 on every established project. Closed issues saturate at
500, releases at 80 distinct versions. Measured:

| Repository | Closed issues | Distinct versions | `w_issues` | `w_releases` | Original | `max()` |
|---|---|---|---|---|---|---|
| `cline/cline` | 3770 | 717 | 1.0 | 1.0 | 20.0 | 20.0 |
| `pypa/virtualenv` | 1429 | 285 | 1.0 | 1.0 | 20.0 | 20.0 |
| `urllib3/urllib3` | 1241 | 108 | 1.0 | 1.0 | 20.0 | 20.0 |
| `bokeh/bokeh` | 7511 | 151 | 1.0 | 1.0 | 20.0 | 20.0 |
| `torvalds/linux` | 0 | 943 | 0.1 | 1.0 | 20.0 | 20.0 |

**`prevalence_score` is 20.0 for all five, under both rules.** The component
contributes a constant to `total_score` and does no ranking work at all for
mature projects. It only discriminates below 500 closed issues *and* below 80
versions - that is, among young or small projects.

Two consequences follow.

**The cliff is now mostly unreachable.** The `closed_issues == 0` branch fired
in the reference row only because the closed-issue count was broken and
reported 0. With the count fixed, that branch is reached only by a project
with genuinely no closed issues, such as `torvalds/linux`. Fixing the cliff is
still right, but it is no longer urgent.

**The real question is the ceilings, not the combination rule.** Choosing
between the original rule, `max()`, and a blend changes nothing in the table
above - every one produces 20.0. If `prevalence_score` is meant to rank
projects rather than to gate them, the saturation points have to move: 80
versions and 500 closed issues are both cleared by any established project.

### Recommendation

1. **Adopt `max()`** for the combination. It removes the cliff, removes a
   branch, keeps existing rows comparable, and costs nothing. A disabled issue
   tracker should be excluded from the comparison rather than scored as 0.1,
   since the signal is absent rather than low.
2. **Decide separately what `prevalence_score` is for.** If it is a gate - "has
   this project shipped and been maintained at all" - the current ceilings are
   fine and the constant 20.0 is the intended answer. If it is meant to
   contribute to a ranking, the ceilings need raising, and that is a
   calibration exercise against a real inventory rather than a code change.

## Related documents

- [`ERROR-CATALOG.md`](ERROR-CATALOG.md) — every error code
- [`CLI-REFERENCE.md`](CLI-REFERENCE.md) — how to produce this file
- [`ROADMAP.md`](ROADMAP.md) — what is deferred and to which version
- [`L1.md`](L1.md), [`L2.md`](L2.md), [`L3.md`](L3.md) — requirements
