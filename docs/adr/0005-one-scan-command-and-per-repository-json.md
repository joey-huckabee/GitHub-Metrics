---
status: accepted
date: 2026-09-02
decision-makers: Joey
---

# One `scan` command, producing both artifacts

> The command shape, the on-disk layout, the column set and what happens to a
> repository that cannot be read are all decided and implemented. The
> contributor block's judgement fields - `foreign` and `adversarial` - remain
> **TBD** and are marked as such below. They are collected as `null` rather
> than omitted, so the shape is stable while the definitions are settled.

## Context and Problem Statement

v0.1.0 split the scaffolding command `repo` into two: `metrics`, which wrote
`githubmetrics.csv` for one point per repository, and `contributors`, which
walked contributor pages and was far more expensive. The split was made on
cost: `metrics` must never pay for contributor pages.

`docs/example.json` is the agreed shape for the per-repository document, and
reading it against `model/software.py` shows the split does not hold. Strip the
`contributors` array, and what remains is **exactly the columns `SoftwareRow`
defines** - the same identity, the same measurements, the same six score
components, the same total, and the five contribution aggregates that are
themselves per repository.

The document is therefore not a second dataset. It is the row plus a
contributor array.

That creates the problem this ADR exists to solve. Both artifacts carry
`scan_id` and `scan_date`, and those are **per run, assigned before any
repository is fetched**. Two invocations produce two UUIDs and two timestamps,
so a `githubmetrics.csv` and a folder of documents collected minutes apart
cannot be joined, cannot be grouped by run, and cannot be stored in the same
table without inventing a third key to relate them. v0.3.0's persistence work
makes that worse, not better: `scan_id` exists precisely so stored rows can be
grouped by the run that produced them.

## Decision Drivers

* One collection run must produce one scan identity, whatever it writes
* The two artifacts must join without a translation table
* Every command takes the same input vocabulary: slugs, URLs and CSV
  inventories, mixed freely
* A repository that cannot be read must still produce an output
* What a run produced must be predictable from the command, not from its flags
* Retired names are not reused (`repo` was retired in v0.1.0)

## Considered Options

* **A: A third command that runs both**, leaving `metrics` and `contributors`
  in place
* **B: `--scan-id` and `--scan-date` flags**, so two invocations can be pinned
  to one identity
* **C: `contributors` collects the metrics too**, and gains a flag to also
  write the CSV
* **D: One command, `scan`**, with contributor collection behind a
  `--contributors` flag
* **E: One command, `scan`, with no flag** - every run writes both artifacts

## Decision Outcome

Chosen option: **E - one command, `scan`, writing both artifacts every time.**
`metrics` is renamed to `scan`; `contributors` is removed.

```
github-metrics scan inventory.csv
github-metrics scan inventory.csv --output results/
```

One invocation, one `ScanIdentifier`, every artifact it writes stamped with the
same pair. The alignment problem does not need solving, because the two
artifacts stop being two runs.

**B was rejected** because it moves a correctness burden onto the operator.
Nothing would stop two runs an hour apart sharing a `scan_date`, and the result
is a stored row that misreports when it was measured - an error that looks
like data, which is the failure mode `GM-COL-003` already exists to prevent
elsewhere.

**A was rejected** because the third command would be a superset of the other
two, and the two would survive only as the ways to get half of it. Three
commands where two are subsets of the third is a menu, not a design.

**C was rejected** on naming rather than mechanics - it is D with the flag
inverted and a worse name. A command called `contributors` that collects stars,
forks, releases and six score components does not say what it does.

**D was rejected**, having been the decision this ADR originally recorded. The
flag buys exactly one state that nothing else expresses - documents without a
table - and charges for it in every other way. What a run produced stops being
readable from the command and starts depending on how it was invoked, which is
the property that matters when someone finds a `githubmetrics/` directory six
months later and needs to know whether it is complete.

It also does not save what it appears to save. The repository query returns
every column of a row for one point whether or not a document is written, so
the flag is a cost lever in one direction only: `--contributors` adds the
contributor pages, while its absence saves nothing on the metrics side that
was ever expensive. A flag that is asymmetric in what it costs but symmetric
in what it looks like is a flag that will be set wrongly.

`scan` is the name because a run is already called a scan everywhere else in
the codebase: `ScanIdentifier`, `scan_id`, `scan_date`. The command and the
identity it stamps now share a word.

