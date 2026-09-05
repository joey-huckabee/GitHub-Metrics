---
status: proposed
date: 2026-09-05
decision-makers: Joey
---

# `--deep-attribution`: walking commit history when the contributor list is not enough

## Context and Problem Statement

The contributor endpoint links only the **first 500 author email addresses** in
a repository to GitHub accounts. Measured on `NousResearch/hermes-agent`: 396
accounts collected out of 3,310 contributor identities — 12% of the people,
though 87% of the commits.

v0.6.0's no-reply recovery ([ADR-0008](0008-statistics-json.md)) lifts that to
1,163 people and 90.3% of commits by extracting the account id and login that
GitHub embeds in `NNN+login@users.noreply.github.com` addresses. The remaining
2,147 people publish real addresses, and GitHub deliberately exposes no
email-to-user lookup, so **no amount of work on that endpoint reaches them**.

Whether the gap matters depends entirely on the question being asked:

- *"Where does this project's work come from?"* — 90% of commits is a strong
  answer, and the missing tail is by construction the people who contributed
  least.
- *"Is there any adversarial contributor here?"* — a single one-commit account
  is exactly what is missing, and no sampling threshold is acceptable.

Both questions are in scope for this tool's consumers. One route serves both,
but only one of them can afford it.

## Decision Drivers

* Complete account attribution must be **reachable**, for the repositories
  where it matters
* It must never be the default: the cost is prohibitive at inventory scale
* A user must be **told** when a repository is one where it would matter,
  rather than having to inspect statistics to find out
* Nothing about the default path may change

## Considered Options

* **Do nothing.** The 90.3% ceiling stands.
* **Always walk the history.** Complete, and unaffordable.
* **An opt-in flag.** Chosen.
* **An opt-in flag, plus a threshold that recommends it.** Chosen — the flag
  alone leaves the user to discover which repositories need it.

## Decision Outcome

Chosen: **`--deep-attribution`, off by default, plus a warning driven by
`--deep-attribution-threshold` that recommends it per repository.**

### The mechanism

`repository.defaultBranchRef.target.history` attributes each commit to a GitHub
account wherever the author's email is linked to one, and **is not subject to
the 500-email ceiling**. It is the only route to complete attribution.

It is a connection, so it is priced by nodes and paginated at 100.

### The cost, measured

`NousResearch/hermes-agent` has **32,005 commits** on its default branch:
roughly **321 pages, 321 GraphQL points and 321 sequential round trips — for
one repository.**

For comparison, the default path collects the same repository for **9 points
and about 5 REST requests**. Deep attribution is therefore roughly **35x the
cost**, and the multiplier grows with commit count rather than with contributor
count.

At inventory scale it is disqualifying: 200 repositories of that size would
need 64,000 points against a 5,000-per-hour budget — about thirteen hours of
pure quota, before geocoding. This is why it is a flag and not a default, and
why the flag should be pointed at a short watchlist rather than an inventory.

### The threshold, and why a warning rather than an automatic escalation

A user should not have to read `statistics.json` to discover that a repository
was badly under-attributed. After collecting each repository the scan knows
what fraction of commits it could not attribute to an account, and when that
exceeds `--deep-attribution-threshold` (default **10%**) it warns:

```
WARNING  NousResearch/hermes-agent: 13.0% of commits (4,155 of 31,983) could
         not be attributed to a GitHub account, above the 10% threshold.
         For complete attribution re-run this repository with
         --deep-attribution (est. 321 GraphQL points, ~5 min).
```

The estimate is real: `history { totalCount }` is one point and
`statistics.json` already collects it.

**10% is a starting value, not a derived one.** It is set where the measured
repository falls above it, so the mechanism demonstrably fires on a real case;
it is expected to be tuned once a portfolio has been scanned. That it is
arbitrary is recorded here rather than implied by its presence.

Escalating automatically was rejected. A tool that silently turns a 9-point
repository into a 321-point one has spent a user's hourly quota on a decision
they did not make, and with `--on-exhaustion wait` now the default
([ADR-0009](0009-rate-limit-exhaustion-policy.md)) it could also silently turn
a five-minute run into an overnight one. Warning costs nothing and leaves the
decision where it belongs.

### What it changes in the output

Nothing structural. Deep attribution produces the same contributor records
through a different route, and `statistics.json` records which route was used:

```json
"attribution": {
  "method": "commit_history",
  "commits_walked": 32005,
  "coverage_percent": 100.0,
  "graphql_points_spent": 321
}
```

`method` is `contributor_list` by default. **Two runs of the same repository by
different methods are not directly comparable** — the deep run has a larger
contributor set and a larger `contribution_total` — so the field exists
precisely so that a consumer can tell them apart rather than diffing them
blindly.

## Consequences

* Good: the "is any adversarial contributor here" question becomes answerable,
  for a watchlist
* Good: users are told which repositories need it instead of guessing
* Good: the default path is untouched
* Bad: a third attribution mode means `contribution_total` can now mean one of
  two populations. `attribution.method` is the mitigation and it must be read
* Bad: 321 sequential round trips is slow even when affordable; it wants a
  progress line
* Bad: the threshold default is a guess until a portfolio has been scanned

## More Information

* [`API-LIMITS.md`](../API-LIMITS.md) §3.3 for the measured cost of the history
  route, and §2 for the ceiling that makes it necessary
* [ADR-0008](0008-statistics-json.md) for the statistics that make the
  under-attribution visible in the first place
