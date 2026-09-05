# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Nothing yet.

## [0.6.0] - 2026-09-05

**Knowing how good the data is.** v0.5.0 made the dataset bigger; this makes it
auditable, which the first live run showed matters more.

That run reported 396 contributors and a `contribution_total` of 27,828 for a
repository with 3,310 contributor identities and 32,016 commits. Both numbers
correct, both implying a census, and nothing in either artifact saying so — a
repository truncated at GitHub's 500-email ceiling looked identical to a
complete one.

Every number this tool publishes now carries the bounds within which it is
true:

| | Contributors | Commits |
|---|---|---|
| v0.5.0 | 396, reported as 100% coverage | 27,828, unbounded |
| **v0.6.0 default** | **1,132 of 3,310 — 34.2%** | **28,904 of 32,016 — 90.3%** |
| **v0.6.0 `--deep-attribution`** | complete | ~100% |

Four capabilities, in the order they were built.

### Added

- **`statistics.json`, a third artifact**, written beside `githubmetrics.csv`
  and carrying the same `scan_id`. Run-level facts plus a per-repository array
  in input order. [ADR-0008](docs/adr/0008-statistics-json.md), fields defined
  in `docs/METRICS.md`.

  The number it exists for is **commit coverage**. Measured across two
  repositories in one run: `NousResearch/hermes-agent` 27,845 of 32,016
  commits — **86.97%** — against `pypa/virtualenv` at **99.6%**. Both looked
  identically complete before.
- **Commit totals, for free.** `history { totalCount }` folded into the
  existing metrics query. `history` is a connection, so this looked like it
  should cost more; measured, the combined document reports `cost: 1` for a
  1,250-commit repository and a 32,016-commit one alike, because asking a
  connection for `totalCount` alone requests no nodes.
- **Bot identification** from GitHub's own `type: "Bot"` field, not a login
  heuristic. `is_bot` on every contributor record, and counts, commits and
  logins in `statistics.json`. **`contribution_total` deliberately still
  includes them**, with `contribution_excluding_bots` published beside it.
- **Concentration** — top-1/5/10 shares, bus factor and Gini — which answers
  "where does this project's work come from" without naming anyone a
  maintainer.
- **The unknown-location share**, which bounds every geographic claim.
  Measured at **81.66%** for one repository: any national percentage computed
  from it carries an unknown of up to 81 points.
- `tool_version` in an artifact, an open item since v0.2.0.

- **The identity census**, so contributor coverage has a real denominator.
  Until now `identities` defaulted to `collected`, making the fraction
  `395/395` — 100% for a repository whose real coverage is 11.9%. A number that
  overstates its own completeness is worse than no number.

  It costs **one REST request per repository, whatever its size**. The
  contributors endpoint paginates by offset and returns a `rel="last"` link, so
  `per_page=1&anon=1` makes the last page number *be* the total count: 3,310
  for `hermes-agent`, 161 for `pypa/virtualenv`. Walking the list would have
  cost 34 pages and made REST the binding budget for a whole inventory.
- **`contributors.breakdown`**, the four components of that denominator —
  `linked_by_github`, `recovered_from_noreply`, `anonymous_unrecoverable`,
  `unresolvable_accounts` — which **sum to `identities`**, so it can be checked
  rather than trusted. One percentage cannot say whether 12% coverage means
  GitHub's ceiling bit (expected, unfixable) or that accounts are disappearing
  between calls (a defect worth chasing).
- **`contributors.truncated_by_github`**, the one-glance boolean: whether the
  500-email ceiling affected this repository at all.

### Fixed

- **`check_budget` was validating runs against a number that never moves.**
  The REST `/rate_limit` endpoint does not track spend: measured, it reported
  5000 remaining for both budgets while the same token had 4,988 GraphQL points
  and 4,984 REST requests left. The pre-flight read it, so it would have
  accepted a run whose budget was already gone — the exact failure it exists to
  prevent.

  The GraphQL budget is now read from GraphQL's own `rateLimit` field, which is
  authoritative and **free**: a document selecting nothing else is not charged,
  confirmed by issuing it twice and reading the same `remaining`.

### Notes

- **REST spend is published as `null`, not estimated.** The
  `X-RateLimit-Remaining` header is accurate but only the latest is kept, and a
  GraphQL response overwrites it with the GraphQL budget — observed going 4,981,
  then 4,976 after one GraphQL call, then 4,980 after the next REST call. A scan
  interleaves both across eight threads. GraphQL binds first and *is* measured;
  a plausible REST number would be a guess wearing a measurement's clothes.
- **`exclusions[].commits` is `null` for the anonymous tail**, not `0`. The
  census counts people in one request; counting their commits needs every page
  of the list. Zero would claim the tail contributed nothing, which for a large
  repository is false by thousands of commits.
- **Read the two coverage figures together.** For `hermes-agent`, 88% of the
  *people* are missing and 13% of the *work* is — the tail averages 1.4 commits
  each. "Where does this project's work come from" is answered well at 87%;
  "did this person contribute" is not answered at all. `docs/METRICS.md` says
  so where the fields are defined.
- **v0.6.0 is feature-complete.**
- 747 tests, trace matrix 213 of 213.

### Added (`--deep-attribution`)

- **`--deep-attribution`**, which attributes every commit by walking the
  history rather than reading the contributors endpoint. That route is **not
  subject to the 500-author-email ceiling** - measured, 99 to 100 of every 100
  commits come back with an account attached.

  It exists for the question a sample cannot answer. *Where does this project's
  work come from* is answered well at 90% of commits; *is there any adversarial
  contributor here* is not answered at all, because a single one-commit account
  is exactly what a sample omits.

  **Measured cost: one GraphQL point per hundred commits** - 13 for a
  1,250-commit repository, 321 for a 32,016-commit one against the 9 an
  ordinary collection takes. Roughly 35x, growing with commit count rather than
  contributor count. For a watchlist, never an inventory.
- **A recommendation, not an escalation.** Every scan names the repositories a
  sample answered badly, with the price attached:

  ```
  ! NousResearch/hermes-agent: 13.0% of commits (4,171 of 32,016) could not be
    attributed to an account, above the 10% threshold. For complete attribution
    re-run this repository with --deep-attribution (about 321 GraphQL points,
    and as many round trips)
  ```

  `--deep-attribution-threshold` moves that line; 10% is a starting value, set
  where the first measured repository falls above it so the mechanism
  demonstrably fires on a real case. Escalating automatically was rejected:
  turning a 9-point repository into a 321-point one spends an hour of quota on
  a decision the user did not make, and with `--on-exhaustion wait` the default
  it would turn a five-minute run into an overnight one.
- `attribution.method` in `statistics.json`, because the two routes find
  different populations and different totals. **Two runs of one repository by
  different methods are not comparable**, and this is what stops them being
  diffed as though they were.

### Notes

- **`HISTORY_QUERY` is the one query in this package allowed to select
  `nodes`.** Everywhere else that is forbidden, because `nodes` prices a query
  by the objects it could return. Here that pricing is precisely what is being
  bought, and there is no other way to see individual commits.
