# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

v0.2.0's contributor dataset. The per-repository JSON in `docs/example.json`
turned out to be the metrics row plus a contributor block, not a second
dataset, so one `scan` now collects and writes both under one scan identity.

### Added

- **Contributor collection, in `scan`.** Every run writes one JSON document per
  repository at `<output>/<owner>/<repoid>.json`, carrying that repository's
  row followed by its contributors. Each contributor record has the account's
  id, display name, company, self-reported location, the address that location
  resolved to, and its commits in that repository.
- **The contributor block**: `contributors` plus five aggregates over it —
  `contribution_total`, `foreign_contribution`, `adversarial_contribution`,
  `foreign_percent`, `adversarial_percent`. These are **document keys, not CSV
  columns**; `githubmetrics.csv` stays at twenty. Promoting them to columns was
  considered — their grain is the repository, and it would make "which
  repositories carry the most foreign contribution" a spreadsheet sort — and
  rejected, because they exist only for a repository whose contributor list was
  read, which is exactly the set that produces a document. As columns they
  would be empty for precisely the rows with no document to explain the gap,
  and `contribution_total` would have to become optional to tell "no
  contributors" from "not collected", a distinction the document never has to
  make.

  A document is therefore the twenty columns, complete and in canonical order,
  then a fixed six-key suffix — which is a rule a test states in one line. The
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
  contributors could not be read.** Zero is a legitimate measurement — a
  repository really can have no contributors — so it cannot also mean "not
  collected". Caught by a test written for the failure path before the path had
  one. Keeping the aggregates out of the CSV settles it for good: a document
  exists only when the list was read, so a `0` there always means the list was
  read and was empty, and there is no state left for the number to be
  ambiguous about.
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

[Unreleased]: https://github.com/joey-huckabee/GitHub-Metrics/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/joey-huckabee/GitHub-Metrics/compare/8feb637...v0.1.0
