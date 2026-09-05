# GitHub and Nominatim API limits

Every ceiling this tool runs into, what it costs, and what can be done about
it. Figures marked **measured** were observed against the live API on
2026-09-05 with a classic personal access token; the repository used was
`NousResearch/hermes-agent` (241,787 stars, 32,005 commits, 3,310 contributor
identities). Figures marked **documented** come from GitHub and have not been
confirmed here.

Read this alongside [`SCAN-PROCESS.md`](SCAN-PROCESS.md), which describes what
the tool does with the data it does get.

---

## 1. The hard ceilings

| Limit | Value | Source | Binds on |
|---|---|---|---|
| REST requests | 5,000 / hour | documented | contributor pages |
| GraphQL points | 5,000 / hour | documented | one per query |
| GraphQL query time | **10 seconds**, then terminated | documented | large aliased documents |
| GraphQL nodes per call | 500,000 | documented | not reached here |
| REST `per_page` | 100 maximum | documented | contributor pages |
| Contributor account linking | **first 500 author email addresses** | documented | contributor completeness |
| Contributor data freshness | "may be a few hours old" | documented | every count |
| Nominatim | 1 request / second | documented | wall-clock time |
| Secondary rate limits | 403 + `Retry-After`, ~100 concurrent | documented | not yet handled |

### What a query actually costs — measured

GitHub's point formula counts **connections**, not fields and not aliases. A
document with no connection costs the minimum of 1 however large it is. Asked
directly with `rateLimit { cost }`:

| Query | Aliases / shape | Cost |
|---|---|---|
| `collect.repository` metrics document | one repository, totals only | **1** |
| `collect.contributors` detail document | 1 aliased `user` selection | **1** |
| same | 10 aliased `user` selections | **1** |
| same | 50 aliased `user` selections | **1** |
| `history { totalCount }` | one connection, count only | **1** |

This settles what `ROADMAP.md` carried from v0.2.0 to v0.5.0 as calculated
rather than measured.

**The consequence is counter-intuitive and worth stating plainly:**
`DETAIL_CHUNK_SIZE` **costs** points rather than saving them. Splitting 396
accounts into 8 chunks of 50 costs 8 points; one document of 396 aliases would
cost 1. Chunking exists solely to stay inside the **ten-second window**, which
a several-hundred-alias document is not a safe bet against. Raising the chunk
size trades timeout risk for points; lowering it does the reverse.

### What a real repository cost — measured

`NousResearch/hermes-agent`, one repository:

| | Spent | Pre-flight floor | Ratio |
|---|---|---|---|
| GraphQL points | 9 (1 metrics + 8 detail chunks) | 2 | **4.5x** |
| REST requests | ~5 (1 lookup + 4 contributor pages) | 1 | 5x |
| Wall clock, cold cache | 186 s | — | |
| Wall clock, warm cache | 42 s | — | |

The floor understates a large repository by roughly five times in both
currencies. That is the documented behaviour of a floor rather than a defect
(see [ADR-0006](adr/0006-collect-every-contributor.md)), but it means
**5,000 GraphQL points does not buy 2,500 repositories of this size — it buys
roughly 550.**

---

## 2. The limit that costs the most data

### The 500-email ceiling

> "To improve performance, only the first 500 author email addresses in the
> repository link to GitHub users. The rest will appear as anonymous
> contributors without associated GitHub user information."

This is the single largest source of missing data in the tool, and it is not a
tuning problem — no request shape available on that endpoint removes it.

**Measured on `NousResearch/hermes-agent`:**

| Population | People | Commits |
|---|---|---|
| Linked accounts (`anon` unset — what the tool collects) | **396** — 12.0% | **27,828** — 87.0% |
| Anonymous (`anon=1` adds these) | 2,914 — 88.0% | 4,155 — 13.0% |
| **Total** | **3,310** | **31,983** |

The framing matters in both directions. **88% of the people are missing, but
they account for 13% of the commits** — the tail averages 1.4 commits each. A
question about *where the work comes from* is answered well by 87% coverage. A
question about *whether any particular person contributed at all* is not
answered at all by it.

An anonymous entry carries **a name and an email, and nothing else**: no
`login`, no `id`, and no `location` field of any kind. It can never be
geocoded, so `foreign` can never be determined for it by any route.

---

## 3. Ways around the limits

Ordered by cost. The first four are implemented; the rest are options.

### 3.1 Implemented

| Technique | Effect | Status |
|---|---|---|
| `per_page=100` | 5 contributor pages instead of 17 per 500 | v0.5.0 |
| Aliased single-object GraphQL for account detail | 1 point per chunk instead of 1 REST request per contributor | v0.2.0 |
| Chunking at `DETAIL_CHUNK_SIZE` | keeps every document inside the 10 s window | v0.5.0 |
| Persistent geocode cache | re-runs pay only for locations never seen | v0.5.0 |