- **Bots are still detected under deep attribution**, from the reserved `[bot]`
  login suffix rather than the `type` field the history query does not carry.
  Equally authoritative rather than a fallback guess: an account name is
  `^[A-Za-z0-9-]+$`, so a bracket cannot appear in a login anyone chose. Caught
  because the first deep run reported **zero** bots for a repository carrying
  four of them - a number that looked measured and was not.
- `build_contributors` was extracted so both routes share everything after
  "which accounts": the same detail query, the same geocoding, the same record
  shape. Only the population differs.
- `gaps_from_outcome` and `recommend_deep_attribution` moved from `cli` to
  `analysis`, where they belong - the CLI had grown past a thousand lines and
  both were deriving statistics rather than parsing arguments.

### Added (`--on-exhaustion`)

- **`--on-exhaustion {wait,fail,partial}`**, so an inventory larger than one
  hour's budget can be scanned at all. Until now the pre-flight simply refused
  it. [ADR-0009](docs/adr/0009-rate-limit-exhaustion-policy.md).
- **Exit 9**, for a run that stopped early. Its own status because "incomplete
  but usable" differs from every other outcome: the artifacts are well-formed
  and every named repository has a row, but some were never attempted. It sits
  **above** 4, because an unreadable repository still produced everything it
  could while a stopped run has repositories it never looked at.
- **A partial run says so in the data**, not only in the status:
  `budget.incomplete_because_exhausted` in `statistics.json`, and a row for
  every named repository with the unreached ones marked. Without that last
  part a partial CSV is merely shorter, and a shorter file cannot be told from
  a shorter inventory.
- `budget.waits` records how many hourly resets a run slept through, and
  `budget.exhausted` is set even when `wait` recovered from it.

### Changed

- **`scan` now waits for the hourly reset by default instead of refusing.**
  This changes the default behaviour, deliberately, and only for runs that
  previously produced nothing usable: a run inside its budget never reaches the
  policy at all. Defaulting to `fail` optimised for the caller who already
  knows their inventory is too large — exactly the caller who can pass a flag —
  and made the first large scan anyone attempts a refusal.

  `--on-exhaustion fail` restores the old behaviour and is what a CI job with a
  step timeout should pass.
- The pre-flight **warns instead of refusing** under `wait` and `partial`,
  naming the consequence before anything is spent. A run about to sleep for an
  hour should say so while someone is watching.

### Notes

- **Checking the budget costs almost nothing.** Asking the API before every
  repository would add a round trip to a run that already makes several per
  repository, so the guard spends a local estimate and verifies only within
  twenty repositories' worth of the limit. The estimate is deliberately a
  *floor* — it subtracts the per-repository minimum, which a real repository
  exceeds — so it reaches the margin early rather than walking into a wall.
- **One sleep per exhaustion, not eight.** The lock covers the whole
  check-and-act sequence rather than the counter, and the thread that wakes
  re-reads the budget so the others are released against a real number. Waking
  into a still-empty budget waits again: another process may share the token.
- Every wait is bounded at an hour plus a margin. A reset time in the far
  future is a clock disagreement, not a real wait, and sleeping on it would
  hang a run with nothing to explain it.

### Added (no-reply recovery)

- **`--recover-anonymous`** (on by default), which links the contributors
  GitHub left anonymous but whose email carries its own no-reply format,
  `NNN+login@users.noreply.github.com`. That format embeds the account id and
  login, and round-trips against the API, so nothing is guessed.

  Measured on `NousResearch/hermes-agent`:

  | | Contributors | Commits |
  |---|---|---|
  | Without | 395 of 3,310 — 11.9% | 27,845 of 32,016 — 87.0% |
  | **With** | **1,132 of 3,310 — 34.2%** | **28,904 — 90.3%** |

  The remaining 2,150 identities publish real addresses, and GitHub exposes no
  email-to-user lookup, so no API resolves them. Nothing infers a login from a
  display name: that would put a real person's name against work that may not
  be theirs.

  Costs a page per hundred identities — 34 requests for that repository against
  4. `--no-recover-anonymous` turns it off for a large inventory.
- **The anonymous tail's commits are measured when recovery runs**, so
  `exclusions[].commits` stops being `null` for `anonymous_no_account`.

### Fixed (units)

- **The coverage breakdown counted accounts where it should have counted
  identities**, so it summed to 3,282 against 3,310 — the documented invariant,
  quietly broken. GitHub identifies contributors by *author email*, so one
  person with two git configurations is two identities; 765 recovered
  identities collapsed into 737 accounts. `linked_by_github` is now derived as
  the remainder, so the total cannot drift again.

  This also means `coverage_percent` mixes units — accounts over addresses —
  and is a **lower bound** on person-level coverage. `docs/METRICS.md` says so.
- **A box-drawing character in a docstring made vulture skip the file**,
  reporting `'charmap' codec can't encode` as a warning and exiting clean — a
  dead-code check that stopped checking. `CLAUDE.md`'s note that non-ASCII
  source is safe is right about em-dashes (cp1252 has one at 0x97) and wrong
  about characters cp1252 cannot encode at all. Diagrams are ASCII now, and the
  convention says why.

### Fixed (SonarCloud)

The quality gate failed on the census pull request and the merge went through
anyway. Every finding was mine, from this release's own work:

- **`new_coverage` 64% against a threshold of 80.** `collect/census.py` had no
  unit tests and `client.py`'s reworked budget readers had none either — 42%
  covered. Both now have their own suites; `census.py` is at 100% and
  `client.py` at 86%. The census tests pin the `Link` header specifically,
  because reading it slightly wrong fails **silently**: the code falls back to
  counting entries on the page, which at `per_page=1` is a plausible `1`. That
  is exactly the bug that occurred during development.
- **`python:S3776`**, cognitive complexity 19 against 15, in
  `graphql.execute` — which `tolerate_missing` had pushed over. The failure
  classification is now its own function, so the happy path reads in one piece.
- **`python:S5778`** four times and **`python:S9073`** once, in
  `tests/test_graphql_partial.py`: stubs built inside `pytest.raises` blocks, so
  a test would pass if *construction* threw rather than the call under test.
  That is the same defect v0.4.1 fixed fourteen instances of, reintroduced.

## [0.5.1] - 2026-09-05

Documentation, planning and one CI dependency. **No functional code changed** —
`github_metrics/` is untouched, and the 660 tests are the same 660.

### Added

- **[`docs/API-LIMITS.md`](docs/API-LIMITS.md)** — every GitHub and Nominatim
  ceiling, what it costs, and the ways around it. Figures are marked measured
  or documented, and the workarounds that do **not** work are recorded so they
  are not retried.
- **[`docs/SCAN-PROCESS.md`](docs/SCAN-PROCESS.md)** — the run end to end,
  written to be read adversarially: every place a value can be wrong, absent,
  or mean something other than it appears to, ending in ten numbered known
  deficiencies.
- **[ADR-0008](docs/adr/0008-statistics-json.md)** — `statistics.json`, a third
  artifact carrying completeness, exclusion reasons, bot impact, concentration
  and geographic bounds.
