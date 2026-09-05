# GitHub-Metrics — Roadmap

What is planned, and what is deferred. Items permanently out of scope are
**not** here — they are non-requirements in [`L1.md`](L1.md).

This file records intent, not commitment. An item listed here has been thought
about and deliberately scheduled; that is different from being promised.

## Where things stand

**Merged:**

- Inventory ingestion from `owner,repoid` CSV, with full validation, a stable
  error taxonomy, lenient and strict modes, and concurrent multi-file reads
- `github_metrics.sources`: slugs, GitHub URLs and CSV inventories, mixed
  freely, resolved in input order with repetitions removed across all of them.
  URL input was scheduled for v0.2.0 and arrived early, because it fell out of
  giving every command the same inputs
- `github-metrics validate`, the offline check — formerly `ingest`
- `github-metrics scan`, the release deliverable: collection over an
  inventory, concurrently, with a rate-limit pre-flight, into
  `githubmetrics.csv` **and** one JSON document per repository
- Contributor collection, folded into `scan` rather than a command of its own.
  `github-metrics contributors` is retired
- A three-level requirements tree traced to tests, checked in CI

**Merged, output half of v0.1.0:** the `SoftwareRow` column definition, the CSV,
JSON and console renderers, field selection, destination resolution and the run
identity. These do not depend on how a metric is calculated - only on which
columns exist - so they were built while the definitions were being settled.

**Settled:** every column in [`METRICS.md`](METRICS.md) now reads Settled, and
the collection layer that was blocked on those definitions is built. The rule
that got it there still holds — nothing is implemented before it is defined
there.

**Not being adjusted for this release:** `prevalence_score` saturates at 20.0
for any mature repository, and `trusted_org_bonus` is 0 for nearly every row
given a three-entry list. Two of the six components therefore do little to
separate a portfolio of mature projects, and the ranking is carried by stars,
forks, maturity and last update. This is a known and accepted property of
v0.1.0, not an oversight; the levers are band boundaries and the length of the
trusted list, and neither is being pulled now.

---

## v0.1.0 — `githubmetrics.csv`

**Released 2026-08-30.** Kept here as the record of what that release covered
and why; v0.2.0 changes several of these decisions, and says so where it does.

### The `metrics` sub-command, renamed `scan` in v0.2.0

**Delivered.** Replaced `repo`, which was scaffolding collecting a different
set of fields. Collects metrics for every repository named by the input and
writes `githubmetrics.csv`.

It shipped as `metrics` and is `scan` from v0.2.0, because the per-repository
JSON turned out to be the same row plus a contributor block, so one command
produces both under one scan identity. See
[ADR-0005](adr/0005-one-scan-command-and-per-repository-json.md).

- **Input:** any number of sources, mixed — slugs, GitHub URLs and CSV
  inventories, exactly as `validate` takes them. Directories are not walked.
  The earlier plan restricted this to one CSV file, or `--owner` with
  `--repoid`; both were dropped in favour of one input vocabulary shared by
  every command, since the alternative makes the user remember which command
  wants which form.
- **Output:** one row per accepted input row, **in input order**.
- **Destination:** a file named by `--output`. When only a directory is given,
  the file is written there as `githubmetrics.csv`. With no `--output` at all,
  the report goes to the console.
- **Console format:** a **vertical** table, one block per input row. Twenty
  columns do not fit across a terminal, so the columns become rows.
- **Field selection:** the caller selects which columns to emit; selecting none
  emits all. Columns come out in canonical order whatever order they are asked
  for, so two runs wanting the same columns produce identical headers.

  This was specified as a rate-limit lever as well — selection would skip the
  API calls no selected column needed. It is a rendering filter only, and that
  is now the right answer rather than a shortfall: every column a row needs
  comes from **one** GraphQL query costing **one** point, so there is no call
  left to skip and no quota to save. The lever was designed when collection was
  assumed to be several REST calls per repository. Making selection cheaper
  than one point is not possible, so the promise is withdrawn rather than
  carried forward.
- **JSON:** an array of objects using the CSV column names, available both as a
  file and to the console.
- **Checking first:** `validate` is a command of its own rather than a
  `--dry-run` flag. Checking a 400-row inventory before spending any quota is
  worth keeping, and a separate command is the form that can be handed to
  someone who has no token at all.
