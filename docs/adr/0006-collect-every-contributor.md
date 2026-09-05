---
status: accepted
date: 2026-09-05
decision-makers: Joey
---

# Collect every contributor, not the top 25

## Context and Problem Statement

`DEFAULT_CONTRIBUTOR_LIMIT` was 25. It was never chosen for the current
design: it was carried over from the `contributors` sub-command that v0.2.0
retired, and `ROADMAP.md` has recorded it since as "inherited, not chosen".

The limit is not a tuning knob. It decides what `contribution_total` counts,
and therefore what `foreign_percent` and `adversarial_percent` will mean when
those definitions land. A percentage computed over the top 25 accounts of a
repository with 4,000 contributors is a percentage of a sample, not of the
repository, and nothing in the document said which.

The documents this tool writes are consumed by a separate stage that makes a
residency determination per contributor. That stage cannot ask about a
contributor this tool did not collect. A truncated list therefore does not
merely reduce precision — it silently removes people from a population that
the downstream analysis is trying to characterise, and removes them
*non-randomly*, since the ones dropped are exactly the long tail of
occasional contributors.

## Decision Drivers

* The downstream residency analysis needs the whole contributor population,
  not its head
* `contribution_total` and the percentages over it must describe the
  repository rather than a sample of it
* A run's cost must still be knowable well enough to refuse a run that cannot
  finish
* The GraphQL detail query must stay inside GitHub's 10-second processing
  window

## Considered Options

* **Keep 25**
* **Raise to a larger fixed cap** — 100, or 500
* **Collect every contributor GitHub returns**
* **Restore a `--contributors N` flag** and let the caller decide

## Decision Outcome

Chosen: **collect every contributor GitHub returns**.
`DEFAULT_CONTRIBUTOR_LIMIT` becomes `None`, meaning no limit applied by this
tool. The parameter survives so a library caller can still bound a run, but
nothing in the CLI sets it.

A fixed cap was rejected for the same reason 25 was: any cap makes
`contribution_total` a sample whose size is a property of this tool rather
than of the repository, and every cap needs the same justification nobody
could give for 25. Restoring a flag was rejected because ADR-0005's reasoning
still holds — a run's output should be readable from the command that produced
it, and a flag that changes what a metric *means* is worse than one that
changes what is written.

### The ceiling is GitHub's, and it is documented rather than removed

"Every contributor" means every contributor the API attributes to an account.
GitHub links only the **first 500 author email addresses** in a repository to
accounts; past that, contributions come back as anonymous entries with no
account attached. This tool leaves `anon` unset — GitHub's default — so those
entries are not requested and every collected contributor has a real account.

That ceiling is not removable by any request shape available here, so
`METRICS.md` documents it as a property of the source instead. It is also why
the definition of `contribution_total` still reads "collected" rather than
"every contributor the repository has ever had".

### Consequences

* Good: the contributor population is complete up to GitHub's own ceiling, and
  the percentages derived from it describe the repository
* Good: no arbitrary constant sits between the data and the analysis
* Bad: **totals from v0.4.1 and earlier are not comparable with totals from
  this release.** Nothing in a document records which limit produced it; the
  `scan_id` distinguishes runs, and the release notes distinguish the rule
* Bad: run cost per repository is no longer a constant, so the budget
  pre-flight weakens from a guarantee to a floor. See below
* Bad: wall-clock time rises roughly with contributor count, dominated by
  geocoding. [ADR-0007](0007-persistent-geocode-cache.md) is what makes that
  survivable across runs

## The two mechanisms this forced

### The REST list is paginated, and the page size is raised

25 fitted in PyGithub's default page of 30, which is the only reason
`REQUESTS_PER_REPOSITORY` was ever exactly 1. Unbounded collection reads every
page, so the client now sets `per_page = 100` — the endpoint's documented
maximum — making a 500-contributor repository 5 requests rather than 17.

### The aliased detail query is chunked

The detail query asks for every contributor's name, company and location as
aliased single-object `user(login:)` selections. Its **cost** was never the
constraint: GitHub's point formula counts connections, this query has none, so
it is one point however many aliases it carries. The constraint is the
**10-second processing window** — GitHub terminates a query that exceeds it,
and several hundred aliased account lookups in one document is not a safe bet
against that.

The query is therefore issued in chunks of `DETAIL_CHUNK_SIZE` accounts, and
the results merged. Aliases are numbered within their chunk, so no document
grows past that bound however large the repository.

## Budget: a guarantee becomes a floor, and says so

`check_budget` existed to make a promise: a run that starts can finish,
because the per-repository cost was exactly known. That promise cannot
survive an unbounded list — the cost depends on a contributor count nobody
has before spending the request that reveals it.

Rather than replace it with an estimate built from a made-up average, the
check is redefined as a **lower bound**. `MIN_POINTS_PER_REPOSITORY` and
`MIN_REQUESTS_PER_REPOSITORY` are what a repository costs at minimum — one
metrics query, one detail chunk, one contributor page — and the pre-flight
refuses any run that cannot afford even that. Passing is now **necessary but
not sufficient**, and the log line, the docstring and `ROADMAP.md` all say so
in those words.

This is a real reduction in what the tool can promise, recorded here rather
than glossed. The alternative — inventing an average contributor count and
calling the product an estimate — would produce a number that looks like the
old guarantee and is not one, which is the failure mode this repository
consistently refuses.

## More Information

* `METRICS.md`, "What the total counts", for the metric definition
* [ADR-0005](0005-one-scan-command-and-per-repository-json.md) for why the
  limit is not a flag
* [ADR-0007](0007-persistent-geocode-cache.md) for the geocoding cost this
  change multiplies
* GitHub REST, "List repository contributors", for the 500-email ceiling and
  the `per_page` maximum
* GitHub GraphQL, "Rate limits and query limits", for the 10-second window and
  the connection-based cost formula