- **[ADR-0009](docs/adr/0009-rate-limit-exhaustion-policy.md)** —
  `--on-exhaustion {fail,wait,partial}`.
- **[ADR-0010](docs/adr/0010-optional-commit-history-attribution.md)** —
  `--deep-attribution`, an opt-in walk of commit history for the repositories
  where the contributor list is not enough, with a threshold that recommends it
  rather than escalating automatically.
- **The geocode cache is now documented for users**, not only in an ADR: its
  default location per platform, how to move, disable or clear it, and the fact
  that deleting it loses no measurement.
- **A section in the user guide on how many contributors you actually get**,
  with the measured 396-of-3,310 people against 87% of commits, so the sample
  is understood before a conclusion is drawn from it.

### Changed

- **`--on-exhaustion` will default to `wait`, not `fail`.** Reversed on review
  before implementation. Defaulting to `fail` optimises for the caller who
  already knows their inventory is too large — precisely the caller who does
  not need protecting — and makes the first large scan anyone attempts a
  refusal. This will change the default behaviour of `scan`, and only for runs
  that previously produced nothing usable.
- **Maintainer coverage is dropped from the plan, not deferred.** There is no
  consistent way to determine who a maintainer is: `collaborators` needs push
  access unavailable on third-party repositories, `CODEOWNERS` is authoritative
  but present on a minority, public org membership is opt-in and is not
  maintainership, and top-N-by-commits is a proxy that would be dishonest to
  label. A field absent for most repositories is not comparable across a
  portfolio, which was the only reason to collect it. The concentration figures
  answer the underlying question without naming anyone.
- `ROADMAP.md`: v0.6.0 revised, and v0.7.0 realigned — the persistence schema
  now has a third artifact and a run-level table to hold, and `tool_version` is
  partly answered by `statistics.json`.
- `SonarSource/sonarqube-scan-action` from 6 to 8. v8's breaking change is that
  `skipSignatureVerification` defaults to `false`, so the scanner's OpenPGP
  signature is verified on download.

### Fixed

- **A CI check that could not fail, found while verifying that bump.**
  Dependabot pull requests do not receive repository secrets, so the SonarCloud
  workflow took its documented skip path and **reported success without
  scanning** — a green tick that was no evidence the bumped action worked. The
  bump was verified on the push to `main` instead, where the scan really runs
  (`skipSignatureVerification: false`, signature downloaded, `✓ GPG signature
  verification passed`). The workflow now says so in a comment, beside the
  coverage-path guard and the quality-gate read that exist for the same reason.
  That is the fourth instance in this project of a check reporting success when
  it could not actually check.

## [0.5.0] - 2026-09-05

Every contributor, and a geocode cache that outlives the run.

The two go together. Collecting every contributor rather than the top 25 is
what the downstream residency analysis needs, and it is only affordable to
repeat because resolved locations now persist between runs.

### Changed

- **`DEFAULT_CONTRIBUTOR_LIMIT` is `None`: a scan collects every contributor**
  GitHub attributes to an account, rather than the first 25 by commits. The old
  limit was inherited from the `contributors` command v0.2.0 retired and had
  never been chosen for the current design. It decided what
  `contribution_total` counted - and therefore what `foreign_percent` and
  `adversarial_percent` will mean - which makes it a measurement decision
  rather than a tuning knob, and the accounts it dropped were exactly the long
  tail a residency analysis is trying to characterise.
  [ADR-0006](docs/adr/0006-collect-every-contributor.md).

  **`contribution_total` from v0.4.1 and earlier is not comparable with
  `contribution_total` from this release.** Nothing in a document records which
  limit produced it; `scan_id` distinguishes runs and this note distinguishes
  the rule.

  GitHub's own ceiling remains and is now documented rather than worked around:
  only the first 500 author email addresses in a repository link to accounts,
  so a repository past that reports a total below its true commit count.
- **The REST contributor list is paginated at 100 per page** (`client.PER_PAGE`),
  the endpoint's maximum. PyGithub defaults to 30, which was invisible while 25
  fitted in one page and is the difference between 5 requests and 17 for a
  500-contributor repository.
- **The aliased contributor-detail query is chunked** at `DETAIL_CHUNK_SIZE`.
  Not for cost - the formula counts connections and this document has none, so
  a chunk costs one point whatever it carries - but for the **ten-second
  processing window**: GitHub terminates a query it cannot finish in time, and
  an unbounded alias count fails on exactly the largest repositories.
- **The budget pre-flight is a floor rather than a guarantee.**
  `POINTS_PER_REPOSITORY` and `REQUESTS_PER_REPOSITORY` are now
  `MIN_POINTS_PER_REPOSITORY` and `MIN_REQUESTS_PER_REPOSITORY`, and passing
  the check is necessary but no longer sufficient - a repository's real cost
  depends on a contributor count nothing knows until the list is read.

  Estimating instead, by multiplying an assumed average, was considered and
  rejected: it would produce a number shaped exactly like the old guarantee and
  unequal to it in meaning. A check that reports more confidence than it has is
  the failure this project has already been caught by three times.

### Fixed

- **A bot in a contributor list aborted the entire scan.** Not a regression in
  this release - it would have done the same in v0.4.1 - but the first live run
  this project has ever done hit it within seconds, on the seventh contributor
  of the first repository tried.

  `dependabot[bot]`, `github-actions[bot]` and a repository's own automation are
  listed as contributors like anyone else, but GraphQL models them as `Bot`
  rather than `User`, so `user(login:)` cannot resolve one. GitHub answers such
  a document with **HTTP 200, the other forty-nine accounts resolved, `null`
  for the bot, and a `NOT_FOUND` entry in the `errors` array**. PyGithub maps a
  lone `NOT_FOUND` to `UnknownObjectException`; `graphql.execute` read that as
  "the repository does not exist"; and the resulting `RepositoryNotFoundError`
  is not a `ContributorCollectionError`, so it escaped the runner's
  per-repository handling and ended the run. **No CSV, no documents, exit 1**,
  and an error blaming the inventory for a repository that was fine.

  Two fixes, because there were two faults:

  - `execute` gained `tolerate_missing`, which the contributor-detail query
    opts into and nothing else does. That document names no repository, so a
    `NOT_FOUND` in it cannot mean one; the unresolved alias comes back `null`
    and the rest of the chunk is kept. A tolerated document's failure is also
    never classified as `RepositoryNotFoundError`, which was sending operators
    to fix inventories that were correct.
  - `get_account_details` now raises `ContributorCollectionError` for every
    failure. `execute` raises errors that are not that type, and the runner
    catches only that type for the contributor half - so *any* detail failure,
    not just a bot, could end a whole run. The contract was always a row and no
    document; nothing enforced it.

  Nine regression tests, built from the payload the live API actually returned.
  `NousResearch/hermes-agent` has three bots in its contributor list.

### Added