**`repo` was not reused.** It was retired in v0.1.0, and a `repo` that means
something new is worse than a new word - the same rule that keeps error codes
and requirement identifiers permanent. `metrics` and `contributors` are
retired on the same terms.

### What `scan` writes

Both, always, into one directory:

| Artifact | Path | Contents |
|---|---|---|
| Tabular | `<root>/githubmetrics.csv` | one row per accepted reference, in input order |
| Documents | `<root>/<owner>/<repoid>.json` | that repository's row, then its contributors |

`<root>` is `--output`, or `githubmetrics` when none is given. Keeping both in
one directory is not tidiness: they share a `scan_id`, and separating them by
default would undo the joinability this decision is about.

`--format` selects the tabular artifact's form - `csv`, `json`, or `console`
to print it instead of writing it. It does not reach the documents, which are
always JSON and always written: there is no console rendering of four hundred
documents, and no CSV form of a nested contributor array. `--fields` likewise
filters only the tabular artifact, because a document with columns missing
would stop being the row it has to join with.

### The column set

**The two artifacts carry the same fields.** `SoftwareRow` gained the five
per-repository contribution aggregates - `contribution_total`,
`foreign_contribution`, `adversarial_contribution`, `foreign_percent`,
`adversarial_percent` - so the CSV header is twenty-five columns and a document
is those twenty-five keys in canonical order followed by `contributors`.

The aggregates are columns rather than document-only keys because they are per
repository, which is the CSV's grain. Leaving them out would mean answering
"which repositories have the most foreign contribution" by parsing four hundred
JSON files, and it would make the two artifacts disagree about what a
repository record contains.

`foreign_contribution`, `adversarial_contribution`, `foreign_percent`,
`adversarial_percent` and the per-contributor `foreign` and `adversarial` are
**collected as `null`** until `METRICS.md` defines them. Reserving the columns
is not the same as implementing the metric: a `null` publishes no number and
makes no claim, whereas a `0` would assert that a repository has no foreign
contribution - an assertion about named people that nothing has measured.

### Where the JSON files go

```
<root>/<owner>/<repoid>.json
```

One directory per owner, lower-cased throughout, and **no file at all for a
repository that could not be fully collected.**

**One directory per owner, not one flattened name.** The alternative,
`<owner>-<repoid>.json`, silently loses repositories. Hyphens are legal in both
an account name and a repository name, so `foo-bar/baz` and `foo/bar-baz` both
flatten to `foo-bar-baz.json`. They are two different repositories rather than
a duplicate pair, so the duplicate detection that already guards the inventory
(`GM-ING-015`) correctly reports nothing, and one file still overwrites the
other. Nothing warns, and the run exits 0. A path separator is not a legal
character in either name, so the nested form cannot express that ambiguity at
all - the collision is removed by construction rather than detected.

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
is* rather than of how someone happened to type it in the inventory.

The grammar in `validation.py` restricts both names to ASCII (`[A-Za-z0-9-]`
and `[A-Za-z0-9._-]`), so lower-casing is unambiguous and `str.lower()` and
`str.casefold()` agree. `.` and `..` are already rejected as repository names,
so no path segment can escape the output directory.

Windows reserved device names were checked rather than assumed: `con.json`,
`nul.json` and `com1.json` all create, list and read back correctly on
Windows 11, because the reservation applies to the bare name and these always
carry the `.json` suffix. No sanitising is needed.

**No file for a repository that could not be fully collected.** Two ways that
happens, and both produce a row and no document:

- the repository could not be read (`GM-COL-001`, `GM-COL-003`), so the row
  carries identity and no measurements;
- the repository was read but its contributor list was not (`GM-COL-005`), so
  the row carries its measurements and an empty `contribution_total`.

The run warns, continues, and exits 4 in the first case.

The asymmetry with the CSV is deliberate and is the whole point. A CSV row is
*positional* - the contract is one row per accepted input row, so omitting one
would shift what every later row means and silently change the file. A
directory has no positions, so an absent file is unambiguous on its own: the
repository was named, and nothing was measured.