- **Duplicates:** dropped, with a warning naming them.
- **Invalid rows:** rejected, as today.
- **Unfetchable repositories:** identity columns filled, metrics and scores
  empty, non-zero exit. See [`METRICS.md`](METRICS.md).
- **Exit codes:** severity-ordered 0–6, per
  [`adr/0004-exit-code-scheme.md`](adr/0004-exit-code-scheme.md).

### Package layout

The current flat module set grows into packages, shaped by where things are
headed rather than by what exists today:

```
github_metrics/
  sources/     where a repository gets named - slugs, URLs, CSV inventories
  model/       ScanIdentifier, RepoMetaData, the output row
  collect/     API access, rate limiting, concurrency
  analysis/    the scoring calculations
  output/      CSV, JSON and console rendering
```

`sources/` and `output/` are separate rather than one `csv_io` package because
they diverge immediately: `sources/` already reads URLs as well as CSV, and
v0.3.0 adds a database *destination*. Nothing named `csv_io` would still be
honest, and it stopped being honest sooner than expected.

### Rate limiting

Integrated with the per-repository concurrency, not bolted beside it.

**Delivered.**

- **On exhaustion: fail the run.** Deliberately blunt for this release.
  Configurable behaviour is v0.2.0.
- **Pre-flight budgeting is on by default.** One point per repository against
  the quota remaining, refusing a run that cannot finish. It is a comparison
  rather than an estimate, because the per-repository cost is exactly one.
  Making it optional is v0.2.0.
- **No reserve buffer.** The budget runs to zero, so a full hourly quota
  collects exactly 5,000 repositories.
- **Fixed concurrency**, eight workers. Quota-adaptive counts are v0.2.0.

### Library use

The package must be usable as a dependency, so that a larger project can
collect metrics in the background rather than shelling out to the CLI.

No separate facade or wrapper API: a caller imports the classes and functions
directly and instantiates them. The CLI becomes one caller among others rather
than the only way in, which means every capability the CLI has must be reachable
without it — including output selection, rate-limit configuration and the
scan identity.

Collection is synchronous. A caller wanting it in the background runs it in
their own thread or task; the package does not impose a concurrency model on
the program embedding it.

### Documentation

- [`METRICS.md`](METRICS.md) — field definitions and scoring bands
- [`USER-GUIDE.md`](USER-GUIDE.md) — worked examples of **every** sub-command
  and parameter
- Library usage documented alongside the CLI

---

## v0.2.0 — contributor collection, and rate-limit tuning

**Released 2026-09-03.** The contributor dataset shipped. The rate-limit knobs
did not and move to v0.3.0, where the conditional-request work they depend
on also lives.

Two things ship deliberately incomplete, and both are visible in the output
rather than hidden: `foreign` and `adversarial` are emitted as `null`, along
with the four aggregates derived from them, because neither has a definition
in `METRICS.md` and nothing is computed before it does. The shape is fixed so
that it does not change when the definitions land.

### Contributor collection

**Built.** The shape is [`example.json`](example.json): the twenty
`SoftwareRow` columns, then the contributor block. One JSON document per
repository, at `githubmetrics/<owner>/<repoid>.json` unless `--output` says
otherwise, lower-cased throughout, and written only for a repository that was
fully collected. See
[ADR-0005](adr/0005-one-scan-command-and-per-repository-json.md) for why each
of those is what it is.

**Merged:**

- `repo_name` renamed `name`, and `url` added as a column after `name`, `owner`
  and `organization`. The CSV and the documents share one set of key names, so
  the two artifacts join without a translation table
- `metrics` renamed `scan`, so one run is one scan and the command shares a
  word with the `scan_id` and `scan_date` it stamps
- `contributors` removed, and folded into `scan`. **No flag governs which
  artifacts a run writes** - it writes both, always. The flagged design was
  considered and rejected: it buys one state nothing else expresses, and in
  exchange what a run produced stops being readable from the command
- The contributor block — the contributor array and the five aggregates over
  it — carried by the document. `githubmetrics.csv` is unchanged at twenty
  columns: the table is the comparable record, the document is one
  repository's detail record, and the two join on the row and the scan