- **A geocode cache that survives the run**, at the platform cache directory
  or wherever `GEOCODE_CACHE_PATH` points. A re-run over a stable inventory now
  pays approximately nothing for the slowest part of a scan.
  [ADR-0007](docs/adr/0007-persistent-geocode-cache.md).

  Expiry differs by outcome, and that is the decision rather than a detail. A
  **match** is trusted for 365 days - places do not move, and `country_code` is
  ISO 3166-1 alpha-2, so the year is there to pick up gazetteer improvements
  rather than to guard against staleness. A **miss** is trusted for 30 days,
  because a miss is a statement about coverage and coverage grows. A **service
  failure is never written at all**: it says nothing about the location, and
  persisting one would let a single outage poison those locations permanently,
  with every later run reading "unresolved" from the cache and never asking
  again. A failure still memoises in-process, so eight workers do not each wait
  out the same dead lookup.
- `github_metrics.geocache`, which owns the cache file's format, expiry and
  atomic write. Separate from `geo.py` because collection may not touch a disk
  format: `geo` opens sockets and parses nothing, `geocache` parses and opens
  nothing, and the CLI joins them.
- `Address.from_mapping` and `Coordinates.from_mapping`, the inverse of
  `to_mapping`, keeping `""` distinct from `None` and a coordinate of `0.0`
  distinct from an absent one across the file boundary.
- `GEOCODE_CACHE_PATH`, documented in `.env.example` and the README. An empty
  value turns persistence off.
- `docs/METRICS.md` gains **"What geocoding is for"** - the residency question
  it exists to answer, why the components and not just a country, and what it
  explicitly does not claim.

### Notes

- No output key, column or value shape changed. What changed is how many
  contributor records a document carries, and therefore `contribution_total`.
- **The point at which the cache should become a table is measured, not
  estimated.** An entry is about 510 bytes; resident memory is 2.1x the file;
  loading costs about four times saving. Review at 10 MB (~20,000 locations),
  move by 50 MB - and the binding constraint is **load time**, not memory,
  which is the opposite of what the first estimate assumed. The table is in
  [ADR-0007](docs/adr/0007-persistent-geocode-cache.md) and `ROADMAP.md`.
- **Verified against the live API for the first time.** One scan of
  `NousResearch/hermes-agent` (241,787 stars, 3,310 contributor identities):
  396 accounts collected in 186 s cold, 42 s on a warm cache, with byte-identical
  addresses between the two.
- **The GraphQL cost is now measured rather than calculated**, which
  `ROADMAP.md` has carried as an open item since v0.2.0. Asked via
  `rateLimit { cost }`: the repository query costs **1** point, and the aliased
  detail query costs **1** point at 1, 10 *and* 50 aliases. `DETAIL_CHUNK_SIZE`
  therefore costs points rather than saving them, and exists purely for the
  ten-second processing window.
- That run also showed the pre-flight floor understating a real repository by
  4.5x - 9 GraphQL points actually spent against a floor of 2 - which is the
  behaviour ADR-0006 describes, observed.
- 660 tests, `make mutants` catches 30 of 30, and the trace matrix covers 187
  of 187 L2 and L3 requirements.

## [0.4.1] - 2026-09-04

The findings v0.4.0's own analysis could not see. **No output changed**: not a
column, not a key, not a value.

v0.4.0 shipped SonarCloud reporting zero issues. That was not a measurement.
The project's main branch on SonarCloud was still named `master`, which this
repository does not have, so every read answered about a branch that had never
been analysed - and an unanalysed branch reports nothing, which is
indistinguishable from a clean one. Renaming it surfaced twenty-four issues
that had been there all along.

That makes three instances in this project of the same failure, and they are
worth naming together: a check that reports success when it cannot actually
check. The coverage paths that would have reported 0%, the three tests that
passed however the code behaved, and now an analysis of a branch that did not
exist.

### Fixed

- **Fourteen exception tests that could pass for the wrong reason**
  (`python:S5778`). Each built its stub *inside* the `pytest.raises` block, so
  the test would pass if **construction** raised rather than the call under
  test - which is not what any of their names claim. The stub now goes
  outside, leaving one call that can throw.
- **Super-linear backtracking in three trace-matrix patterns**
  (`python:S8786`). `\s*` matches a newline, so beside `[^\n]+` under
  `re.MULTILINE` the two compete for the same characters. They now use
  horizontal whitespace, which is what they meant.
- **A float equality one change away from failing silently**
  (`python:S1244`). `weight == 0.0` is exact today because every weight is a
  band-table constant; the moment one is interpolated it starts missing values
  that are zero to any decimal place that matters. A weight cannot be negative,
  so `<= 0.0` is the same test now and a correct one later.
- **A field named `logger` on a class named `Logger`** (`python:S1700`), and
  **file paths repeated three and four times** in the mutation list
  (`python:S1192`), where a typo in one copy would have skipped that mutation
  rather than failed.

### Changed

- **`reset_logger` drains its handlers rather than copying them.** SonarCloud
  reported the `list()` call as unnecessary; it was not. `removeHandler`
  mutates the list being iterated, so walking it directly leaves half the
  handlers attached - four become two, measured - and a second `reset_logger`
  then duplicates every record. A `while` loop is correct and cannot be
  mistaken for redundant.
- **`Address.with_query` replaces `dataclasses.replace` in the geocoder.**
  mypy resolves `replace` correctly, but SonarCloud models it as returning
  `DataclassInstance` whatever the annotation says. The copy is written out so
  both can follow it - carrying fields across by name so a new one cannot be
  dropped, with a test comparing the copy against the declared fields.

### Documentation

- **`docs/ROADMAP.md` gains "Carried, and known"** - what this project knows
  about itself and has not done. The contributor-detail query cost that is
  calculated rather than measured; the single integration test that **neither
  CI workflow runs**, so nothing has ever exercised the live API; a
  contributor limit inherited rather than chosen; a generic Nominatim user
  agent; and a geocoding cache that dies with the process.

  It also records the two SonarCloud rules answered differently than they ask,
  because both read as tidy-ups and both would reintroduce a measured defect if
  a later reader helpfully undid them.

## [0.4.0] - 2026-09-03

Static analysis, and evidence that the test suite is worth having. **No output
changed**: not a column, not a key, not a value.

### Added

- **SonarCloud analysis** — `.github/workflows/sonarcloud.yml` and
  `sonar-project.properties`. The project key, organization and token all come
  from repository secrets, so nothing in the repository has to be kept in step
  with an account. The scan **skips rather than fails** when a secret is
  absent, and on pull requests from forks, which cannot read secrets: a red X
  on every push trains people to ignore a workflow.
- **`make mutants`** — `scripts/mutation-check.py` breaks one documented
  behaviour at a time, thirty of them, and requires the suite to notice. Not
  part of `make check`: it runs the suite once per mutation, so it costs
  minutes rather than seconds.
- **`L2-LOG-003` and `L3-LOG-003`.** `L1-LOG-001` promises diagnostics on a
  separate stream at a selectable severity, and nothing derived it — the
  behaviour had tests that named no requirement.

### Fixed

