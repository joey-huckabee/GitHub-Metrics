# GitHub-Metrics — Metric Definitions and Scoring

The authoritative definition of every column a scan produces: where the value
comes from, how it is calculated, and how it is scored.

A run writes two artifacts, and they are for different things.

`githubmetrics.csv` is the **comparable table**: one row per accepted
reference, twenty columns, for ranking and comparing a portfolio. Its grain is
the repository and its shape is fixed, which is what makes two runs diffable
and a column sortable.

`<owner>/<repoid>.json` is the **detail record for one repository**: that same
row, in the same order and under the same names, followed by the contributor
block — who contributed, where they are, and the totals over them.

Every CSV column is a document key, which is what lets the two be joined on
the run that produced them. The block is what the document is *for*, and it
belongs there rather than in the table for the same reason: a contributor
array has no representation at the table's grain.

**Every metric and score column is Settled.** Six fields are not: the four
contribution aggregates that depend on `foreign` and `adversarial`, and those
two contributor fields themselves. All six are emitted as empty or `null` and
nothing computes them.

The rule that got the rest here still applies: nothing is implemented that is
not defined here first — a metric with an undocumented definition is not
comparable across repositories, which is the whole point of collecting it. A
row marked **TBD** means the definition is still being argued about and no code
depends on it. Reserving a column is not implementing the metric: a `null`
publishes no number, which is exactly why the six can ship ahead of their
definitions without making a claim.

## Output shape

One row per accepted input row, in input order. Column order is fixed and is
the order below.

```csv
name,owner,organization,url,scan_date,scan_id,stars,forks,age_days,last_update_hours,closed_issues,releases,prevalence_score,stars_score,forks_score,maturity_score,last_update_score,trusted_org_bonus,total_score,is_trusted_org
```

Reference row (the worked example this document is calibrated against):

```csv
cline,cline,cline,https://github.com/cline/cline,2026-07-12 20:33:07.254804+00:00,ca219015-79a4-4bd6-b37e-272fa74bd8c2,64574,6900,736.5466017006597,8.10177526,0,825,20.0,10.0,15.0,12.0,15.0,0.0,72.0,false
```

A document is these twenty keys, in this order, then the six keys of the
contributor block. The columns come first and complete, which is the whole
relationship between the two artifacts.

---

## Identity and provenance

| Column | Type | Source | Definition | Status |
|---|---|---|---|---|
| `name` | `str` | API, input fallback | The repository's name. GitHub's value when the repository was read; the `repoid` from the input row otherwise. | **Settled** |
| `owner` | `str` | input | The `owner` value from the input row, verbatim. | **Settled** |
| `organization` | `str` | API | The owning organisation's login, or **empty** when the repository is owned by an individual account. | **Settled** |
| `url` | `str` | derived | The repository's canonical `https://github.com/owner/name` address. Built from the two columns above rather than reported separately, so it is filled even for a repository that could not be read. | **Settled** |
| `scan_date` | `datetime` | run | One timestamp for the entire run, identical in every row. UTC, rendered as Python `str(datetime)` — `2026-07-12 20:33:07.254804+00:00`. | **Settled** |
| `scan_id` | `UUID` | run | One UUID4 for the entire run, identical in every row. | **Settled** |

`scan_date` and `scan_id` are per-**run**, not per-repository. Two rows from
the same invocation always carry the same pair, which is what makes a stored
result set groupable later.

### `name`, formerly `repo_name`, formerly `client_name`

The first column was named `client_name` and was read — by this tool, wrongly
— as another copy of the owner. It is the **repository's name**, and the
mistake was possible only because the reference row is `cline/cline`, where the
owner and the repository are spelled the same. The column was renamed to say
what it holds, and then shortened again: `repo_name` said `repo` twice in a
file whose every row is a repository, and the per-repository JSON this column
set also feeds calls it `name`. One word, one spelling, both artifacts.

Together with `owner` it is what makes a row identifiable: without it a file of
four hundred rows cannot say which repository any of them describes except by
position in the input.

**It is verified rather than echoed.** The query asks GitHub for the name, so
the column carries the name the repository has now rather than the name the
inventory believes it has. That costs nothing — it is a field in a query
already being sent — and it matters because a rename redirects silently, in
exactly the way a transfer does:

| | |
|---|---|
| the inventory says | `pypa/pep517` |
| GitHub reports | `pypa/pyproject-hooks` |

The entry still resolves, so nothing fails; without asking, every row would
agree with the file and disagree with GitHub. A rename is logged at WARNING,
and a difference of case alone is not a rename.

When the repository could not be read there is no reported name, so the column
falls back to the `repoid` from the input row. It is the one column that has an
answer in every case, which is what a failed row needs most.

### Organisation and owner

A GitHub repository is owned by either a **user account** or an
**organisation**, and the API says which: `owner.__typename` is exactly `User`
or `Organization`. The column follows from it.

| Owner kind | `owner` | `organization` |
|---|---|---|
| Organisation | `urllib3` | `urllib3` |
| Individual | `torvalds` | *(empty)* |

Empty is the answer for a personally owned repository, not a missing value.
It is the only place a row records that the repository belongs to a person,
and echoing the user's login into the column would make the two kinds of
ownership indistinguishable downstream - every aggregate by organisation would
silently acquire one bucket per individual maintainer.

**A repository that has moved is refused, not collected.** GitHub redirects a
rename and a transfer silently, so a stale entry still resolves:

| | inventory says | GitHub reports |
|---|---|---|
| transferred | `tiangolo/fastapi` | `fastapi/fastapi` |
| renamed | `pypa/pep517` | `pypa/pyproject-hooks` |

Nothing fails, and that is the danger. Collecting it would produce a row in
which every number is correct, measured against a repository the inventory does
not name, with nothing in the output to say the reference was stale — an error
that survives review because it looks like data.

So the reference is treated as defective. The row is emitted with its identity
columns and **no measurements**, exactly as for a repository that could not be
read; the run warns with the current `owner/name` so correcting the list is a
copy and paste; and it exits **4** rather than clean. The code is
[`GM-COL-003`](ERROR-CATALOG.md).

```
WARNING  pypa/pep517 has been renamed to pypa/pyproject-hooks. GitHub still
         redirects, so the reference resolves, but it no longer names the
         repository the inventory asked for. No data collected; update the
         inventory to pypa/pyproject-hooks
```

Comparison is case-insensitive, because GitHub names are: `PyPA/virtualenv` and
`pypa/virtualenv` are the same reference, and refusing a row over its spelling
would reject a working entry.

## Raw metrics

### Direct forks, not the network

`forkCount` counts the repositories forked **directly from this one**. GitHub
also tracks a *network*: a fork of a fork of the original, and so on down, all
grouped under the root repository. The network is the larger number, and for a
popular project it can be much larger, because a fork made from someone else's
fork counts there and not here.

Direct forks is the right measurement for this tool. A fork taken from another
fork says something about that fork's visibility rather than about the original
project, so counting it would credit a repository for attention it did not
attract. It is also the number the bands were calibrated against.

