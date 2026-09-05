# The scan process, end to end

What one `github-metrics scan` does, in order, and **every place a value can be
wrong, absent, or mean something other than it appears to**. Written to be read
adversarially: the question it answers is not "how does this work" but "where
could this dataset already be misleading me".

Companion documents: [`METRICS.md`](METRICS.md) defines the fields,
[`API-LIMITS.md`](API-LIMITS.md) the ceilings, [`ERROR-CATALOG.md`](ERROR-CATALOG.md)
the codes.

---

## The shape of a run

```
sources ──▶ resolve ──▶ budget ──▶ collect (N workers) ──▶ rows ──▶ CSV
                │                        │                   │
             offline                  network             documents
```

1. **Resolve** every source to a `RepositoryRef`. Offline, no token.
2. **Pre-flight** the budget. Refuses a run that cannot afford its *minimum*.
3. **Collect** each repository concurrently, in input order.
4. **Build rows**, one per accepted reference.
5. **Write** `githubmetrics.csv`, then one document per fully-collected repository.
6. **Save** the geocode cache. **Exit** with the highest applicable status.

---

## Stage 1 — Resolving sources

**Offline by construction.** Never opens a socket, needs no token. This is why
a bad inventory can be diagnosed before spending quota.

Each argument is classified by rules **in a fixed order**: URL, then existing
file, then `.csv` suffix, then slug.

### Corner cases

| Input | Outcome |
|---|---|
| `owner/repo` | slug |
| `https://github.com/o/r/blob/main/f.py#L10` | resolves to `o/r`; deep links are stripped |
| `https://gitlab.com/o/r` | rejected, `GM-ING-017` |
| A file named `pypa/virtualenv` that exists on disk | **read as a file, not a slug** — the file rule precedes the slug rule |
| BOM-prefixed CSV (Excel "Save as CSV UTF-8") | handled; decoded `utf-8-sig` |
| CRLF line endings | handled |
| A binary file renamed `.csv` | rejected once on a NUL check, not once per row |
| Duplicate references across several files | dropped, with a warning naming them |

**Where this can mislead you:** duplicates are removed *across all sources*, so
a repository named in two inventories appears once. A count of input lines is
therefore not a count of output rows. `validate` reports both.

---

## Stage 2 — The budget pre-flight

Reads the remaining GraphQL points and REST requests and refuses a run that
cannot afford `MIN_POINTS_PER_REPOSITORY` (2) and `MIN_REQUESTS_PER_REPOSITORY`
(1) per repository.

> **This is a floor, not a guarantee.** Since v0.5.0 collects every
> contributor, a repository's real cost depends on a contributor count that
> nothing knows until the list has been read. **Passing the pre-flight does not
> mean the run will finish.** Measured, one large repository spent 9 GraphQL
> points against a floor of 2.

**Where this can mislead you:** a run that exhausts the budget partway produces
a file that is part measurement and part absence. Until `--on-exhaustion`
lands ([ADR-0009](adr/0009-rate-limit-exhaustion-policy.md)) the run fails at
that point, and rows already written are on disk while the rest were never
attempted. **Check the row count against your inventory count.**

---

## Stage 3 — Collecting one repository

Two GraphQL queries and one REST endpoint. Order matters: the metrics query
runs first, and a failure there means no contributor work is attempted.

### 3a. Repository metrics — one GraphQL document, 1 point

Totals only, never `nodes`. Yields stars, forks, timestamps, closed issues,
releases, owner type.

| Situation | Behaviour |
|---|---|
| Repository deleted or private | `GM-COL-001`; identity-only row, no document, exit 4 |
| Repository **renamed or transferred** | **refused**, `GM-COL-003` |
| Case differs from the inventory | accepted; GitHub names are case-insensitive |
| Owner is a `User`, not an `Organization` | `organization` is **empty** — that is the answer, not a gap |

**The rename refusal is deliberate and is the subtlest rule here.** GitHub
silently redirects a renamed or transferred repository, so a stale reference
resolves and returns *correct numbers about a repository your inventory does
not name*. That is worse than a failure, because an error that looks like data
survives review. The row carries identity and no measurements, and the warning
names the current `owner/name` so the inventory can be corrected.

**Where this can mislead you:** `closed_issues` comes from GraphQL because REST
cannot count it — `open_issues_count` includes pull requests, and PyGithub's
`totalCount` returns **1** for every repository since GitHub moved to cursor
pagination. If you compare this column against a number obtained via REST
elsewhere, the REST one is wrong, not this one.

### 3b. The contributor list — REST, paginated at 100

`GET /repos/{o}/{r}/contributors`, `anon` deliberately **unset**.

> **This is the largest source of missing data in the tool.** GitHub links only
> the first **500 author email addresses** to accounts. Measured on one large
> repository: **396 accounts collected out of 3,310 contributor identities —
> 12% of the people, but 87% of the commits.**

| Situation | Behaviour |
|---|---|
| More than 500 author emails | the excess is simply absent; **no warning today** |
| List unreadable | `GM-COL-005`; full row, **no document**, run continues |
| Repository genuinely has no contributors | `contribution_total` is `0`, and that `0` is a measurement |
| Counts are stale | GitHub caches this endpoint; values may be hours old |

**Where this can mislead you, in three ways:**

1. **`contribution_total` is not the repository's commit count.** It is the sum
   over collected contributors. Measured: 27,828 against 32,005 actual commits.
2. **Bots are in it.** `dependabot[bot]`, `github-actions[bot]` and a
   repository's own automation are listed like anyone else. One repository's
   bot held 263 commits. They are counted in `contribution_total` **by
   deliberate decision** — the totals stay raw, and v0.6.0's `statistics.json`
   reports bot counts and a bots-removed figure beside it rather than altering
   the total.