- **Three tests that could not fail.** Found by the mutation check, and each a
  different shape of the same problem:

  `document_path`'s case-folding was asserted with `Path == Path`, and
  `WindowsPath.__eq__` folds case — so the test passed on Windows however the
  code behaved. The rule exists because on Windows and macOS the second
  spelling silently overwrites the first, and those were exactly the platforms
  not checking it.

  The byte-order mark had two independent defences, `utf-8-sig` in the decode
  and an `lstrip` in the header normaliser. Either alone handled Excel's
  export, so deleting the decode left every test green. The redundant one is
  gone and the fixture now pins the decode.

  `resolve_destination` raises `GM-OUT-002` from two guards and the test
  asserted only the code, leaving the missing-directory guard unverified. It
  now asserts the reason, and the second guard has a test of its own.

- **Coverage would have reported 0% to SonarCloud.** coverage.py wrote file
  names relative to the package directory — `cli.py` against a `<source>` of
  `.../github_metrics` — and Sonar resolves coverage paths from the repository
  root. It would have matched nothing and said nothing about why, which is the
  failure that gets a quality gate switched off rather than fixed. CI now
  greps the report for repository-relative paths before scanning.

- **Sonar reported a clean project for a branch that had never been analysed.**
  Without a `branch` or `pullRequest` parameter these endpoints answer about
  the project's default branch, and an unanalysed branch answers 200 with
  nothing — which reads exactly like good news. Every read now names what was
  analysed, and waits for the report to finish processing first.

- **Addresses acquired a state and a county nobody published.** The components
  are the match's own, and are never reverse-geocoded. Forward-geocoding
  `United States` returns the country's centroid; reverse-resolving that point
  returns a county in Kansas, so every contributor naming a country was
  recorded as living there. A residency rule keyed on `state` or `county`
  would have been reading invented values.

### Changed

- **Ruff widened to the ground SonarCloud's Python rules cover** — security
  (flake8-bandit), cyclomatic complexity, the pylint port, timezone-naive
  datetimes, stray prints, logging misuse — with thresholds matching
  SonarCloud's own defaults, so a function that passes locally passes there.
- **Address components ordered for US addresses.** `town`, `village`, `hamlet`
  and `locality` lead the settlement chain; `borough` joins the suburb chain
  for New York, where a Brooklyn address is `city` "City of New York" with
  `borough` "Brooklyn". The non-US keys stay, last: tuning for US addresses
  cannot mean only US addresses, because identifying who is *not* American is
  the point of the rule these components feed.
- **`read_repository_csv`'s per-row ladder is `_check_row`.** It was over the
  complexity threshold, and making its documented contract visible — field
  count, then emptiness, then grammar, stopping at the first failure — beat
  raising the threshold. Duplication stays with the caller, being the one
  condition that depends on the rows around a row rather than on the row
  itself. Behaviour unchanged.

## [0.3.0] - 2026-09-03

Retires the two metric probe commands. **No output changed**: not a column, not
a key, not a value.

### Removed

- **`github-metrics closed-issues` and `github-metrics releases`.** They
  existed so a metric definition could be checked against real repositories
  while it was still being argued about, which `L2-CLI-005` said in as many
  words. Every definition in `METRICS.md` now reads Settled, so the condition
  that justified them has gone, and two commands duplicating what `scan`
  collects are two more surfaces to keep correct.

  The `closed_issues` and `releases` **columns are untouched**. They are
  Settled metrics, `scan` collects them in the same one-point GraphQL query as
  everything else, and they appear in both artifacts exactly as before. What
  went is the per-metric command, not the measurement.

- **`L2-CLI-005`, `L3-CLI-005` and `L3-CLI-006` are retired.** Identifiers are
  permanent, so each is recorded with its condition in a `Retired` section of
  `L2.md` and `L3.md` and its number is never reused.

### Kept

- **`github-metrics bands`** — the half of the probes still earning its place.
  Every scoring table stays printable, for every metric, without a token
  (`L2-CLI-006`). Retiring the probes does not retire the tables.
- **`collect.closed_issues` and `collect.releases`** as library API, for a
  caller that wants one number rather than a whole row. `L2-MET-001` through
  `L2-MET-007` still describe them and their tests still run.

## [0.2.0] - 2026-09-03

The contributor dataset. The per-repository JSON in `docs/example.json`
turned out to be the metrics row plus a contributor block, not a second
dataset, so one `scan` now collects and writes both under one scan identity.

Three things ship knowingly incomplete, and all three are visible in the
output rather than hidden behind it:

- **`foreign` and `adversarial` are `null`**, along with the four aggregates
  derived from them. Neither has a definition in `METRICS.md`, and nothing is
  computed before it does. The keys are present so the shape does not change
  when the definitions land; a `null` publishes no number and makes no claim.
- **The contributor-detail query's cost is calculated, not measured.**
  `POINTS_PER_REPOSITORY` is 2 on the strength of GitHub's documented cost
  formula. The metrics query's point was measured against the live API; this
  one has not been, which is a departure from this project's own rule.
- **`GEOCODER_USER_AGENT` defaults to a generic string.** Nominatim's usage
  policy asks for an agent that identifies the application and its operator,
  and the penalty for a generic one is a block that fails every later run.
  Set it before scanning anything large.

Every run also now pays for contributor pages and geocodes, which the v0.1.0
command split existed to avoid. That is the accepted price of one scan
identity per run, and geocoding rather than the GitHub API sets the pace: a
first run over a few hundred repositories takes hours.

### Added

- **Contributor collection, in `scan`.** Every run writes one JSON document per
  repository at `<output>/<owner>/<repoid>.json`, carrying that repository's
  row followed by its contributors. Each contributor record has the account's
  id, display name, company, self-reported location, the address that location
  resolved to, and its commits in that repository.
- **The contributor block**: `contributors` plus five aggregates over it —
  `contribution_total`, `foreign_contribution`, `adversarial_contribution`,
  `foreign_percent`, `adversarial_percent`. These belong to the document.
  `githubmetrics.csv` is unchanged at twenty columns: it is the comparable
  table, and its shape is fixed so that two runs diff and a column sorts, while
  the document is one repository's detail record.

  A document is the twenty columns, complete and in canonical order, then that
  fixed six-key suffix — which is a rule a test states in one line. The
  previous formulation, "the first twenty keys must not collide with the rest",
  could not.
- **`--format console`**, printing the tabular artifact instead of writing it.
  The documents are still written: there is no console rendering of four
  hundred of them.
- **Structured addresses.** `geo.py` returns Nominatim's fourteen address
  components rather than a bare coordinate pair, and distinguishes three states
  that a naive implementation collapses into one — never asked (every field
  `null`), asked and unresolved (`query` only), and matched (`""` for a
  component the match genuinely lacks). Without the middle state an account
  publishing nothing is indistinguishable from one publishing `she/her`, and
  those are different facts about that account.

  Three details of the GitHub-to-Nominatim mapping are not obvious and each
  fixes a way the naive version is wrong. **Place names are pinned to
  English**, because Nominatim answers in the local language by default — so
  `country` would read `Germany` for one contributor and `Deutschland` for
  another, and a rule keyed on it would apply to some accounts and not others
  without failing. **A settlement is found under whichever key names its
  kind** — `city`, `town`, `village`, `municipality` or `hamlet` — because
  reading only `city` leaves the field empty for most of the world. **The
  ISO 3166-2 subdivision code is taken from the coarsest `ISO3166-2-lvl*`
  present**, because a hard-coded `lvl4` is the US level and finds nothing
  elsewhere. A residency rule should key on `country_code`, which has no
  language at all.