### 3.2 Recover accounts from `users.noreply.github.com` emails — **verified**

GitHub's own no-reply address format embeds the account's numeric id and
login:

```
275304381+hakanpak@users.noreply.github.com
    │        └── login
    └── databaseId
```

Confirmed against the API: `user(login: "hakanpak")` returns
`databaseId 275304381`, an exact match. The mapping is authoritative — it is
GitHub's own construction, not a heuristic.

**Measured recovery on `NousResearch/hermes-agent`:**

| | People | % of people | Commits | % of commits |
|---|---|---|---|---|
| Collected today | 396 | 12.0% | 27,828 | 87.0% |
| **+ no-reply recovery** | **1,163** | **35.1%** | **28,892** | **90.3%** |
| Still unrecoverable | 2,147 | 64.9% | 3,091 | 9.7% |

Cost: `anon=1` makes the list 34 pages instead of 4, and the recovered accounts
add ~19 detail chunks. Roughly **34 REST requests and 24 GraphQL points** per
repository of this size, against 5 and 9 today.

The 2,147 that remain publish a real address (`nsovipgl@gmail.com`). **GitHub
exposes no email-to-user lookup**, deliberately, so no API turns those into
accounts. They can be counted and their commits attributed, but they cannot be
located, and adding them to the contributor array without saying so would drag
every percentage down while looking like coverage.

Scheduled for v0.6.0.

### 3.3 Walk the commit history — complete, and expensive

`repository.defaultBranchRef.target.history` attributes each commit to a GitHub
account where the author email is linked, and it is **not subject to the
500-email ceiling**. It is the only route to complete account attribution.

**Measured:** `NousResearch/hermes-agent` has **32,005 commits** on its default
branch. `history` is a connection, so it is priced by nodes and paginated at
100: roughly **321 pages, 321 points and 321 sequential round trips for one
repository.**

At inventory scale that is not viable — 200 repositories of this size would
need 64,000 points against a 5,000-per-hour budget, about thirteen hours of
pure quota. It is viable for a **short watchlist**, and that is how it should
be offered if it is offered: an opt-in command over a handful of repositories,
never the default.

### 3.4 Conditional requests

`ETag` / `If-None-Match` returning **304 does not count against the rate
limit**. Over a stable inventory this makes re-runs nearly free on the REST
side. Long-deferred; it pairs naturally with the persistence work.

### 3.5 Continue past exhaustion rather than refusing

`--on-exhaustion wait` sleeps to the hourly reset and continues, so a run
larger than one hour's quota completes unattended. See
[ADR-0009](adr/0009-rate-limit-exhaustion-policy.md).

### 3.6 Things that do **not** help, recorded so they are not retried

- **Raising `DETAIL_CHUNK_SIZE` to save points.** It does save points, but the
  binding constraint is the 10-second window, not cost. Trading a hard failure
  on large repositories for a handful of points is the wrong direction.
- **Requesting `nodes` anywhere.** It prices a query by the objects it could
  return, making the cheapest route the most expensive one for exactly the
  largest repositories. Tests assert neither query contains it.
- **Reading account detail through REST.** One request per contributor;
  any large repository exhausts the REST budget on its own.
- **A second token, or unauthenticated requests.** Unauthenticated REST is 60
  requests per hour. Multiple tokens raise questions of attribution and terms
  of service that this tool should not answer on its own.

---

## 4. Nominatim

| Limit | Value | Consequence |
|---|---|---|
| Rate | 1 request / second | sets the wall clock of a cold run |
| User agent | must identify the application and a contact | penalty is **blocking the agent** |
| Bulk use | discouraged | this tool caches to disk to keep volume low |

The penalty is what makes this stricter than politeness: a blocked user agent
fails every *later* run rather than the one that misbehaved.
`GEOCODER_USER_AGENT` still defaults to the generic string `github-metrics`,
which the policy asks not to use — **set it to something identifying you.**
That remains an open item in `ROADMAP.md`.

Measured: 143 distinct locations for one repository's 396 contributors, 175 of
whom published a location at all. Cold 186 s, warm 42 s.

---

## 5. Quick reference: what a run of *N* repositories costs

Using the measured per-repository figures for a large repository. Small
repositories cost close to the floor.

| Repositories | GraphQL points | REST requests | Fits in one hour? |
|---|---|---|---|
| 1 | ~9 | ~5 | yes |
| 100 | ~900 | ~500 | yes |
| 550 | ~4,950 | ~2,750 | at the GraphQL edge |
| 1,000 | ~9,000 | ~5,000 | **no — needs `--on-exhaustion wait`** |

Geocoding, not the API, sets wall-clock time on a first run. With a warm cache
the API becomes the constraint again.