| Column | Type | Source | Definition | Status |
|---|---|---|---|---|
| `stars` | `int` | API | GraphQL `stargazerCount`. | **Settled** |
| `forks` | `int` | API | GraphQL `forkCount` — **direct forks only**, not the whole fork network. | **Settled** |
| `age_days` | `float` | derived | `created_at` to `scan_date`, in days, at full precision. See [Last update](#last-update) for the anchor. | **Settled** |
| `last_update_hours` | `float` | derived | `updated_at` to `scan_date`, in hours. See [Last update](#last-update). | **Settled** |
| `closed_issues` | `int` | API | Closed issues, all time, **excluding pull requests**. See [Closed issues](#closed-issues) below. | **Settled** |
| `releases` | `int` | API | Distinct versions, which is the tag count. See [Releases](#releases). | **Settled** |

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
| `prevalence_score` | `float` | closed issues, releases | `20 × max(issue weight, release weight)`. See [Prevalence score](#prevalence-score). | **Settled** |
| `stars_score` | `float` | `stars` | `10 × weight`. See [Stars and forks](#stars-and-forks). | **Settled** |
| `forks_score` | `float` | `forks` | `15 × weight`. See [Stars and forks](#stars-and-forks). | **Settled** |
| `maturity_score` | `float` | `age_days` | `15 × weight`. See [Maturity](#maturity). | **Settled** |
| `last_update_score` | `float` | `last_update_hours` | `15 × weight`. See [Last update](#last-update). | **Settled** |
| `trusted_org_bonus` | `float` | `is_trusted_org` | `10.0` when trusted, `0.0` otherwise. See [Trusted organisations](#trusted-organisations). | **Settled** |
| `total_score` | `float` | the six above | Arithmetic sum. See [Total score](#total-score). | **Settled** |
| `is_trusted_org` | `bool` | policy | Owner is on the trusted list, matched case-insensitively. See [Trusted organisations](#trusted-organisations). | **Settled** |

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

### Resolved scoring questions

**The bands are step functions.** Every one of them is a threshold table, and
the tables are data rather than branches so that each can be tested across its
whole domain. `github-metrics bands` prints them.

**Each component is capped at its own weight, and `total_score` at 85.**

| Component | Maximum |
|---|---|
| `prevalence_score` | 20.0 |
| `forks_score` | 15.0 |
| `maturity_score` | 15.0 |
| `last_update_score` | 15.0 |
| `stars_score` | 10.0 |
| **Subtotal** | **75.0** |
| `trusted_org_bonus` | 10.0 |
| **`total_score`** | **85.0** |

A component is `weight x points` where the weight is at most 1.0, so each one
is capped by construction; the total is the sum of the six and cannot exceed
85 without one of the six weights changing. 85 is therefore a ceiling that
falls out of the design rather than a clamp applied over it, which is the
better kind: a clamp hides a weight that has drifted, while a ceiling that is
also a sum makes the drift visible as a total that no longer reaches 85. A
test asserts the six values still sum to it.

**Every score is a float, rendered with a decimal.** The reference row shows
`12.0` beside `15` and `0`, which reads like a type difference and is not —
it is a formatting artifact of the spreadsheet the row was copied from. All six
components and the total are floats.

**A repository that could not be fetched keeps its row and scores nothing.**
Not zero: empty. Zero is a legitimate score for a repository that was measured
and found wanting, so using it here would put an unreadable repository and a
genuinely inactive one in the same bucket, and every average over the file
would absorb the difference. See *Unfetchable repositories* below for what the
row contains and what the run does about it.

## Contributor block

The part of a document that has no CSV equivalent. Six keys, after the twenty
columns: the contributor array, then five aggregates over it.

| Key | Type | Source | Definition | Status |
|---|---|---|---|---|
| `contributors` | `array` | API | One entry per collected contributor, most commits first. See [Contributor records](#contributor-records). | **Settled** |
| `contribution_total` | `int` | derived | Sum of `contribution` over every contributor **collected** for this repository. See [What the total counts](#what-the-total-counts). | **Settled** |
| `foreign_contribution` | `int` | derived | Commits by contributors where `foreign` is true. | **TBD** |
| `adversarial_contribution` | `int` | derived | Commits by contributors where `adversarial` is true. | **TBD** |
| `foreign_percent` | `float` | derived | `foreign_contribution` as a percentage of `contribution_total`. | **TBD** |
| `adversarial_percent` | `float` | derived | `adversarial_contribution` as a percentage of `contribution_total`. | **TBD** |

**These are not CSV columns.** They exist only for a repository whose
contributor list was read, and such a repository is exactly the one that gets a
document; a repository whose list failed gets a row and no document. Putting
them in the CSV would add five columns that are empty for precisely the rows
with nothing to explain them, and would change a twenty-column contract for
data the CSV has no grain for.

The four **TBD** keys are emitted as `null`, for every repository, and no code
computes them. They are present so the shape does not change when the
definitions land — a `null` publishes no number and makes no claim, whereas a
`0` would assert that a repository has no foreign contribution, which is an
assertion about named people that nothing has measured.

### What the total counts

`contribution_total` is the sum over the contributors this tool collected, not
over every contributor the repository has ever had. The list is truncated at
**25 accounts**, ranked by commits descending, which is what
`DEFAULT_CONTRIBUTOR_LIMIT` fixes.

That is a narrower thing than the name suggests, so it is stated here rather
than left to be discovered. The truncation is logged at DEBUG when it bites,
which is what makes a total reconcilable against GitHub's own figure later.
The limit is fixed rather than configurable: GitHub ranks the list by
contribution, so the first 25 accounts carry the great majority of a
repository's commits, while the tail is long enough that collecting all of it
would let one very large repository set the cost of an entire run.

`contribution_total` is `0` for a repository that genuinely has no
contributors. It is never "unknown": a repository whose contributor list could
not be read (`GM-COL-005`) produces no document at all, so a zero in a
document always means the list was read and was empty. That is why this one is
a plain number while every metric column is optional.

### Contributor records

`contributors` is an array ordered by commits descending. Each entry:

| Field | Type | Source | Definition | Status |
|---|---|---|---|---|
| `scan_id` | `UUID` | run | The run that collected this record, identical to the document's. | **Settled** |
| `scan_date` | `datetime` | run | As above. | **Settled** |
| `github_id` | `str` | API | The account's numeric GitHub id, **as a string**. Stable across a rename, which the login is not. See [Why the id is a string](#why-the-id-is-a-string). | **Settled** |
| `name` | `str` | API | The account's display name, falling back to its login when it publishes none. | **Settled** |
| `organization` | `str` | API | The account's self-reported company, or `""` when it publishes none. | **Settled** |
| `location` | `str` | API | The account's self-reported location, verbatim, or `null` when it publishes none. Free text; GitHub does not validate it. | **Settled** |
| `internal_address` | `object` | derived | What `location` resolved to. See [Addresses](#addresses). | **Settled** |
| `contribution` | `int` | API | Commits attributed to this account in this repository. | **Settled** |
| `foreign` | `bool` | policy | Whether the contributor is foreign to the United States. | **TBD** |
| `adversarial` | `bool` | policy | Whether the contributor is adversarial. | **TBD** |

`foreign` and `adversarial` are emitted as `null`. Both attach a judgement to a
named person, and neither has a definition anywhere in this repository, so
neither is computed. `null` rather than `false`, because `false` is the
judgement, not the absence of one.

An account that is deleted or suspended between reading the contributor list
and reading its detail is still recorded, carrying its login as `name` and
nothing else resolved. Its `contribution` is a real measurement of this
repository, so dropping the record would quietly reduce `contribution_total`.

### Why the id is a string

Not because Python needs it to be. Python integers are arbitrary-precision, so
there is no width to check on this side and no 64-bit guard to write.

The ceiling is downstream. A JSON number greater than 2<sup>53</sup> - 1 loses
precision in any consumer backed by an IEEE-754 double — JavaScript and
everything built on it — and it does so **silently**, yielding an id that is
close to the right one rather than an error. GitHub account ids are comfortably
below that today and nothing guarantees they stay there. A string has no such
ceiling, and nothing arithmetic is ever done with an account id.

### Addresses

`internal_address` decomposes a location into components, and it has three
states that must stay distinguishable:

| State | Looks like | Means |
|---|---|---|
| Never asked | every field `null` | the account published no location |
| Asked, unresolved | `query` set, the rest `null` | the account published something no gazetteer recognises |
| Matched | `query`, `formatted_address` and the components set; `""` for components the match lacks | resolved |

The middle state is why `query` exists. Without it, an account publishing
`she/her` would be indistinguishable from one publishing nothing, and those
are different facts about that account.

**The components are the match's own, and are never reverse-geocoded.** A
forward lookup with `addressdetails` returns the components of the place that
matched. Looking the coordinates back up instead - forward geocode, then
reverse geocode the result - manufactures precision that was never in the
data: `United States` resolves to the country's centroid, and reverse-resolving
that point returns a county in Kansas, so every contributor who names a country
acquires a state and a county they have nothing to do with. A residency rule
keyed on `state` or `county` would then be reading invented values. It also
doubles the request count on the slowest part of a run.

The `""` in the third state is a measurement too: a country-level match
genuinely has no city, and recording that is not the same as never having
looked.

| Field | Type | Nominatim source |
|---|---|---|
| `query` | `str` | the location as this contributor published it, whitespace-normalised |
| `formatted_address` | `str` | the single-line rendering of the match |
| `street` | `str` | `road`, else `pedestrian`, `residential` |
| `house_number` | `str` | `house_number` |
| `suburb` | `str` | `neighbourhood`, else `suburb`, `borough`, `city_district`, `quarter` |
| `post_code` | `str` | `postcode` |
| `state` | `str` | `state`, else `province`, `region` |
| `state_code` | `str` | the **coarsest** `ISO3166-2-lvl*` present |
| `state_district` | `str` | `state_district` |
| `county` | `str` | `county` |
| `country` | `str` | `country`, in English |
| `country_code` | `str` | `country_code`, lower case |
| `city` | `str` | `city`, else `town`, `village`, `hamlet`, `locality`, `municipality` |
| `internal_location` | `object` | `{ latitude, longitude }` |

#### Joining two APIs that do not agree

GitHub hands over one free-text string with no schema; Nominatim answers with a
component map whose **keys vary by country**. Three rules bridge them, and each
fixes a way the obvious mapping is wrong.

**Names are pinned to English.** Nominatim returns place names in the local
language unless asked otherwise, so without this `country` would read `Germany`
for one contributor and `Deutschland` for another, and any rule keyed on it
would apply to some accounts and not others — silently, since both values are
correct. **A residency rule should key on `country_code` regardless:** it is
ISO 3166-1 alpha-2, it has no language, and it does not change when a country
is renamed in one dataset and not another.

**A settlement is named by its kind.** Nominatim reports `city` only for places
it classes as cities, so the first key present wins. The chains are ordered
US-first, because residency against the United States is the question the
contributor block is collected to answer: `town` is ubiquitous in New England
and the Mid-Atlantic, `village` and `hamlet` cover New York's tiers, and
`locality` catches unincorporated places that have a name and no government.
`borough` is in the *suburb* chain for New York, where an address in Brooklyn
comes back as `city` "City of New York" with `borough` "Brooklyn" - the city is
still New York, so the borough sits below it rather than replacing it.

The non-US keys - `municipality`, `quarter`, `city_district` - are last rather
than absent. Tuning for US addresses cannot mean only US addresses: dropping
them would leave the field empty for exactly the contributors a foreign
residency rule exists to identify.

**The ISO 3166-2 level is not fixed.** A US state arrives as `ISO3166-2-lvl4`,
but the first-level subdivision sits at a different administrative level in
other countries, so a hard-coded `lvl4` finds nothing for them. Every
`ISO3166-2-lvl*` key is collected and the **coarsest** taken, because a lower
administrative level is a larger area — a finer one would be a county inside
the subdivision rather than the subdivision `state` names.

#### What is asked, and what is recorded

The location is whitespace-normalised and stripped of invisible format
characters before it is looked up, and the **cache is keyed case-insensitively**.
`San Francisco, CA`, `san francisco, ca` and `San  Francisco,  CA` are one
place typed three ways; Nominatim answers them identically, so folding them
into one lookup turns three seconds into one. At one request per second over a
few hundred repositories that is the difference between a run of hours and a
much longer one.

`query` still records the spelling **this** contributor published, so a record
continues to describe the account it belongs to. What appears in the logs is
the normalised form, because that is what was asked and one line per distinct
location is the useful cardinality.

**Coordinates are `null` when unresolved, never `0.0`.** 0,0 is a real
position in the Gulf of Guinea, so a zeroed pair plots as Null Island and
reads as data rather than announcing itself as a failure. This is the same
rule as *Unknown vs. zero* below, applied to a number that has no other way to
say "nothing here".

## Formatting rules

| Concern | Rule | Status |
|---|---|---|
| Booleans | Lowercase `true` / `false`, not Python's `True` / `False` | **Settled** |
| `scan_date` | Python `str(datetime)`, UTC, microsecond precision | **Settled** |
| Floats | Full precision, no rounding (`736.5466017006597`) | **Settled** |
| Score columns | All six components are `float` and render with a decimal point | **Settled** |
| Undefined keys | The four TBD aggregates render as `null` in every document | **Settled** |
| Coordinates | `null` when unresolved, never `0.0` — 0,0 is a real place | **Settled** |
| Unfetchable repositories | Input and scan columns filled, everything else empty, run exits 4 | **Settled** |
| Unknown vs. zero | Empty field in CSV, `null` in JSON — never `0` | **Settled** |

## Unfetchable repositories

A repository that 404s, is private, or otherwise cannot be read still produces
a row — the output has one row per accepted input row regardless of what
happened to it.

Filled: `name`, `owner`, `url`, `scan_date`, `scan_id` — everything that
comes from the input row, from the scan, or from those two together. Empty: `organization`, every metric and
every score, written as an empty field rather than a zero. Zero is a legitimate
value — a repository really can have zero releases — so using it for "not
known" would make the two indistinguishable in the output.

`organization` is empty here for the same reason as the metrics: only the API
can report it, and the API reported nothing. It is the one identity-looking
column a failed read cannot fill.

```csv
cpython,python,,https://github.com/python/cpython,<scan_date>,<scan_id>,,,,,,,,,,,,,,
```

**And it produces no document.** A CSV row is positional — one row per
accepted input row — so omitting one would shift what every later row means.
A directory has no positions, so an absent file says "named, not measured" on
its own. Writing the document anyway would publish an empty contributor array
and a `contribution_total` of zero, which nothing reading a directory of
documents could tell from a repository that genuinely has no contributors.

The same applies, one step later, to a repository that was read successfully
but whose contributor list was not (`GM-COL-005`): the row keeps every
measurement it collected — it is a complete row, because no column of it is
derived from contributors — and no document is written. The absent file is the
only record that the contributor half failed, which is why the run also warns.

**The run reports which repositories failed and exits non-zero.** A row of
empty measurements is not a result, and a file that contains some of those
without saying so would be read as a set of very low scores. The failures are
named on stderr and the exit status is 4 — degraded, output still written —
so a pipeline can tell "some rows could not be collected" from "the run
produced nothing".

The run exits with a **non-zero** status; see
[`adr/0004-exit-code-scheme.md`](adr/0004-exit-code-scheme.md).

`scan_date` and `scan_id` **are** filled on such a row. They are per-run
values, assigned before any repository is fetched, so they are known regardless
of what happened to this one — and a row that cannot be attributed to a run is
of little use once results are stored. How the run identity is represented in
the v0.3.0 schema, where it is likely a foreign key rather than a repeated
column, is a separate question deferred to that design.

## Field selection

The caller may choose which columns the **tabular** artifact emits; selecting
none emits all of them.

The tabular artifact has twenty columns and that number does not vary: the
contributor block is a document key set, not a set of columns, so no run
produces a different header from another.

Selection is a rendering filter and nothing more. It was specified as a
rate-limit lever as well — a column nobody asked for would need no data, so
the selection would decide which API calls a run must make. That promise is
withdrawn rather than carried forward: every column a row needs comes from one
GraphQL query costing one point, so there is no call left to skip and no quota
to save. The lever was designed when collection was assumed to be several REST
calls per repository.

**Selection does not reach the documents.** A document with columns missing
would stop being the row it has to join with, which is the one property the
pair of artifacts exists to have.

## JSON output

Two different things carry JSON, and they are not the same artifact.

`--format json` renders the **tabular** artifact as an array of objects, one
per row, using the same field names as the CSV columns. `scan_date` and
`scan_id` repeat in every object, exactly as they repeat in every CSV row.

The **documents** are always JSON and always written, one per repository, at
`<owner>/<repoid>.json`. Each is the twenty columns in canonical order followed
by the contributor block. `--format` does not affect them: there is no CSV form
of a nested contributor array, and no console rendering of four hundred
documents.

## Rate-limit cost per repository

A scan spends from **two** separate hourly budgets, and `check_budget` refuses
a run that does not fit either one before collecting anything.

| What | Route | Cost per repository | Status |
|---|---|---|---|
| `closed_issues` | GraphQL `issues(states: CLOSED) { totalCount }` | 1 point | **Settled** |
| `stars`, `forks`, `organization`, dates | GraphQL, same query | 0 additional | **Settled** |
| `releases`, `tags` | GraphQL, same query | 0 additional | **Settled** |
| contributor list | REST `/repos/{owner}/{repo}/contributors` | 1 request | **Settled** |
| contributor detail | GraphQL, one aliased document | 1 point | **Calculated, not measured** |

**Two GraphQL points and one REST request per repository.** GraphQL binds
first, at 2,500 repositories an hour against REST's 5,000.

Because GraphQL bills per query rather than per field, folding every count into
one document costs one point per repository regardless of how many metrics are
collected — against roughly 1,600 repositories an hour for the three-request
REST equivalent, which would still be wrong.

The metrics query's cost is confirmed against the live API:
`collect.repository` sends one document returning eleven fields and the
response reports a cost of **1**. The contributor-detail query's cost is
**calculated rather than measured** — it follows from GitHub's documented
formula for a document of single-object selections — and remains a debt to
settle with a real token.

The condition that keeps both cheap is that neither asks for `nodes`. A
`nodes` selection prices a query by the number of objects it could return, so
a repository with 825 releases would cost more than one with 3, and the
cheapest route would become the most expensive one for exactly the largest
projects. Tests assert that neither query contains `nodes`.

### Why the contributor detail is not REST

The REST contributors payload is a minimal account object — login, id, avatar
— and carries no name, company or location. Reading those through PyGithub
completes each account lazily, which is **one REST request per contributor**:
26 per repository at the collection limit, so a 200-repository inventory
exhausts REST's 5,000-per-hour budget before it finishes. Aliasing the
accounts into one GraphQL document makes a repository's cost independent of
how many contributors it has.

### Geocoding is the slow part

Nominatim's usage policy permits **one request per second**, and that is
enforced in `geo.py` rather than trusted to politeness: the penalty for
exceeding it is the service blocking the user agent, which fails every later
run rather than the one that misbehaved.

That makes the geocoder, not the GitHub API, the pace of a large scan. The
per-run cache is what makes it survivable — contributor locations repeat
heavily across a portfolio, so the cost is the number of *distinct* locations
rather than the number of contributors — but a first run over a large
inventory is measured in hours.

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

**Status: settled.** The combination rule is fixed; the ceilings are not revisited.

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

### Decision

**`prevalence_score = 20 x max(issue weight, release weight)`**, with the issue
signal excluded when the tracker is disabled.

```python
release_weight = score_releases(distinct_versions)
if issues_enabled:
    weight = max(score_closed_issues(closed_issues), release_weight)
else:
    weight = release_weight
prevalence_score = 20.0 * weight
```

This removes the cliff, removes the branch, and makes the score non-decreasing
in both inputs - closing an issue or cutting a release can never lower it.

### It is a gate, and that is the intended reading

Both ceilings are cleared by any established project, so the component reports
a constant 20.0 for mature repositories and does no ordering work among them.
That is accepted deliberately: the question it answers is **"has this project
shipped anything and been maintained at all"**, and for a mature project the
answer is yes.

Where it earns its keep is the bottom of the range. Below 500 closed issues and
80 versions it still separates:

| Project shape | Closed issues | Versions | Score |
|---|---|---|---|
| Nothing at all | 0 | 0 | 2.0 |
| Just started | 25 | 2 | 4.0 |
| Getting going | 160 | 25 | 12.0 |
| Established | 600 | 100 | 20.0 |

**The consequence to be aware of:** for a portfolio of established projects,
`prevalence_score` contributes a fixed 20 points to every row. It cannot help
rank them, and the ordering of `total_score` is decided entirely by the other
five components. If ranking mature projects is wanted later, the change is to
the band *boundaries* - the 500 and the 80 - not to the weights, which stay
0.0 to 1.0 either way.

### When a signal is absent

The issue signal is weighed only when there is issue evidence. Two situations
produce none, and both are treated the same way - the signal is excluded and
the release weight stands alone:

- the issue tracker is switched off, or
- the tracker is on but nothing has been closed.

This matters because the two band tables disagree about zero. The closed-issue
table floors at **0.1** for any count below 20, zero included; the release
table scores 0 as **0.0**. Weighing an empty tracker would therefore score a
project that has shipped nothing and closed nothing at 2.0, placing it above a
project with no evidence at all - which is backwards.

Excluding an absent signal keeps that project at **0.0**, matching the rule
this replaces. One closed issue, or one version, is evidence and scores 2.0.

| Closed | Versions | Tracker | Score |
|---|---|---|---|
| 0 | 0 | on | **0.0** |
| 0 | 0 | off | **0.0** |
| 1 | 0 | on | 2.0 |
| 0 | 1 | on | 2.0 |
| 600 | 0 | on | 20.0 |
| 600 | 0 | off | **0.0** |

The last row is the case that shows why the tracker flag is collected: a
repository can accumulate closed issues and later have its tracker switched
off. The issues remain, but they are no longer a signal this score will read.

### Earlier recommendation, now adopted

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

---

## Trusted organisations

**Status: `is_trusted_org` is settled. `trusted_org_bonus` is not** - the
points a trusted owner earns have not been supplied.

### The list

| Owner | Institution |
|---|---|
| `spring-projects` | `VMware` |
| `google` | `Google` |
| `hibernate` | `Red Hat` |

Matching is case-insensitive, because GitHub account names are.

### This is policy, not measurement

Every other value in the output is something GitHub reports. This one is a
judgement, and the API cannot supply it:

| Owner | GitHub org name | `company` field | Trusted-list value |
|---|---|---|---|
| `spring-projects` | Spring | null | **VMware** |
| `hibernate` | Hibernate | null | **Red Hat** |
| `google` | Google | null | Google |

The values are the **institution behind** the organisation. GitHub knows
`spring-projects` as "Spring"; that VMware stands behind it is editorial
knowledge held by this project. The `company` field is null for all three, so
there is no API route to it either.

Two consequences follow. The list must be maintained by hand, and it must be
replaceable without a code change - an analysis that trusts a different set of
institutions is a different analysis, not a different program. The registry
accepts an explicit mapping today; a configuration source is the natural next
step.

### The institution names are the product

These strings end up in a report, so their spelling matters. They are written
as the institutions write them - "Red Hat", not "Redhat". An earlier revision
carried a trailing colon on `VMware:` and an unspaced `Redhat`; both were
transcription slips and are corrected. A test asserts the spellings and that no
value ends in punctuation.

### The bonus

`trusted_org_bonus` is **10.0** for an owner on the list and **0.0** for every
other. A flat award, not a band: every other component scales a points budget
by a 0.0-1.0 weight because its input is a count that varies, whereas trust is
a yes-or-no judgement with nothing to interpolate between.

The reference row carries `is_trusted_org` false and a bonus of 0, for a
`total_score` of 72. The same repository under a trusted owner would score
**82**.

### What that means for a ranking

Ten points is a large award - half of what prevalence pays - and it applies to
a **three-entry list**. For a typical FOSS inventory almost every row scores 0
here, so like `prevalence_score` this component varies for very few
repositories.

That is worth knowing rather than worrying about. Two of the six components now
contribute little to ordering a portfolio of mature projects: prevalence
because it saturates, and this one because the list is short. The ranking is
carried by stars, forks, maturity and last-update. Whether that is the intended
balance is a calibration question, and the levers are the list's length and the
band boundaries rather than the weights.

### Resolved: `organization` does not come from this map

It comes from the API. The two are separate things that happened to look alike
in the reference row, where `organization` reads `cline` and the owner is also
`cline`.

The trusted map's values are *institution* names - `spring-projects` maps to
`VMware`, not to `spring-projects` - so if the column were sourced from it, a
trusted repository would carry `VMware` and every untrusted one would carry
nothing. It carries the owner's login instead. See **Organisation and owner**
under *Identity and provenance*.

---

## Last update

**Status: the scoring is settled. What the input measures is not.**

`last_update_score` is `15 x weight`, where the weight comes from how many
hours have passed since the repository was last updated. Smaller is better
here, unlike every other table.

### Scoring bands

| Hours since update | In years | Weight | Points |
|---|---|---|---|
| 0 - 438 | <= 0.05 | 1.0 | 15.0 |
| 439 - 876 | <= 0.1 | 0.9 | 13.5 |
| 877 - 2,190 | <= 0.25 | 0.8 | 12.0 |
| 2,191 - 4,380 | <= 0.5 | 0.6 | 9.0 |
| 4,381 - 8,760 | <= 1 | 0.4 | 6.0 |
| 8,761 - 26,280 | <= 3 | 0.2 | 3.0 |
| more than 26,280 | > 3 | 0.0 | 0.0 |

Roughly: touched in the last 18 days earns full marks, untouched for three
years earns nothing.

The reference row carries `last_update_hours` of 8.10177526 and
`last_update_score` of 15, which the top band reproduces.

### What changed, and what did not

**The bands are unchanged.** A test sweeps every whole hour from zero to five
years against a transcription of the original chain and asserts the weights
match. Three things around them are fixed:

1. **A band that had lost its weight.** The chain ended `> 0.05 year -> 1`
   followed by `<= 0.05 year -> 1`. Both sides produced 1.0, so the edge could
   not change an answer - sweeping 0 to 40,000 hours found no input where
   removing it mattered. The intended weight for that band was **0.9**, and it
   is restored.

   This is the only change that alters output. A repository last updated
   between 18 days and five weeks ago now scores 0.9 rather than 1.0, and 13.5
   points rather than 15. A test sweeps five years of hourly inputs and asserts
   that the differing hours are **exactly 439 to 876** - the change is surgical,
   not merely intended.
2. **A negative input scored full marks.** Hours since an update cannot be
   negative from a correct measurement, but clock skew between GitHub and the
   local machine can produce one, and `<= 0.05 year` accepted it silently as
   "just updated". It is now reported and scored as zero hours.
3. **The bounds are exact integers.** `0.1 * 8760` is not exactly 876 in
   binary floating point.

### What the input measures

Both settled, and both apply to `age_days` as well.

**Which timestamp?** GitHub reports two, and they are not close together:

| Repository | `pushed_at` | `updated_at` | Difference |
|---|---|---|---|
| `urllib3/urllib3` | 99.67 h | 28.43 h | **71.2 h** |
| `pypa/virtualenv` | 48.06 h | 7.63 h | **40.4 h** |
| `torvalds/linux` | 1.64 h | 0.11 h | 1.5 h |
| `cline/cline` | 0.65 h | 0.69 h | 0.05 h |

**`updated_at` is the one used.** `pushed_at` moves only when code is pushed
to a branch; `updated_at` also moves when repository metadata changes - a
description edit, a topic, a settings change. `updated_at` is therefore the
broader reading of "something happened here", which is what this metric takes
activity to mean.

The consequence to be aware of: a repository whose description was edited last
week but whose last commit was two years ago reports as fresh. `pushed_at` is
collected alongside and costs nothing, so the narrower reading remains
available if that trade ever needs revisiting.

**Both are anchored to `scan_date`**, the single instant recorded once per run.

The implementation this replaces measured at fetch time. Its own reference row
shows the cost: an `age_days` of 736.5466017006597 against a `created_at` of
`2024-07-06T07:28:10Z` implies a "now" of `20:35:16`, **129 seconds after** the
`scan_date` of `20:33:07` printed in the same row.

That made rows within one file incomparable. On a forty-minute run the last
repository's elapsed times were measured against an instant forty minutes later
than the first's - a systematic bias, not noise, with later rows always looking
older and staler - and re-running the same inventory in a different order
changed its numbers.

A fixed anchor removes both, and lets a reader recompute the arithmetic by
hand, because `scan_date` is a column in the row.

**Archived rows will not reproduce exactly.** The same repository scanned the
same second now reports an `age_days` smaller by however long the run took to
reach it - 129 seconds in the reference row's case. A test pins that difference
so it is a recorded consequence rather than a later mystery.

---

---

## Maturity

`maturity_score` is `15 x weight`, from `age_days`. Older is better, to a
ceiling of four years.

| Age | Weight | Points |
|---|---|---|
| under 3 months | 0.0 | 0.0 |
| 3 - 6 months | 0.2 | 3.0 |
| 6 months - 1 year | 0.4 | 6.0 |
| 1 - 2 years | 0.6 | 9.0 |
| 2 - 3 years | 0.8 | 12.0 |
| 3 - 4 years | 0.9 | 13.5 |
| 4 years or more | 1.0 | 15.0 |

The reference row's `age_days` of 736.5466017006597 is a little over two years
and scores 12.0, which this reproduces.

### A units defect, corrected

The chain opened with `if age < 0.25`, comparing the age in **days** against a
threshold the rest of the chain applied to **years**:

```python
age_in_years = age / 365
if age < 0.25:              # days
    age_weight = 0
elif age_in_years < 0.5:    # years
    age_weight = 0.2
```

So the "too young to score" band covered ages below 0.25 *days* - six hours -
rather than below 0.25 *years*. Every repository between six hours and three
months old scored 0.2 instead of 0.0, which credited a repository created
yesterday with three points of maturity.

A test sweeps six years of daily ages and asserts the weights that change are
**exactly days 1 through 91**.

The chain also ended `< 5 years -> 1.0` followed by `>= 5 years -> 1.0`. Both
produce 1.0, so the five-year edge decided nothing. Unlike the equivalent case
in [Last update](#last-update) no weight had gone missing - the progression had
already reached its maximum at four years - so this is a plateau, and it is now
written as one.

---

## Stars and forks

Two counts, two budgets, and two tables that agree below 90 and diverge above
it.

| | Stars | Forks |
|---|---|---|
| Points | 10 | 15 |
| Full marks at | 300 | 150 |

| Count | Stars weight | Forks weight |
|---|---|---|
| under 5 | 0.0 | 0.0 |
| 5 - 9 | 0.1 | 0.1 |
| 10 - 19 | 0.2 | 0.2 |
| 20 - 29 | 0.3 | 0.3 |
| 30 - 39 | 0.4 | 0.4 |
| 40 - 49 | 0.5 | 0.5 |
| 50 - 69 | 0.6 | 0.6 |
| 70 - 89 | 0.7 | 0.7 |
| 90 - 109 | 0.8 | 0.8 |
| 110 - 149 | 0.8 | **0.9** |
| 150 - 299 | 0.9 | 1.0 |
| 300 or more | 1.0 | 1.0 |

The reference row's 64,574 stars score 10.0 and its 6,900 forks score 15.0.

### Two defects in the fork function

**It had no `return` statement.** It assigned a weight into a local and fell
off the end, returning `None` for every input, so
`15 * score_forks(forks)` raised
`TypeError: unsupported operand type(s) for *: 'int' and 'NoneType'`. The fork
score could never have been produced.

**Its terminal branch assigned 0.1, not 1.0.** Supplying the missing return
alone would have made a repository with 110 forks score **below** one with 109
- 0.1 against 0.8 - so more forks would have meant a lower score. A test now
asserts the weight never decreases as forks rise.

`score_stars` needed neither fix. Its chain was complete, total and monotone;
only the negative-count guard is new.

### Where the 0.9 band went, and why

The fork chain ran `< 110 -> 0.8` and then straight to its terminal branch,
with no 0.9 anywhere. The band is restored at **150**, for three reasons:

- It preserves every threshold the original had, adding one rather than moving
  any.
- 150 is already a boundary in the star table, so the two tables share one
  vocabulary of thresholds - 5, 10, 20, 30, 40, 50, 70, 90, 110, 150, 300 -
  rather than introducing a number that appears nowhere else.
- It keeps the ceiling reachable by a genuinely well-used project.

That last point is worth the measurement behind it. Across twelve well-known
Python repositories the median fork-to-star ratio was **0.186**, ranging from
0.05 to 0.47:

| Repository | Stars | Forks | Ratio |
|---|---|---|---|
| `astral-sh/ruff` | 49,397 | 2,370 | 0.05 |
| `tiangolo/fastapi` | 101,939 | 9,831 | 0.10 |
| `bokeh/bokeh` | 20,436 | 4,261 | 0.21 |
| `python/cpython` | 75,290 | 35,306 | 0.47 |

At that median, the star ceiling of 300 corresponds to about **56 forks**. So a
fork ceiling of 150 is roughly three times harder to reach than the star
ceiling in equivalent terms, and the original's 110 would have been harder
still. The asymmetry is deliberate: a fork takes more than a click, so it is
the stronger signal and is scored on a stricter scale and a larger budget.

**This is the one value chosen here rather than supplied.** It is a single
entry in a table if it should be something else.

## Total score

```
total_score = prevalence_score
            + stars_score
            + forks_score
            + maturity_score
            + last_update_score
            + trusted_org_bonus
```

A plain sum of six components, confirmed against the reference row:

```
20.0 + 10.0 + 15.0 + 12.0 + 15.0 + 0.0 = 72.0
```

Each component is a points budget scaled by a 0.0-1.0 weight, except
`trusted_org_bonus`, which is a flat award because trust is a yes-or-no
judgement with nothing to interpolate between.

| Component | Budget | Settled? |
|---|---|---|
| `prevalence_score` | 20 | yes |
| `stars_score` | 10 | yes |
| `forks_score` | 15 | yes |
| `maturity_score` | 15 | yes |
| `last_update_score` | 15 | yes |
| `trusted_org_bonus` | 10 | yes |

**Every budget is now known, and they sum to 85.** A repository at full marks
on all six scores 85; the reference row's 72 is 85 less the 10-point trusted
bonus it did not earn and the 3 points its maturity band left on the table.

## Related documents

- [`ERROR-CATALOG.md`](ERROR-CATALOG.md) — every error code
- [`CLI-REFERENCE.md`](CLI-REFERENCE.md) — how to produce this file
- [`ROADMAP.md`](ROADMAP.md) — what is deferred and to which version
- [`L1.md`](L1.md), [`L2.md`](L2.md), [`L3.md`](L3.md) — requirements