- Contributor collection itself: the ranked list from REST, every account's
  detail from one aliased GraphQL document, and locations resolved to a
  fourteen-field address through Nominatim
- `check_budget` extended to both currencies. A scan costs 2 GraphQL points and
  1 REST request per repository, so GraphQL binds first at 2,500 repositories
  an hour
- `--geocode` and `--contributors N` removed. Geocoding is unconditional; the
  limit is `DEFAULT_CONTRIBUTOR_LIMIT`

**Blocked on definitions**, per the rule that nothing is implemented before
`METRICS.md` defines it:

- `foreign` — foreign to the United States. The rule that applies it is coming
  from Joey as code
- `adversarial` — no agreed rule yet. Also coming as code

Neither has a definition anywhere in the repository today, and both attach a
judgement to a named person, so neither is computed. Both are **emitted as
`null`**, along with the four aggregates that depend on them
(`foreign_contribution`, `adversarial_contribution`, `foreign_percent`,
`adversarial_percent`), so that the shape does not change when the definitions
land. Reserving a column is not implementing the metric: a `null` publishes no
number and makes no claim.

**Open, and worth revisiting once there is a real run to look at:**

- **The cost of the default.** Every scan now pays for contributor pages and
  geocodes. That was the thing the v0.1.0 command split existed to avoid, and
  it is accepted here as the price of one identity per run. If it hurts, the
  lever is `DEFAULT_CONTRIBUTOR_LIMIT`, not a flag
- **Geocoding throughput.** Nominatim permits one request per second, so a
  first run over a large inventory is measured in hours. The per-run cache
  makes the cost the number of distinct locations rather than of contributors,
  but a cache that survived between runs would make re-runs nearly free. That
  belongs with v0.3.0's store
- **The contributor-detail query cost is calculated, not measured.** The
  metrics query's one point was confirmed against the live API; the aliased
  detail query's has not been. The repository's own convention is that a cost
  is measured rather than assumed
- **The probe commands.** `closed-issues` and `releases` exist to check a
  metric definition against real repositories before wiring it in, and every
  metric now reads Settled. Whether they are retired is still open

### Delivered early: GitHub URL input

This was the headline of v0.2.0 and shipped in v0.1.0 instead. It was scheduled
here on the assumption that URL handling was a feature of its own; it turned
out to be a consequence of giving every command the same inputs, which is a
smaller change than a separate URL mode would have been.

What was planned is what was built: a deep link into a file or a line range
still resolves, and a URL that cannot be read as a repository carries its own
code — two of them, `GM-ING-016` for the shape and `GM-ING-017` for a host that
is not GitHub.

The reasoning in
[ADR-0001](adr/0001-two-column-csv-as-the-inventory-contract.md) held up: URL
parsing is kept out of the CSV contract, in one clearly-marked place with its
own codes, rather than spread across every row of an inventory.

Naming several repositories on the command line came with it, since a source
list is a list.

### Rate-limit behaviour made configurable

Each of these is deferred from v0.1.0, where the behaviour is fixed:

- **Exhaustion policy** — currently always fail. Add: wait until reset, or emit
  partial results for what was collected.
- **Pre-flight budgeting** — currently always on. Make it possible to turn off.
- **Secondary rate limits.** GitHub returns 403 with `Retry-After` for burst
  and abuse detection, which is a different mechanism from the primary hourly
  quota. Needs its own backoff with a retry cap.
- **Worker count.** Currently fixed. Open question is whether workers should
  throttle down as remaining quota falls, or hold a fixed concurrency behind a
  shared limiter.
- **Conditional requests.** `ETag` / `If-None-Match` returning 304 does not
  count against the rate limit, which would make re-runs over a stable
  inventory nearly free. Worth having once there is something to re-run.

---

## v0.3.0 — Retiring the metric probes

**Released 2026-09-03.** `github-metrics closed-issues` and `github-metrics
releases` are removed.

They existed so a metric definition could be checked against real repositories
while it was still being argued about — `L2-CLI-005` said so in as many words.
Every definition in `METRICS.md` now reads Settled, so the condition that
justified them no longer holds, and two commands that duplicate what `scan`
collects are two more surfaces to keep correct.

