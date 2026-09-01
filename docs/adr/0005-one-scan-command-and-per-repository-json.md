---
status: proposed
date: 2026-08-31
decision-makers: Joey
---

# One `scan` command, and a JSON file per repository

> **Draft.** The command shape is decided. The contributor block's definitions
> and the on-disk filename layout are **TBD** and marked as such below. This
> ADR is expected to be rounded out before it moves to `accepted`.

## Context and Problem Statement

v0.1.0 split the scaffolding command `repo` into two: `metrics`, which writes
`githubmetrics.csv` for one point per repository, and `contributors`, which
walks contributor pages and is far more expensive. The split was made on cost:
`metrics` must never pay for contributor pages.

`docs/example.json` is the agreed shape for the contributor dataset, and
reading it against `model/software.py` shows the split does not hold. Strip the
`contributors` array and the five contribution aggregates, and what remains is
**exactly the columns `SoftwareRow` already defines** — the same identity, the
same measurements, the same six score components, the same total.

The contributor dataset is therefore not a second dataset. It is the metrics
row plus a contributor block.

That creates the problem this ADR exists to solve. Both artifacts carry
`scan_id` and `scan_date`, and those are **per run, assigned before any
repository is fetched**. Two invocations produce two UUIDs and two timestamps,
so a `githubmetrics.csv` and a folder of per-repository JSON collected minutes
apart cannot be joined, cannot be grouped by run, and cannot be stored in the
same table without inventing a third key to relate them. v0.3.0's persistence
work makes that worse, not better: `scan_id` exists precisely so stored rows
can be grouped by the run that produced them.

## Decision Drivers

* One collection run must produce one scan identity, whatever it writes
* The cheap path must stay cheap — collecting an inventory's metrics must not
  require paying for contributor pages
* Every command takes the same input vocabulary: slugs, URLs and CSV
  inventories, mixed freely
* A repository that cannot be read must still produce an output, as it does
  today
* Retired names are not reused (`repo` was retired in v0.1.0)

## Considered Options

* **A: A third command that runs both**, leaving `metrics` and `contributors`
  in place
* **B: `--scan-id` and `--scan-date` flags**, so two invocations can be pinned
  to one identity
* **C: `contributors` collects the metrics too**, and gains a flag to also
  write the CSV
* **D: One command, `scan`**, with contributor collection behind a flag

## Decision Outcome

Chosen option: **D — one command, `scan`, with contributor collection behind a
flag.** `metrics` is renamed to `scan`; `contributors` folds into it.

```
github-metrics scan inventory.csv                      # CSV only, one point per repository
github-metrics scan inventory.csv --contributors       # CSV and per-repository JSON
```

One invocation, one `ScanIdentifier`, every artifact it writes stamped with the
same pair. The alignment problem does not need solving because the two
artifacts stop being two runs.

**B was rejected** because it moves a correctness burden onto the operator.
Nothing would stop two runs an hour apart sharing a `scan_date`, and the result
is a stored row that misreports when it was measured — an error that looks like
data, which is the failure mode `GM-COL-003` already exists to prevent
elsewhere.

**A was rejected** because the third command would be a superset of the other
two, and the two would survive only as the ways to get half of it. Three
commands where two are subsets of the third is a menu, not a design.

**C was rejected** on naming rather than mechanics — it is D with the flag
inverted and a worse name. A command called `contributors` that collects stars,
forks, releases and six score components does not say what it does, and the
cheap path would be the flagged one, which puts the default on the expensive
side.

`scan` is the name because a run is already called a scan everywhere else in
the codebase: `ScanIdentifier`, `scan_id`, `scan_date`. The command and the
identity it stamps now share a word.

**`repo` was not reused.** It was retired in v0.1.0 and a `repo` that means
something new is worse than a new word — the same rule that keeps error codes
and requirement identifiers permanent.

### What `scan` writes

| Artifact | When | Default location |
|---|---|---|
| `githubmetrics.csv` | always | `--output`, else the console |
| one JSON per repository | `--contributors` | a folder named `githubmetrics` |

The per-repository JSON follows `docs/example.json`: the twenty `SoftwareRow`
columns, then the contributor block.

### Consequences

Good:

- One run, one scan identity, joinable artifacts, and a shape v0.3.0's store
  can group without a fourth key
- The cheap path stays the default: no flag, one point per repository
- `contributors`' unsettled JSON output stops being a separate contract to
  settle

Bad:

- `metrics` is renamed one release after `repo` was replaced by it. Two command
  renames in two releases is churn, and this one lands on the release
  deliverable's own name. It is taken now rather than later because the
  alternative is `scan_id` and a command called `metrics` disagreeing about
  what a run is called for the life of the tool
- `contributors` is removed. `--geocode` and `--contributors N` move to `scan`

Neutral:

- The `metrics` name is retired, not recycled, and will not be reused

## Open questions

**TBD — the contributor block's definitions.** `foreign` and `adversarial`
appear in `docs/example.json` with no definition anywhere in the repository.
Both are deferred to v0.2.0, and both are blocked on code Joey is supplying:
`foreign` resolves against the United States, and `adversarial` has no agreed
rule yet. Neither is implemented before it is defined in
[`../METRICS.md`](../METRICS.md).

**TBD — the filename layout.** Two candidates, and they are not equivalent:

| Layout | Collides? |
|---|---|
| `githubmetrics/<owner>-<repoid>.json` | **yes** |
| `githubmetrics/<owner>/<repoid>.json` | no |

Hyphens are legal in both account and repository names, so the flat form maps
`foo-bar/baz` and `foo/bar-baz` to the same `foo-bar-baz.json`. They are two
different repositories, not duplicates, so
[`ADR-0001`](0001-two-column-csv-as-the-inventory-contract.md)'s duplicate
detection cannot catch it — it correctly reports no duplicate, and one file
still overwrites the other. The nested form has no such case because the
separator is a path boundary rather than a legal name character.

The filename is built from the **input** `owner` and `repoid` rather than from
what GitHub reports, because a repository that could not be read still needs a
file and has no API-reported name. Case is not a concern: duplicate detection
already folds case (`RepositoryRef.key`), so `PyPA/virtualenv` and
`pypa/virtualenv` never both reach the writer and cannot collide on a
case-insensitive filesystem.

## More Information

- Column definitions: [`../METRICS.md`](../METRICS.md)
- The example this shape follows: [`../example.json`](../example.json)
- Exit codes, unchanged by this decision:
  [`0004-exit-code-scheme.md`](0004-exit-code-scheme.md)