- **`GM-COL-005`**, for a repository that was read but whose contributor list
  was not, and **`GM-OUT-003`**, for a document directory that cannot be used.
- **`GM-COL-004`, `GM-OUT-001` and `GM-OUT-002` documented** in the error
  catalogue, which claimed to carry every code and was missing three.

- **`url` as an output column**, after `name`, `owner` and `organization`. It
  is derived from the owner and the name rather than reported separately, which
  makes it an identity column: a repository that could not be read still
  carries the address someone would visit to find out why. `RepoMetaData.url`
  spells it as GitHub does, `RepositoryRef.url` as the inventory does, and a
  row uses whichever it has. Twenty columns now, not nineteen.
- **[ADR-0005](docs/adr/0005-one-scan-command-and-per-repository-json.md)**,
  recording why one command produces both artifacts, and settling the on-disk
  layout: `githubmetrics/<owner>/<repoid>.json`, always lower case, and no file
  at all for a repository that could not be read. The flat
  `<owner>-<repoid>.json` was rejected because hyphens are legal in both an
  account and a repository name, so it maps `foo-bar/baz` and `foo/bar-baz` to
  one file — two different repositories rather than a duplicate pair, so
  duplicate detection correctly reports nothing while one overwrites the other.
  Lower case because GitHub names are case-insensitive and `RepositoryRef.key`
  already folds case to say so. No file because a JSON document holding an
  empty contributor array and a zero total reads exactly like a repository with
  no contributors, and the CSV's empty *fields* have no equivalent in a
  directory listing — where an absent file is unambiguous on its own. Two invocations produce
  two `scan_id` values and two `scan_date` values, so a `githubmetrics.csv` and
  a folder of per-repository JSON collected minutes apart cannot be joined or
  grouped by run — which is the whole reason those columns exist. Still
  `proposed`: the contributor block's definitions and the on-disk filename
  layout are open.

### Changed

- **`contributors` is removed, and `scan` writes both artifacts every time.**
  No flag governs which — not `--contributors`, not `--metrics`. Both
  artifacts carry `scan_id` and `scan_date`, and those are assigned once per
  run, so two commands produce two identities and a CSV and a folder of
  documents collected minutes apart cannot be joined or grouped by the run that
  measured them. That is the only reason those columns exist.

  A flag was considered and rejected. It buys exactly one state nothing else
  expresses — documents without a table — and charges for it by making what a
  run produced depend on how it was invoked rather than on which command was
  run. It is also not the cost lever it looks like: the repository query
  returns every column of a row for one point whether or not a document is
  written, so the saving is asymmetric while the flag looks symmetric.

  The cost is real and is accepted rather than hidden: every run now pays for
  contributor pages and geocodes, which is what the v0.1.0 command split
  existed to avoid. See
  [ADR-0005](docs/adr/0005-one-scan-command-and-per-repository-json.md), now
  `accepted`.
- **`--output` names a directory, and both artifacts go in it**, defaulting to
  `./githubmetrics`. Keeping them together is not tidiness — they share a
  `scan_id`, and separating them by default would undo the joinability the
  whole design is for.
- **A scan costs two GraphQL points and one REST request per repository**, and
  `check_budget` now checks both budgets. GraphQL binds first, at 2,500
  repositories an hour rather than 5,000. The contributor *list* is the one
  REST call, because GraphQL has no connection reporting commits attributed per
  account; the contributor *details* deliberately are not, because the REST
  payload carries no name, company or location and PyGithub would complete each
  account lazily at one request each — 26 per repository, so a 200-repository
  inventory would exhaust the REST budget before finishing. One aliased GraphQL
  document makes a repository's cost independent of its contributor count, and
  carries no `nodes`, so it is not priced by how many accounts could come back.
- **Geocoding is unconditional and paced at one request per second.**
  `--geocode` is gone. The pace is enforced rather than trusted to politeness:
  Nominatim's penalty is blocking the user agent, which fails every *later* run
  rather than the one that misbehaved. Locations are cached per run, so the
  cost is the number of distinct locations rather than of contributors — but a
  first run over a large inventory takes hours.
- **Geocoding is cached case-insensitively**, on a whitespace-normalised form
  of the location with invisible format characters removed. `San Francisco, CA`,
  `san francisco, ca` and `San  Francisco,  CA` are one place typed three ways
  and Nominatim answers them identically, so they are one lookup rather than
  three — which at one request per second is the pace of a whole run. Each
  contributor still records the spelling it published.
- **`--contributors N` is gone**; the limit is a fixed 25.
  `contribution_total` counts what was collected rather than what exists, and
  `METRICS.md` says so, because a total that silently means something narrower
  than its name is the kind of number that survives review.
- **`docs/example.json` reordered**: the twenty-five columns in canonical
  order, then `contributors` last. `foreign`, `adversarial` and the four
  aggregates that depend on them are `null` rather than `false`/`0`, because
  `false` is a judgement about a named person that nothing in this repository
  has measured. Its `contribution_total` is now the sum of the contributors it
  actually shows.
- **The legacy `metrics.py` and `models.py` are deleted.** They backed the
  `contributors` command and collected a different field set — watchers, open
  issues, commits in the last year, licence, language — that appears in
  neither artifact.
- **`metrics` is now `scan`.** A run is called a scan everywhere else in the
  codebase — `ScanIdentifier`, `scan_id`, `scan_date` — and the command that
  stamps those values now shares their word. It also stops being a name that
  only fits half of what the command is growing into. `metrics` is retired
  rather than recycled, as `repo` was before it.
- **`repo_name` is now `name`.** `repo_name` said `repo` twice in a file whose
  every row is a repository, and the per-repository JSON this column set feeds
  calls it `name`. One spelling across both artifacts means the CSV and the
  JSON join on identical keys rather than through a translation table.
- **`docs/example.json` corrected** against the shipped column set: the
  `prevalance_score` spelling, `trusted_org: ""` replaced by the
  `is_trusted_org` boolean the CSV already carries, and the columns reordered
  to the canonical order so the example and the header cannot disagree.
- **Field selection is documented as a rendering filter and nothing more.**
  The docstring still described it as a rate-limit lever, which was withdrawn
  in v0.1.0 once every column came from one GraphQL query costing one point.

### Fixed

- **`contribution_total` would have reported `0` for a repository whose
  contributors could not be read**, in an intermediate version that carried the
  aggregates as columns. Zero is a legitimate measurement — a repository really
  can have no contributors — so it cannot also mean "not collected". The
  shipped shape has no such state: a document exists only where the list was
  read, so a `0` there always means read and empty.