The **columns are untouched**: `closed_issues` and `releases` are Settled
metrics, they are collected by `scan` in the same GraphQL query as everything
else, and they appear in both artifacts exactly as before. What went is the
per-metric command, not the measurement.

`github-metrics bands` survives and is the half that was still earning its
place: the scoring tables stay printable, for every metric, without a token.
`collect.closed_issues` and `collect.releases` stay too, as library API for a
caller that wants one number rather than a row.

`L2-CLI-005`, `L3-CLI-005` and `L3-CLI-006` are retired. Their identifiers are
permanent and are recorded with their conditions in `L2.md` and `L3.md`.

---

## v0.4.0 — The analysis pipeline, and verifying the tests

**Released 2026-09-03.** No behaviour changed for a caller; what changed is
what the project can prove about itself.

- **SonarCloud analysis**, in a workflow that skips rather than fails when its
  secrets are absent, waits for the report to be processed before reading the
  result, and names the branch on every read. Two of those exist because the
  first versions reported a clean project for a branch that had never been
  analysed - a 200 carrying nothing reads exactly like good news.
- **Coverage actually reaches it.** coverage.py wrote paths relative to the
  package, so Sonar would have resolved none of them and reported 0% without
  erroring. CI now greps the report for repository-relative paths before
  scanning.
- **`make mutants`** breaks one documented behaviour at a time and requires the
  suite to notice. Three checks could not fail when it was first run, and
  those are fixed; all thirty are caught now.
- **Address components ordered for US addresses**, and the components are the
  match's own rather than a reverse lookup's - which was manufacturing a state
  and a county for anyone who published a country.

---

## v0.5.0 — Every contributor, and a cache that outlives the run

**Released 2026-09-05.** One change to what is measured, and one to what it
costs - plus the first defect this project's own live API run ever found.

`DEFAULT_CONTRIBUTOR_LIMIT` is `None`: a scan collects **every contributor**
GitHub attributes to an account, rather than the top 25 by commits. The old
limit was inherited from the retired `contributors` command and had never been
chosen for the current design, and it made `contribution_total` - and every
percentage that will be derived from it - a statistic about a sample whose size
was a property of this tool. The downstream residency analysis cannot ask about
a contributor this tool did not collect, and the accounts a 25-cap dropped were
exactly the long tail. [ADR-0006](adr/0006-collect-every-contributor.md).

**Totals from v0.4.1 and earlier are not comparable with totals from this
release.** Nothing in a document records which limit produced it.