3. **There is currently no signal in the output that truncation occurred.**
   A repository at the ceiling looks exactly like one with 396 contributors.
   Closing that gap is the main purpose of `statistics.json`.

### 3c. Account detail — aliased GraphQL, chunked

One `user(login:)` selection per account, aliased by position, `DETAIL_CHUNK_SIZE`
per document. Yields name, company, location.

Chunking exists for the **10-second processing window**, not for cost: measured,
a chunk costs 1 point whether it carries 1 alias or 50.

| Situation | Behaviour |
|---|---|
| Login is a **bot** | GraphQL cannot resolve it; alias returns `null` **plus** a `NOT_FOUND` error |
| Account deleted or suspended since the list was read | same |
| Any other GraphQL error | `ContributorCollectionError`; row kept, document dropped |

> **This was a defect until v0.5.0 and it is worth understanding**, because it
> is the shape of failure most likely to recur. GitHub answers a partly
> resolvable document with **HTTP 200, the resolved accounts, `null` for the
> unresolved one, and an error entry beside them**. Reading only the error
> discards 49 good answers; classifying its `NOT_FOUND` as "repository not
> found" blames a correct inventory. Both happened, and the resulting exception
> was of a type the runner does not catch for the contributor half, so **one
> bot ended an entire scan with no CSV at all**.

An account with no detail is still recorded, using its login as `name`, because
its commits are a real measurement and dropping it would quietly reduce
`contribution_total`.

### 3d. Geocoding

Each distinct location, normalised and case-folded, resolved through Nominatim
at one request per second, cached to disk between runs.

An address has **three states that must not be collapsed**:

| State | Looks like | Means |
|---|---|---|
| Never asked | every field `null` | the account published no location |
| Asked, unresolved | `query` only | published something no gazetteer knows |
| Matched | `query` + components; `""` where the match lacks one | resolved |

**Where this can mislead you:**

- **`""` and `null` are different.** `""` means the lookup ran and the
  component does not exist at that resolution — a country-level match genuinely
  has no city. `null` means nothing is known. Collapsing them makes an account
  publishing nothing look like one publishing `she/her`.
- **A location is self-reported free text, never verified.** `127.0.0.1`,
  `In front of a terminal` and `Dood` were all real values in one measured run.
  A match is a statement about the *string*, not about a person's residence.
- **Components are the match's own and are never reverse-geocoded.** A
  reverse lookup on a country centroid manufactures a state and a county —
  `United States` would acquire a county in Kansas.
- **A service failure is indistinguishable from a genuine miss in the output,
  by design**, and is deliberately never cached, so an outage costs one run's
  resolution rather than every future run's.
- Measured coverage on one repository: **175 of 396 published a location at
  all; 159 matched, 14 did not, 221 were never asked.** Any percentage over
  `country_code` has 56% of its population absent.

---

## Stage 4 — Rows, documents, and what they promise each other

- One CSV row per accepted reference, **in input order**, always.
- One document per **fully collected** repository — metrics *and* contributors.
- Both carry the same `scan_id` and `scan_date`.

| Repository state | Row | Document |
|---|---|---|
| Fully collected | yes, complete | yes |
| Metrics failed | yes, identity only | **no** |
| Metrics fine, contributors failed | yes, complete | **no** |

**A row without a document means "named, not fully measured."** A CSV row is
positional so omitting one would shift every later row's meaning; a directory
has no positions, so an absent file says it on its own. This is why the counts
differ and why that difference is information rather than a bug.

**Where this can mislead you:** a repository whose contributors failed produces
a **complete-looking row** and no document, and the run still **exits 0**. The
only signals today are a stderr warning and the `Wrote N documents` line being
lower than the row count. If you are consuming the CSV alone, you cannot tell.
That gap is on the v0.6.0 list.

---

## Stage 5 — Exit status

Severity-ordered; the **highest applicable** wins.

| Code | Meaning | File written? |
|---|---|---|
| 0 | clean | yes |
| 3 | some input rows rejected | yes |
| 4 | some repository unreadable | yes |
| 5 | budget exhausted or refused | no |
| 6 | input unreadable | no |
| 7 / 8 | no token / rejected token | no |

The load-bearing split is **3–4 against 5–6**: the first pair still wrote a
usable file, the second did not.

---

## Known deficiencies

Open, and listed so an analyst finds them rather than discovering them in the data.

| # | Deficiency | Effect | Status |
|---|---|---|---|
| 1 | 500-email ceiling not reported anywhere in the output | a truncated repository is indistinguishable from a complete one | v0.6.0 `statistics.json` |
| 2 | `contribution_total` includes bot commits | inflates human contribution | v0.6.0 reports it separately; total stays raw by decision |
| 3 | No commit-coverage figure | no way to know 87% from 100% | v0.6.0 |
| 4 | Contributors failing does not change the exit code | a degraded run exits 0 | open |
| 5 | Pre-flight is a floor | a run can start and not finish | by design; `--on-exhaustion` in v0.6.0 |
| 6 | `foreign` / `adversarial` always `null` | no residency determination is made here | by design; a separate stage |
| 7 | Location published by only ~44% of contributors | every geographic percentage has a large unknown | inherent; v0.6.0 quantifies it |
| 8 | Contributor counts may be hours stale | GitHub caches the endpoint | inherent |
| 9 | Nothing records which tool version produced a row | old and new rows compare silently | open, wants an ADR |
| 10 | `GEOCODER_USER_AGENT` defaults to a generic string | risks the agent being blocked | set the variable |

**The one to internalise:** items 1, 2, 3 and 7 all mean the same thing — the
dataset is a *sample with unstated bounds*. `statistics.json` exists to state
those bounds per repository so a downstream percentage can carry an honest
error bar instead of implying a census.
