---
status: proposed
date: 2026-08-31
decision-makers: Joey
---

# One `scan` command, and a JSON file per repository

> **Draft.** The command shape, the on-disk layout and what happens to a
> repository that cannot be read are all decided. The contributor block's
> definitions - `foreign` and `adversarial` - remain **TBD** and are marked as
> such below. This ADR is expected to be rounded out before it moves to
> `accepted`.

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
| `githubmetrics.csv` | always, one row per accepted reference | `--output`, else the console |
| one JSON per repository | `--contributors`, and only for a repository that was read | a folder named `githubmetrics` |

The per-repository JSON follows `docs/example.json`: the twenty `SoftwareRow`
columns, then the contributor block.

### Where the JSON files go

```
githubmetrics/<owner>/<repoid>.json
```

One directory per owner, lower-cased throughout, and **no file at all for a
repository that could not be read.**

**One directory per owner, not one flattened name.** The alternative,
`githubmetrics/<owner>-<repoid>.json`, silently loses repositories. Hyphens are
legal in both an account name and a repository name, so `foo-bar/baz` and
`foo/bar-baz` both flatten to `foo-bar-baz.json`. They are two different
repositories rather than a duplicate pair, so the duplicate detection that
already guards the inventory (`GM-ING-015`) correctly reports nothing, and one
file still overwrites the other. Nothing warns, and the run exits 0. A path
separator is not a legal character in either name, so the nested form cannot
express that ambiguity at all - the collision is removed by construction
rather than detected.

It also groups the output the way an analyst reads it. An inventory is usually
organised by owner, and `ls githubmetrics/` answers "which organisations did
this run cover" without parsing filenames.

**Always lower case.** GitHub account and repository names are
case-insensitive, so `PyPA/virtualenv` and `pypa/virtualenv` name one
repository, and `RepositoryRef.key` already folds case to detect exactly that.
The filename has to agree with that identity or the tool contradicts itself:
on Linux the two spellings would produce two files for one repository, and on
Windows and macOS the second would overwrite the first while the run reported
two successes. Lower-casing makes the path a function of *which repository this
is* rather than of how someone happened to type it in the inventory, which is
also what makes the file findable without knowing the spelling used.

The grammar in `validation.py` restricts both names to ASCII
(`[A-Za-z0-9-]` and `[A-Za-z0-9._-]`), so lower-casing is unambiguous and
`str.lower()` and `str.casefold()` agree. `.` and `..` are already rejected as
repository names, so no path segment can escape the output directory.

**No file for a repository that could not be read.** The run warns, continues,
and exits 4 as it does today; the CSV still carries the identity-only row.

The asymmetry with the CSV is deliberate and is the whole point. A CSV row is
*positional* - the contract is one row per accepted input row, so omitting one
would shift what every later row means and silently change the file. A
directory has no positions, so an absent file is unambiguous on its own: the
repository was named, and nothing was measured.

Writing the file anyway would be the worse of the two. A JSON document holding
identity, an empty contributor array and a `contribution_total` of zero is
indistinguishable, to anything that reads a directory of them, from a
repository that genuinely has no contributors. That is an error that looks
like data, which is the same failure `GM-COL-003` refuses a moved repository
to avoid. The CSV can afford the empty row because its empty *fields* say
"not measured"; a JSON file has no equivalent, because the aggregate that
would have to be empty is a number that reads perfectly well as zero.

### Consequences

Good:

- One run, one scan identity, joinable artifacts, and a shape v0.3.0's store
  can group without a fourth key
- The cheap path stays the default: no flag, one point per repository
- `contributors`' unsettled JSON output stops being a separate contract to
  settle

Bad:

- The two artifacts no longer have the same number of entries. A run over four
  hundred repositories with three unreadable writes four hundred CSV rows and
  three hundred and ninety-seven JSON files, so a consumer joining them must
  treat a missing file as an outcome rather than as an error. That is the
  intended reading, and the exit code and the warning both say so, but it is a
  difference between the two that has to be known
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

**Settled — the filename layout.** See
[Where the JSON files go](#where-the-json-files-go) above.

The path is built from the **input** `owner` and `repoid`, not from what GitHub
reports. Since only a repository that was read successfully gets a file, and a
renamed or transferred one is refused rather than collected (`GM-COL-003`), the
two can differ only in case - which lower-casing settles. Building the path
from the input also means an operator can predict where a row will land without
running the tool.

Windows reserved device names were checked rather than assumed: `con.json`,
`nul.json` and `com1.json` all create, list and read back correctly on
Windows 11, because the reservation applies to the bare name and these always
carry the `.json` suffix. No sanitising is needed.

## More Information

- Column definitions: [`../METRICS.md`](../METRICS.md)
- The example this shape follows: [`../example.json`](../example.json)
- Exit codes, unchanged by this decision:
  [`0004-exit-code-scheme.md`](0004-exit-code-scheme.md)