That change is only affordable because of the second one: the geocode cache now
**persists between runs**, so a re-run over a stable inventory pays
approximately nothing for the slowest part of a scan.
[ADR-0007](adr/0007-persistent-geocode-cache.md), and the expiry table under
[Carried, and known](#geocoding-has-no-cache-beyond-a-single-run).

What it forced, none of which was optional:

- **The REST list is paginated** and the client sets `per_page = 100`, the
  endpoint's maximum. 25 fitted in PyGithub's default page of 30, which was the
  only reason one repository ever cost exactly one REST request.
- **The GraphQL detail query is chunked** at `DETAIL_CHUNK_SIZE` accounts. Its
  *cost* was never the constraint - the point formula counts connections and
  this query has none - but GitHub terminates any query taking more than ten
  seconds, and several hundred aliased account lookups in one document is not a
  safe bet against that.
- **The budget pre-flight weakens from a guarantee to a floor.** See below; it
  is the one thing here that the project can do less well than before.

### The budget pre-flight is now necessary, not sufficient

`check_budget` used to promise that a run which starts can finish, because the
per-repository cost was exactly known. An unbounded contributor list ends that:
the cost depends on a count nobody has until the request that reveals it has
been spent.

`MIN_POINTS_PER_REPOSITORY` and `MIN_REQUESTS_PER_REPOSITORY` are what a
repository costs at minimum, and the pre-flight refuses a run that cannot
afford even that. Passing no longer means the run will finish.

Inventing an average contributor count and calling the product an estimate was
rejected: it would produce a number that looks like the old guarantee and is
not one. The honest form is a floor that says it is a floor.

This raises the value of the exhaustion policy deferred from v0.2.0 - waiting
for the reset, or emitting partial results, rather than failing the run - since
mid-run exhaustion is now reachable in a way it was not before. That work
stays where it is, but it is no longer merely a convenience.

---

## v0.6.0 — Knowing how good the data is

**Planned.** v0.5.0 made the dataset bigger. This release makes it
**auditable**, which the live run showed matters more.

That run reported 396 contributors and a `contribution_total` of 27,828. Both
correct; both misleading alone. The repository has 3,310 contributor identities
and 32,005 commits, GitHub linked only the first 500 author email addresses,
three of the 396 are bots holding 289 commits, and only 175 published a
location at all. **None of that appears in either artifact**, so a repository
truncated at GitHub's ceiling is indistinguishable from a complete one.

The theme is therefore not more metrics. It is: every number this tool
publishes should carry the bounds within which it is true.

### `statistics.json`, a third artifact

One per scan, beside `githubmetrics.csv`, sharing its `scan_id`. Run-level
facts plus a per-repository array.
[ADR-0008](adr/0008-statistics-json.md) has the full field list; the load-bearing
parts:

- **Completeness.** `commits.total_on_default_branch` against
  `commits.attributed_to_collected`, and the coverage percentage between them
  - 87.0% in the measured run. This is the most important number in the file.
- **Exclusions with reasons**, counted in people *and* commits:
  `anonymous_recoverable_noreply`, `anonymous_no_account`,
  `account_unresolvable`, `bot`, `no_location_published`,
  `location_unresolved`, `geocoder_unavailable`.
- **Bots**: count, commits, and `contribution_excluding_bots`.
  **`contribution_total` itself is not adjusted** - a decision, not an
  oversight. Changing it would be its third redefinition in two releases and
  would bake one judgement into a raw measurement. Both numbers are published
  and the analysis chooses.
- **Concentration**: top-1/5/10 share, bus factor, Gini. Directly answers
  "where does this project's work come from" and costs nothing extra.
- **Geography**: commits and people per `country_code`, distinct countries, and
  `commits_with_unknown_location_percent` - the error bar on every geographic
  claim, and the number the downstream stage needs most.
- **`tool_version`**, which finally puts a tool version in an artifact. The gap
  has been recorded since v0.2.0.

Deliberately absent: `foreign`, `adversarial` and anything derived from them.
That determination is a separate project. This file supplies the denominators
and the unknown-share it needs to bound its own percentages, and asserts
nothing about any person.

### Recovering accounts from no-reply email addresses

GitHub's own no-reply format embeds the account id and login:
`275304381+hakanpak@users.noreply.github.com`. Verified against the API - the
embedded id matches `databaseId` exactly - so this is GitHub's construction
rather than a heuristic.

Measured on the same repository: recovers **767 of the 2,914 anonymous
contributors**, taking coverage from 396 people / 87.0% of commits to
**1,163 people / 90.3% of commits**. The remaining 2,147 publish real
addresses, and GitHub exposes no email-to-user lookup, so no API can resolve
them.

Cost: `anon=1` makes the contributor list 34 pages instead of 4. That may need
to be opt-out on very large inventories.

### `--on-exhaustion {fail,wait,partial}`

An inventory larger than one hour's quota cannot be scanned today.
[ADR-0009](adr/0009-rate-limit-exhaustion-policy.md).

`fail` stays the default so nothing changes silently. `wait` sleeps to the
reset and continues. `partial` collects what fits and stops.

The important half is not the flag but that **a partial run says so in the
data**: exit 9, `budget.incomplete_because_exhausted` in `statistics.json`, and
**a row for every named repository including those never attempted**. Without
that last part a partial CSV is merely shorter, and a shorter file cannot be
told from a shorter inventory.

This also inherits the exhaustion-policy work deferred from v0.2.0. Secondary
rate limits - 403 with `Retry-After` - are a different mechanism and stay
deferred.

### Documentation

- [`API-LIMITS.md`](API-LIMITS.md) - every ceiling, what it costs, and the
  ways around it, with the measured figures. Includes the ones that do **not**
  work, so they are not retried.
- [`SCAN-PROCESS.md`](SCAN-PROCESS.md) - the run end to end, written to be read
  adversarially: every place a value can be wrong, absent, or mean something
  other than it appears to, plus a numbered list of known deficiencies.

### Open, and needing a decision

**How are maintainers identified?** The weakest part of the plan.
`GET /repos/{o}/{r}/collaborators` requires push access and is unavailable for
any repository you do not own. `CODEOWNERS` is public and authoritative but
often absent. Public org membership is opt-in and is not maintainership. Top-N
by commits is a proxy, and calling a proxy "maintainers" is the kind of
invented value this project refuses elsewhere.

Proposal: report `maintainers.source` (`codeowners`, `org_public_members`,
`unavailable`), never fall back to a proxy, and say `unavailable` honestly.
Whether "are the maintainers and their work fully captured" is answerable then
depends on the repository, and the file should say which.

---

## v0.7.0 — Persistence

Capture results in **SQLite**, behind an interface that allows the store to be
swapped for **PostgreSQL** later without changing the collection code. One
schema for both artifacts: a document is the row plus its contributor block, so
splitting repository and contributor persistence into separate versions - as an
earlier draft of this roadmap did - would design the same join twice.

The schema has to be designed before it is written, not after. `scan_id` and
`scan_date` already exist as per-run identifiers precisely so that stored rows
can be grouped by the run that produced them.

Points to settle:

- Whether history is append-only (every scan retained) or last-value-wins
- Whether the CSV output remains primary, becomes an export of the database, or
  both
- How a metric definition change is recorded, so old and new rows are not
  silently compared
- **How a stored row is attributed to a tool version.** Nothing in the output
  carries one: `RepositoryMetrics.tool_version` did until v0.2.0 removed that
  type with the `contributors` command, and `SoftwareRow` has no equivalent. A
  row is attributable to a *run* and not to the code that made it, which
  matters more once rows outlive the release that produced them. Adding a
  column is a change to the output contract and wants an ADR.
- **Folding the geocode cache in.** v0.5.0 shipped it as a JSON file rather
  than waiting for this store, because unbounded contributor collection was
  unusable without it. This store will already hold addresses, so the cache
  belongs here rather than beside it - and a JSON file stops being the right
  shape well before it stops working. The trigger, and the reasoning behind
  it, is below.

### When the geocode cache should become a table

A single JSON file is parsed in full at startup and rewritten in full at the
end of a run. That is right while it is small and wrong once it is not, so the
boundary is recorded rather than left to be noticed when a run starts feeling
slow.

Measured against the real `GeocodeCache`, filled with fully-populated
city-level matches - the worst case, since a real cache holds many
country-only matches and misses, which are smaller:

| Entries | File size | Save | Load | Resident |
|---|---|---|---|---|
| 1,000 | 0.49 MB | 14 ms | 102 ms | 1.2 MB |
| 5,000 | 2.46 MB | 58 ms | 468 ms | 5.3 MB |
| 20,000 | 9.89 MB | 245 ms | 1.9 s | 20.2 MB |
| 50,000 | 24.80 MB | 677 ms | 4.8 s | 51.1 MB |
| 100,000 | 49.64 MB | 1.3 s | 9.7 s | 101.9 MB |

An entry is about **510 bytes**. Resident memory is 2.1x the file, and loading
costs roughly four times what saving does.

**Review at 10 MB, about 20,000 distinct locations. Move by 50 MB.**

**Load time forces the move, not memory** - which is the opposite of what the
first estimate assumed, and the reason these numbers were measured rather than
reasoned about. Every run pays the parse in full before it does anything: two
seconds at 20,000 entries, ten at 100,000, whether the run needs forty
locations or all of them. That tax lands hardest on precisely the small re-runs
the cache exists to make fast. Memory is mild by comparison at 2.1x, and the
atomic whole-file rewrite costs a quarter of the load.

The move is **into [v0.7.0](#v070--persistence)'s store, not a database of its
own**, and SQLite answers the binding constraint directly: a run reads the keys
it needs and parses nothing else, so start-up stops scaling with the cache.

For scale, a 200-repository inventory with unbounded contributors produces
somewhere around eight to fifteen thousand distinct locations - so a serious
portfolio lands near 4-8 MB with a half-second load, inside the JSON regime and
within sight of the review point. A program of a few thousand repositories
crosses it.

**That last paragraph is the estimated one.** How many distinct locations an
inventory actually yields has not been observed against a real run, and belongs
with the other things below that have never been checked against the live API.
Everything in the table is measured.

---

## Deferred, and possibly not ours

### `foreign` and `adversarial`

Emitted as `null`, along with the four aggregates derived from them
(`foreign_contribution`, `adversarial_contribution`, `foreign_percent`,
`adversarial_percent`). Nothing in this repository computes them and nothing
ever has - checked against the whole history, not only the current tree.

**A separate repository processes the documents this tool writes**, and may be
where those values are filled in. If so they are not deferred here at all, they
belong to that stage, and this tool's job is to leave an unambiguous marker
that they have not been computed. `null` is that marker; `0` would be
indistinguishable from "computed, and none found", which is the reason the
keys are not simply omitted.

Settling this needs a look at that code. Until then the shape is fixed and
nothing here asserts anything about a named person.

---

## Carried, and known

Things this project knows about itself and has not done. Recorded here so a
reader finds them rather than rediscovering them, and so that the ones which
look like defects are not "fixed" back into defects.

### A cost that is calculated rather than measured

`MIN_POINTS_PER_REPOSITORY` is 2 - one metrics query, one detail chunk. The
first was measured against the live API: `collect.repository` sends one
document and the response reports a cost of 1. The second follows from
GitHub's documented cost formula, which prices a query by its connections, and
has never been confirmed. The formula says an aliased document of single-object
`user(login:)` selections has no connections and therefore costs the minimum of
1 however many aliases it carries - which is also why chunking that query for
the ten-second window costs points rather than being free.

This repository's own convention is that a cost is measured rather than
assumed, so this is a departure recorded rather than a rule quietly relaxed. It
matters less than it did, because v0.5.0 already downgraded the pre-flight from
a guarantee to a floor - but it matters in a new way: the floor is now what
decides whether a run is refused outright, so an understated per-chunk cost
makes an unaffordable run look affordable.

**Settling it** is the same one-scan check as before, and now also worth
reading for how the *chunked* detail query is priced against a repository with
several hundred contributors.

One scan of two or three repositories with a real token at `LOG_LEVEL=DEBUG`,
reading the cost the API reports back.

### Nothing has run against the live API

There are 611 tests. Exactly one is marked `integration`, and **both** CI
workflows deselect it, so the only test that touches GitHub has never run in
CI - or anywhere else on record.

The stubs are faithful to what the API is *documented* to return, which is not
the same as what it returns. Four things in particular are assumed rather than
observed: that PyGithub's paginated contributor list slices the way the code
expects, that the aliased GraphQL document comes back keyed as `u0`, `u1`, …,
that Nominatim's component keys appear as mapped, and what a large repository
does to the pace of a run.

**Settling it:** either a scheduled workflow holding a token, or a documented
pre-release check run by hand. The second is cheaper and honest; the first
catches drift in an API nobody controls.

### `DEFAULT_CONTRIBUTOR_LIMIT` is inherited, not chosen

**Settled in v0.5.0.** The limit is gone: `DEFAULT_CONTRIBUTOR_LIMIT` is
`None` and a scan collects every contributor GitHub attributes to an account.
The decision, the alternatives and the ceiling GitHub imposes regardless are in
[ADR-0006](adr/0006-collect-every-contributor.md). Kept here because the entry
records a question that was open for three releases, and because totals from
v0.4.1 and earlier are not comparable with totals from this one.

### The geocoder user agent is generic

`GEOCODER_USER_AGENT` defaults to the string `github-metrics`. Nominatim's
usage policy asks for an agent identifying the application *and* a way to reach
whoever runs it, and the penalty for a generic one is blocking the agent -
which fails every later run rather than the one that earned it.

Setting the environment variable is enough; the default is what is wrong.

### Geocoding has no cache beyond a single run

**Settled in v0.5.0.** The cache persists to disk, and re-runs over a stable
inventory pay approximately nothing for geocoding. It arrived ahead of the
store rather than inside it because
[ADR-0006](adr/0006-collect-every-contributor.md) made unbounded contributor
collection unusable without it - the reasoning that deferred it was sound right
up until the thing it was waiting behind became the thing that needed it.

What expires, and when, is the part worth knowing:

| Outcome | Persisted | Expiry |
|---|---|---|
| Matched | yes | 365 days |
| No match | yes | 30 days |
| Service error | **no** | - |

A matched entry needs no TTL for correctness: places do not move, and
`country_code` - the field a residency rule should key on - is ISO 3166-1
alpha-2 and does not shift when a country is renamed in one dataset before
another. The year is there to pick up gazetteer improvements, not to guard
against staleness. A miss expires sooner because a miss is a statement about
coverage rather than about the place, and OSM coverage grows. A **service
error is never written to disk at all**: persisting one would let a single
outage permanently poison every location it touched, since every later run
would read "unresolved" from the cache and never ask again. That is an error
that looks like data, which is the failure this repository refuses everywhere
else.

Full reasoning in [ADR-0007](adr/0007-persistent-geocode-cache.md).

### Two SonarCloud rules are deliberately answered differently

Both of these read as tidy-ups that were skipped. They were not: applying
either as written reintroduces a defect that was measured, so they are recorded
here rather than left for a future reader to helpfully undo.

- **`python:S7504` on `logger.py`** - "remove this unnecessary `list()` call".
  The call was not unnecessary: `removeHandler` mutates the list being
  iterated, so walking it directly leaves half the handlers attached - four
  become two, measured - and a second `reset_logger` then duplicates every
  record. The code now drains the list with a `while` loop instead, which is
  correct and cannot be mistaken for redundant. **Do not restore direct
  iteration over `logger.handlers`.**
- **`python:S5886` on `Address.with_query`** - "use `dataclasses.replace`".
  `replace` is what this method does, and mypy resolves its type correctly, but
  Sonar models it as returning `DataclassInstance` whatever the annotation
  says. The copy is written out so both checkers can follow it, carrying fields
  across by name so a new field cannot be dropped, with a test comparing the
  copy against the declared fields. **Replacing it with `dataclasses.replace`
  brings the finding back.**

---

## Cross-cutting

**Library usage documentation.** Using GitHub-Metrics as a dependency rather
than a CLI needs worked examples, not just an API listing. Begins in v0.1.0 and
grows with each release.

---

## Later, unversioned

### Output formats for analysis

Parquet, and CSV shapes other than `githubmetrics.csv`, would let results go
straight into pandas without a conversion step. Deferred until the metric set
stabilises — a schema that changes every release is worse than no schema.

### More metrics

Candidates, each of which needs a definition in [`METRICS.md`](METRICS.md)
before it needs an implementation:

- Release cadence and time since last release
- Issue and pull request response latency
- Bus factor / contributor concentration
- Dependency counts from the dependency graph API
- Security policy and advisory presence

The hard part is definition, not collection. "Time to first response" requires
deciding what counts as a response and whose response counts, and a number
without a documented definition is not comparable across repositories.

### GitHub Enterprise validation

`GITHUB_API_URL` already points collection at an enterprise instance, but the
name grammar in `validation.py` encodes github.com's rules. An enterprise
instance may differ. Not addressed until someone needs it, because guessing at
another deployment's constraints would be inventing requirements.

### Non-GitHub forges

GitLab, Codeberg and sourcehut host FOSS too. This would be a substantial
change: `owner,repoid` is a GitHub-shaped identifier, and the inventory
contract would need a forge column. Listed to acknowledge the question, not
because it is planned.

---

## Deliberately not scheduled

Recorded so that "why hasn't this been done" has an answer.

**A configuration file.** Everything configurable today is a handful of
environment variables and command-line flags. A config file would add a
precedence order to reason about for no current benefit. This may change at
v0.2.0, when the rate-limit knobs arrive.

**A plugin system for metrics.** Premature while the metric set is still small
enough to read in one sitting.

**A web UI or dashboard.** This is a CLI and a library. Presentation belongs to
whatever consumes its output.

**Async I/O.** Threads are sufficient for the current workload and are far
easier to reason about. If collection at scale shows threads to be the
bottleneck, that measurement — not the fashion — is what would justify
revisiting it.