Writing the file anyway would be the worse of the two. A document holding
identity, an empty contributor array and a `contribution_total` of zero is
indistinguishable, to anything that reads a directory of them, from a
repository that genuinely has no contributors. That is an error that looks
like data, which is the same failure `GM-COL-003` refuses a moved repository
to avoid. The CSV can afford the empty row because its empty *fields* say
"not measured"; a JSON file has no equivalent, because the aggregate that
would have to be empty is a number that reads perfectly well as zero.

### What a run costs

Per repository, from two separate hourly budgets:

| Currency | Cost | Of |
|---|---|---|
| GraphQL points | 2 | 5,000 |
| REST requests | 1 | 5,000 |

One GraphQL point for the metrics query and one for the aliased
contributor-detail query; one REST request for the contributor list, which is
the only route that reports commits attributed per account. GraphQL binds
first, at 2,500 repositories an hour. `check_budget` refuses a run that does
not fit **either** budget before collecting anything.

The contributor detail deliberately does not come from REST. The contributors
payload is a minimal account object with no name, company or location, so
reading those through PyGithub completes each account lazily - one request per
contributor, 26 per repository at the collection limit, and a 200-repository
inventory exhausts REST's budget before it finishes. Aliasing the accounts into
one GraphQL document makes a repository's cost independent of how many
contributors it has. Every alias selects a single object, so the document
carries no `nodes` and is not priced by how many accounts could come back -
the same condition `collect/repository.py` is held to.

### Consequences

Good:

- One run, one scan identity, joinable artifacts, and a shape v0.3.0's store
  can group without a fourth key
- What a run produced is readable from the command alone
- The CSV and the documents carry identical fields, so a consumer that has one
  needs no mapping to read the other
- `contributors`' unsettled JSON output stops being a separate contract

Bad:

- **The cheap path is gone.** Every run now pays for contributor pages, and
  every run geocodes. There is no longer a way to collect metrics alone, which
  was the entire justification for the v0.1.0 split. That is accepted as the
  price of one identity per run; if it turns out to hurt, the lever to revisit
  is `DEFAULT_CONTRIBUTOR_LIMIT` rather than a flag.
- **Geocoding sets the pace of a large run.** Nominatim's usage policy permits
  one request per second, enforced in `geo.py` because the penalty for
  exceeding it is the service blocking the user agent - which fails every
  later run rather than the one that misbehaved. The per-run cache means the
  cost is the number of *distinct* locations rather than of contributors, but
  a first run over a large inventory is measured in hours, not minutes.
- The two artifacts no longer have the same number of entries. A run over four
  hundred repositories with three unreadable writes four hundred CSV rows and
  three hundred and ninety-seven documents, so a consumer joining them must
  treat a missing file as an outcome rather than as an error. The exit code and
  the warning both say so, but it is a difference that has to be known.
- `metrics` is renamed one release after `repo` was replaced by it, and
  `contributors` is removed one release after it was introduced. Three command
  changes in two releases is churn. It is taken now rather than later because
  the alternative is `scan_id` and a command called `metrics` disagreeing about
  what a run is called for the life of the tool.
- Four columns and two contributor fields ship as permanently `null` until
  their definitions land.

Neutral:

- The `metrics` and `contributors` names are retired, not recycled
- `--geocode` is gone; geocoding is unconditional
- `--contributors N` is gone; the limit is `DEFAULT_CONTRIBUTOR_LIMIT`

## Open questions

**TBD - the contributor block's judgement fields.** `foreign` and
`adversarial` appear in `docs/example.json` with no definition anywhere in the
repository. Both are blocked on code Joey is supplying: `foreign` resolves
against the United States, and `adversarial` has no agreed rule yet. Neither
is computed before it is defined in [`../METRICS.md`](../METRICS.md); both are
collected as `null` so the shape does not change when they land.

**To verify against the live API.** `POINTS_PER_REPOSITORY` is 2 on the
strength of GitHub's documented cost formula rather than a measurement. The
metrics query's one point was measured; the contributor-detail query's has
not been. The repository's own convention is that a cost is measured rather
than assumed, so this is a debt to settle with a real token.

**Settled.** The command shape, the column set, the on-disk layout, the
filename casing, and what happens to a repository that could not be fully
collected.

## More Information

- Column definitions: [`../METRICS.md`](../METRICS.md)
- The example this shape follows: [`../example.json`](../example.json)
- Exit codes, unchanged by this decision:
  [`0004-exit-code-scheme.md`](0004-exit-code-scheme.md)