- **Null Island in the contributor example.** An ungeocoded contributor carried
  `"latitude": "0", "longitude": "0"`, but 0,0 is a real coordinate in the Gulf
  of Guinea, so a failed lookup was indistinguishable from a successful one
  — the same mistake the "metric fields default to `None`, never `0`" rule
  exists to prevent. Unknown coordinates are `null`, as is the rest of an
  address that was never resolved. `"location": "null"` was also the string
  rather than the value.

## [0.1.0] - 2026-08-30

First release. Reads a list of GitHub repositories, collects comparable
metrics for each, scores them, and writes `githubmetrics.csv`.

Two components are known not to separate a mature portfolio:
`prevalence_score` saturates at 20.0 for any established repository, and
`trusted_org_bonus` is 0.0 for nearly every row given a three-entry list. The
ranking is carried by stars, forks, maturity and last update. That is accepted
for this release; the levers are the band boundaries and the length of the
trusted list.

### Added

- **Repository inventory ingestion.** `github_metrics.sources` reads an
  `owner,repoid` CSV into validated `RepositoryRef` values. It performs no
  network access and collects no metrics, so it needs no `GITHUB_TOKEN`.
  Tolerates a UTF-8 BOM, CRLF or LF endings, reordered/recased/padded headers,
  unknown columns, blank lines and padded values; rejects malformed headers,
  non-UTF-8 bytes, binary content, malformed rows, invalid GitHub names and
  duplicates.
- **`github_metrics.validation`** with the GitHub account and repository name
  grammars, returning the reason a name was rejected rather than a boolean.
- **`github_metrics.errors`** — a stable `GM-<AREA>-<NNN>` code taxonomy,
  separating file-level exceptions from row-level `RowIssue` records.
- **Concurrent multi-file reads** via `read_repository_csvs`, deterministic in
  both result order and error precedence.
- **`github-metrics ingest`** command, with `--strict`, `--workers`,
  `--format {text,json}` and `--output`, and distinct exit statuses (0 clean,
  3 rows rejected, 2 unreadable).
- **`github_metrics.sources`**, where a repository gets named. A source is a
  slug, a GitHub URL, or a CSV inventory, and every command that takes
  repositories takes all three, mixed freely in one invocation. URLs are
  accepted with or without a scheme, with `www.`, with a trailing slash, with
  `.git`, in the `git@` clone form, and with a leftover browsing path.
- **A URL on another host is refused rather than reduced** (`GM-ING-017`).
  `gitlab.com/foo/bar` reduces perfectly well to `foo/bar`, which is exactly
  the problem: the result is a plausible reference to a different repository,
  so a mistake would become a row instead of an error.
- **A repository named twice is collected once** (`GM-ING-015`), whether the
  repetition is within a file, across files, or between a file and the command
  line. The first mention keeps its position and the second is reported against
  the source that already had it. Collecting it twice would spend the rate
  limit twice and produce two rows no consumer could tell apart.
- **`github-metrics metrics`**, the release deliverable. Takes the same sources
  as `validate`, collects concurrently, scores, and writes `githubmetrics.csv`
  — one row per accepted reference, in input order. `--output` (a directory
  gets the default filename), `--format csv|json`, `--fields`, `--workers`,
  `--strict`. With no destination the rows render vertically on the console.
- **A rate-limit pre-flight.** Collection costs one GraphQL point per
  repository, so a run confirms the token can cover it before collecting
  anything (`GM-COL-004`, exit 5). A run that discovers exhaustion halfway has
  already spent what it had and leaves a file where the repositories at the end
  are indistinguishable from ones that could not be read. No reserve is held
  back, so a full hourly quota collects exactly 5,000 repositories.
- **`github-metrics contributors`**, replacing the other half of `repo`. Same
  sources, separate dataset. `metrics` never pays for contributor pages, which
  are the expensive half of the request budget and produce columns
  `githubmetrics.csv` does not have. Its own columns are not settled, so it
  emits JSON.
- **Documentation set** under `docs/`: user guide, CLI reference, error
  catalog, architecture, maintainer guide, roadmap, three levels of
  requirements (`L1.md`, `L2.md`, `L3.md`) and three ADRs.
- **`scripts/build-trace-matrix.py`**, which generates `docs/TRACE-MATRIX.md`
  from the requirement documents and `@pytest.mark.requirement` markers. CI
  runs it with `--check` so the matrix cannot drift.
- **Byte-exact CSV fixtures** under `tests/data/`, exempted from line-ending
  normalisation so a CRLF fixture keeps testing CRLF.

- **Metric collection over GraphQL.** `github_metrics.collect` gathers closed
  issues, releases and tags, and repository timestamps. `collect.repository`
  fetches every value a row needs in **one query costing one point**, so an
  inventory of any size fits a 5,000-point hourly budget.
- **Owner and organisation.** A row records the owner the inventory supplied
  and the owner GitHub reports. `organization` carries the owning
  organisation's login, and is empty when the repository belongs to an
  individual account - which is the answer, not a gap. A repository GitHub
  redirects to a new owner (`tiangolo/fastapi` to `fastapi/fastapi`) is
  reported, since the inventory entry still resolves but no longer matches.
- **Scoring.** `github_metrics.analysis` scores closed issues, releases,
  prevalence, last update, maturity, stars, forks and the trusted-organisation
  bonus. Every band table is data rather than an `if`/`elif` chain, is total
  over its domain, and renders itself for the documentation and the CLI.
- **Elapsed time anchored to `scan_date`.** `analysis.elapsed` measures age and
  time-since-update against the single instant recorded for the run, so rows in
  one file stay comparable and re-running an inventory in a different order
  cannot change its numbers.
- **`github-metrics closed-issues`** and **`github-metrics releases`**, probe
  commands reporting one metric with `--explain` and `--format json`.
- **`github-metrics bands [METRIC]`** prints the scoring tables, rendered from
  the objects the scorer reads, needing neither token nor network.
- **Credential handling.** `--token`, `--token-file`, and a free up-front check
  against the rate-limit endpoint, with exit 7 for no token and exit 8 for a
  token GitHub rejects. `LOG_LEVEL=DEBUG` reports the token's source, kind,
  length and scopes; the value itself is never logged, at any level.
- **`github_metrics.analysis.trusted_orgs`** with a replaceable registry of
  trusted owners and a 10-point bonus.
- **`stars` and `forks` are settled**: GraphQL `stargazerCount`, and
  `forkCount` for **direct forks only** rather than the whole fork network.
  A fork taken from another fork measures that fork's visibility, not the
  original project's.
- **`github_metrics.analysis.total`** sums the six components. The ceiling of
  **85.0** is computed from their weights rather than clamped, so a component
  that drifts past its own share shows up as a total that overshoots — and is
  reported with the offending component named, instead of being flattened back
  to 85.0 with nothing to say why.

- Initial project scaffolding: Poetry packaging, CLI entry point, tooling
  configuration (black, isort, ruff, pylint, mypy, vulture, pytest), pre-commit
  hooks, and GitHub Actions CI.
- `github-metrics repo OWNER/NAME` collects point-in-time repository metrics.
- `github-metrics rate-limit` reports remaining API budget.
- `github_metrics.logger` with `LogLevels`, a re-runnable `reset_logger()`, and a
  deprecated `Logger` wrapper kept for older callers.
- `-V` as a short alias for `--version`, the version in the `--help` banner, and a
  `tool_version` field on every `RepositoryMetrics` snapshot.

### Changed

- **`ingest` is now `validate`, and `ingest.py` is now `sources/csv_inventory.py`.**
  The old name described what the command did to the file rather than what it
  did for the user, and it sat oddly beside `metrics` and `contributors`, which
  are the commands that ingest anything. `validate` says what it is for:
  checking a list before any rate limit is spent on it, on a machine with no
  token at all.
- **`validate --format json` emits one document per run**, not one per file.
  The sources are an input detail; a consumer wants the references in the order
  they were asked for. `source_line` is `null` for a repository named on the
  command line, which has no line to point at.
- **A `RowIssue` may carry no line number** — a reference typed as an argument
  has no line to point at, and the rendered message drops the part it cannot
  fill rather than printing `line None`.
- **`repo` is gone**, replaced by `metrics` and `contributors` rather than
  renamed to either: it was scaffolding, and it collected a different set of
  fields from both.
- The CLI resolves configuration lazily, so a command that needs no credentials
  no longer fails when none are configured.
- Python 3.14 is now a required CI target rather than `continue-on-error`; it passed
  on the first run.
- Bumped `actions/checkout` to v7, `actions/setup-python` to v7,
  `actions/upload-artifact` to v7, and `github/codeql-action` to v4.
- The CLI configures logging through `reset_logger()` instead of
  `logging.basicConfig()`.

- **Counts come from GraphQL, not REST.** REST cannot count closed issues
  correctly at any price: `open_issues_count` includes pull requests, the
  issues endpoint returns pull requests with no server-side filter, and since
  that endpoint moved to cursor pagination PyGithub's `totalCount` returns
  **1** for every repository, silently. GraphQL returns an exact total,
  excludes pull requests by construction, and costs one point.
- **Distinct versions are counted once.** The release metric scored
  `releases + tags`, but publishing a release creates a tag, so every release
  was counted twice - by between 1.3x and 2x across a sample, and the
  overstatement grew with how consistently a project used the Releases feature.
  The scored value is now the distinct version count.
- **Prevalence combines the stronger of its two signals** rather than falling
  back from one to the other, and excludes the issue signal for a repository
  whose tracker is disabled.
- **A project with no evidence at all scores 0.0**, rather than collecting the
  lowest non-zero band for existing.
- **`client_name` is renamed `repo_name` and holds what it always held.** The
  original header was misleading and this tool read it, wrongly, as a second
  copy of the owner — a mistake only possible because the reference row is
  `cline/cline`, where the owner and the repository are spelled the same. It is
  the repository's name, and together with `owner` it is what makes a row
  identifiable at all. Still nineteen columns.
- **`repo_name` is verified against the API rather than echoed from the input.**
  The name comes from the query already being sent, so verification costs
  nothing.
- **A repository that has been renamed or transferred is refused, not
  collected** (`GM-COL-003`, exit 4). GitHub redirects both silently, so a
  stale inventory entry still resolves and returns correct numbers — about a
  repository the inventory does not name, with nothing in the output to say so.
  The row is emitted with its identity columns and no measurements, and the
  warning names the current `owner/name` so the list can be fixed by copying
  it. Case is not a difference, since GitHub names are case-insensitive.
- **`organization` is empty for an unfetchable repository**, alongside the
  metrics, rather than being treated as an always-known identity column. It
  reads like identity but only the API can report it, and a repository that
  could not be read reported nothing.
- **Collection and scoring narrate at DEBUG rather than INFO.** Twenty log
  lines moved. Every one of them fires once per repository, so on an inventory
  of four hundred they were four hundred copies each — enough to bury the one
  line an operator was looking for. The detail is unchanged and one level down;
  warnings are untouched. The only INFO line left in the package is ingestion's
  per-file summary, which is per run rather than per repository.

### Fixed

- `cli.py` and `metrics.py` declared a module `LOGGER` and never used it.
  `cli.py` no longer declares one (its output goes through `click.echo`), and
  `metrics.py` now logs: a debug line when a license lookup fails, so an API
  error is distinguishable from a repository that simply has no license, and an
  info line when the contributor list is truncated by `--contributors`.
- Tests restore the `github_metrics` logger after each case. `reset_logger()`
  sets `propagate = False` on a process-wide singleton, so a test that ran the
  CLI could stop `caplog` from seeing later tests' records.
- Line endings are normalised to LF and pinned by `.gitattributes`.
- A shared `.vscode/` workspace pins every linter to this project's 100-column
  line length, removing spurious `line too long` warnings.

- **A gap in the closed-issue bands.** The chain ended `< 500 -> 0.9` and
  `> 500 -> 1.0`, so a count of exactly 500 matched no branch and returned the
  initial `0` - a plausible number rather than an error. Band tables cannot
  have that gap and are tested across their whole domain.
- **A units mismatch in the maturity score.** The first branch compared an age
  in *days* against a threshold in *years*, so only a repository under six
  hours old counted as too young and everything from six hours to three months
  was credited 0.2 - three points of maturity for a repository created
  yesterday.
- **A weight lost from the last-update bands.** A boundary intended as 0.9 was
  written as 0.05 and, being below every other weight, decided nothing: it was
  inert across the whole 0-40,000 hour range.
- **A negative elapsed time is reported and clamped to zero** instead of being
  accepted as "just updated" and scored at full marks. A repository can be
  updated while a long scan runs, and clocks disagree.
- **GraphQL failures are no longer read as success.** The API reports errors
  with HTTP 200 and an `errors` array, so a path that checks only the status
  sees success and then reads a null repository. `NOT_FOUND` is classified
  separately: a deleted or renamed repository is an expected outcome of a valid
  reference, not a defect.
- **Withdrawn: the claim that vulture silently skips non-ASCII source.** It was
  recorded here as a fix, and it is not one. The pinned version reads source
  with `tokenize.open`, which honours PEP 263 and defaults to UTF-8, and a file
  it genuinely cannot read sets `ExitCode.InvalidInput` rather than passing
  quietly — checked on a cp1252 console against two otherwise identical
  files, one containing an em-dash. The `\uXXXX` escapes in
  `scripts/build-trace-matrix.py` and `github_metrics/errors.py` are harmless
  and stay, but they were never necessary.

[Unreleased]: https://github.com/joey-huckabee/GitHub-Metrics/compare/v0.4.1...HEAD
[0.4.1]: https://github.com/joey-huckabee/GitHub-Metrics/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/joey-huckabee/GitHub-Metrics/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/joey-huckabee/GitHub-Metrics/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/joey-huckabee/GitHub-Metrics/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/joey-huckabee/GitHub-Metrics/compare/8feb637...v0.1.0
