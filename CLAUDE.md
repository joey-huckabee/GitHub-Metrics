# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with
code in this repository.

## Project Overview

GitHub-Metrics is a Python library and CLI that reads an inventory of GitHub
repositories and calculates comparable metrics about them for free and open
source software analysis.

The work splits in two, and the split is the most important thing to know about
this codebase:

1. **Ingestion** turns a list of repositories into validated references. It is
   offline, fast, and needs no credentials.
2. **Collection** turns those references into metrics by calling the GitHub
   API. It needs a token and it spends rate limit.

v0.1.0 shipped `githubmetrics.csv`. v0.2.0 shipped the contributor dataset:
one `scan` produces **two artifacts under one scan identity** - that CSV, and
one JSON document per repository carrying the same row followed by its
contributor block. There is no flag selecting between them; see
`docs/adr/0005-one-scan-command-and-per-repository-json.md` for why.

`foreign` and `adversarial` are emitted as `null` and nothing computes them,
along with the four aggregates derived from them. They are defined in neither
`METRICS.md` nor code, which is the rule holding rather than an oversight.

`docs/ROADMAP.md` covers each version and `CHANGELOG.md` the release history.

**Metric definitions and scoring bands are still being agreed.** `docs/METRICS.md`
is the working document, and entries marked **TBD** are not settled. Nothing is
implemented before it is defined there — a metric with an undocumented
definition is not comparable across repositories, which is the only reason to
collect it.

## Common Commands

```bash
# Setup
poetry install --with dev
poetry run pre-commit install

# The gate. `make check` is exactly what CI runs.
make check          # lint + types + tests + dead code + trace matrix
make format         # black, isort, ruff --fix
make lint           # black, isort, ruff, pylint (no writes)
make types          # mypy --strict
make test           # pytest, integration tests deselected
make dead           # vulture
make trace          # regenerate docs/TRACE-MATRIX.md
make trace-check    # fail if the committed matrix is stale
make mutants        # mutation check; minutes, not seconds, and not in `check`

# Without make
poetry run pytest -m "not integration"
poetry run mypy --config-file mypy.ini
poetry run python scripts/build-trace-matrix.py --check

# The CLI
poetry run github-metrics validate inventory.csv
poetry run github-metrics validate inventory.csv --format json --output out.json
poetry run github-metrics scan inventory.csv --output ./results/
poetry run github-metrics scan pypa/virtualenv github.com/psf/requests
poetry run github-metrics bands
poetry run github-metrics bands releases
poetry run github-metrics rate-limit
LOG_LEVEL=DEBUG poetry run github-metrics validate inventory.csv
```

## Architecture

### The one structural rule

**Ingestion never touches the network. Collection never touches a disk format.**

Everything else follows from it. `sources/` imports nothing that can open a
socket; `metrics`/`collect` imports nothing that parses a file format. Neither
imports the other; they meet only at the CLI.

This is the rule most likely to be broken by a well-meaning change — adding an
existence check to ingestion looks like an improvement. It would cost the
offline guarantee (L1-ING-002), the credential-free CLI path (L2-CLI-002), and
the ability to diagnose a bad inventory before spending API quota. Raise an ADR
before doing it.

### Module map

| Module | Responsibility | Network |
|---|---|---|
| `cli` | Argument parsing, rendering, exit codes | via collection |
| `sources/` | Slugs, URLs and CSV inventories to validated `RepositoryRef` values; concurrency across files | never |
| `validation` | Account and repository name grammar; returns reasons, not booleans | never |
| `errors` | Stable `GM-<AREA>-<NNN>` taxonomy, exceptions and `RowIssue` | never |
| `logger` | Logging configuration, once, at startup | never |
| `config` | `Settings` from environment; requires `GITHUB_TOKEN` | never |
| `model/` | `ScanIdentifier`, `SoftwareRow`, `Contributor`, `Address` | never |
| `output/` | CSV, JSON, console, per-repository documents; field selection; destinations | never |
| `client` | Authenticated PyGithub wrapper | yes |
| `collect/` | Collection: repository, counts, timestamps, contributors, credentials | via `client` |
| `analysis/` | Scoring. Band tables and the weights they produce | never |
| `geo` | Location to a structured address (Nominatim, paced at one per second) | yes |
| `geocache` | The on-disk geocode cache: format, expiry, atomic write | never |

`sources/` is where a repository gets named, in any of the three forms a
command accepts: a slug, a GitHub URL, or a CSV inventory. `resolve_sources`
decides which is which by rules applied in a fixed order — URL, existing file,
`.csv` suffix, slug — and returns references in the order the arguments were
written. Every command that takes repositories takes all three, so nobody has
to remember which command wants which.

`collect/` now owns the run as well as the queries: `budget.py` refuses a run
the token cannot cover, and `runner.py` collects concurrently while returning
results in input order. `analysis/row.py` is the single place a collected
repository becomes an output row — the seam where the structural rule would
otherwise be broken first.

### Ingestion pipeline

```
bytes -> NUL check -> UTF-8 decode -> csv.reader -> header -> rows -> result
```

Three properties are deliberate:

- **The whole file is read into memory, not streamed.** Inventories are
  hundreds to a few thousand short rows, so the cost is trivial, and holding
  the rows lets a duplicate on line 900 be reported against its first
  occurrence on line 12 without a second pass.
- **Validation is layered so each failure has exactly one code.** Field count,
  then emptiness, then grammar, then duplication. A row stops at its first
  failure.
- **Line numbers are physical**, so they match what the analyst's editor shows.

### Error model

Two shapes, chosen by **blast radius**, not by severity:

- **Exceptions** when the whole unit of work is impossible (missing file,
  unreadable header, bytes that are not text). Returning an empty result would
  let a caller mistake "broken" for "empty".
- **`RowIssue` records** when one row is spoiled. These are *data*, not control
  flow, which is the entire reason every problem in a file can be reported in
  one pass. Strict mode is the single place the two meet: it promotes the first
  `RowIssue` to an exception.

## Requirements and traceability

Three levels, traced to tests by `@pytest.mark.requirement` markers:

| File | Level | Content |
|---|---|---|
| `docs/L1.md` | Product | SHALL statements about *what* the tool does, plus non-requirements |
| `docs/L2.md` | Architecture | *How* each L1 is structurally satisfied |
| `docs/L3.md` | Implementation | Concrete obligations; where tests attach |
| `docs/TRACE-MATRIX.md` | Generated | Forward trace, artifacts and status |

**Status is derived, never written.** `scripts/build-trace-matrix.py` computes
it from markers, and CI runs it with `--check`. A document that records its own
status will eventually claim coverage the tests do not provide.

A marker naming an id that no document declares is a **hard error**, so a typo
in a marker fails the build rather than quietly reading as untested.

Requirements verified without a test (Inspection, Analysis, Demonstration) must
declare **both** a verification method and an `**Evidence**` line naming the
artifact that carries the check. A method with no evidence is a plan, not a
result, and the matrix reports it as Draft.

## Reference docs

- `docs/METRICS.md` — field definitions and scoring bands (the working document)
- `docs/ERROR-CATALOG.md` — every `GM-*` code, its cause and its resolution
- `docs/SCAN-PROCESS.md` — the run end to end, every corner case, and a
  numbered list of known deficiencies. Read before changing collection
- `docs/API-LIMITS.md` — GitHub and Nominatim ceilings, measured costs, and
  the workarounds that do and do not work
- `docs/ARCHITECTURE.md` — how the pieces fit and why
- `docs/CLI-REFERENCE.md` — every flag and exit code
- `docs/USER-GUIDE.md` — task-oriented introduction
- `docs/MAINTAINER-GUIDE.md` — working on the code
- `docs/ROADMAP.md` — what is deferred, and to which version
- `docs/adr/` — decisions with real alternatives, in MADR format

## Conventions worth preserving

These are the non-obvious ones. Most were learned by getting them wrong first.

- **Metric fields default to `None`, never to `0`.** Zero is a legitimate
  measurement — a repository really can have zero releases and zero closed
  issues — so using it for "not collected" would make a measured repository
  indistinguishable from one that could not be read, and every downstream
  aggregate would absorb the difference silently. `None` renders as an empty
  CSV field and as JSON `null`. Identity fields default to `""` instead,
  because they come from the input row and the scan rather than from the API
  and are known even for a repository that 404s.
- **The output column set is derived from `SoftwareRow`'s field order**, not
  from a parallel list. A hand-maintained second list of column names is the
  mechanism by which the CSV, JSON and console formats drift apart.
- **Concurrency goes across files, not within one**, and determinism is part of
  the requirement rather than a quality attribute. Results return in **input
  order** (`Executor.map`, never `as_completed`), and when several sources fail
  the error raised belongs to the **earliest source in input order**, not to
  whichever thread failed first. That second half only becomes visible once a
  batch contains two bad files, and then it presents as a flaky error message
  rather than as a concurrency bug. See ADR-0002 for why parallelising rows
  *within* a file would be slower, not faster.
- **`tests/data/**` is marked `-text` in `.gitattributes`.** The repository is
  LF everywhere else, but a CRLF fixture that git normalised to LF would
  silently stop testing CRLF handling *and the test would still pass*. Fixtures
  are byte-exact inputs; never normalise them.
- **Non-ASCII source is fine. This entry used to say the opposite.** The claim
  was that vulture reads source with the locale encoding, so a file containing
  an em-dash was silently skipped on a cp1252 console. That is not true of the
  pinned version (>=2.13): `vulture.utils.read_file` uses `tokenize.open`,
  which honours PEP 263 and defaults to UTF-8, and a file it genuinely cannot
  read sets `ExitCode.InvalidInput` rather than passing quietly. Checked by
  running vulture on a cp1252 console against two identical files, one with an
  em-dash and one without: both reported the same unused function.

  The `\uXXXX` escapes in `scripts/build-trace-matrix.py` and
  `github_metrics/errors.py` are therefore unnecessary. They are also harmless
  — they preserve every runtime string exactly — so they stay until there
  is a reason to touch those lines. Do not add more, and do not reshape prose
  to avoid a dash.

  **Refined in v0.6.0: the safe set is what cp1252 can encode, which is not
  all of Unicode.** A box-drawing character (U+2502) in a docstring made
  vulture report `Unable to parse file ... 'charmap' codec can't encode` as a
  *warning* and skip the file entirely - a dead-code check that silently
  stopped checking. An em-dash survives because cp1252 has one at 0x97; box
  drawing, arrows and most symbols have no mapping at all. So: dashes and
  accented text in prose are fine and stay, but **draw diagrams in ASCII**.
  Markdown is unaffected - nothing lints it with the locale encoding.
- **When editing files programmatically on Windows, write bytes or pass
  `newline="\n"`.** `Path.write_text()` translates `\n` to `os.linesep`, which
  rewrites the whole file with CRLF and turns a ten-line change into a
  233-line diff. `.gitattributes` pins `* text=auto eol=lf`, but the working
  tree still churns.
- **`reset_logger()` configures a process-wide singleton** and sets
  `propagate = False` on the `github_metrics` logger. An autouse fixture in
  `tests/conftest.py` restores it after every test; without that, a test that
  runs the CLI stops `caplog` from seeing records in tests that run
  *afterwards* — a latent flake that only appears once someone writes a log
  assertion.
- **Per-repository narration is DEBUG; INFO is per run.** The unit of work is
  the repository and an inventory holds hundreds, so one INFO line per
  repository is hundreds of lines burying whatever the operator needed to see.
  A value worth explaining is DEBUG; a value worth doubting is a WARNING.
  `sources/csv_inventory.py`'s per-file summary is the one INFO line in the package, and it is
  per run rather than per row. A test asserts that collecting an unremarkable
  repository emits nothing at INFO or above.
- **Logs go to stderr, always.** The CLI writes JSON to stdout, and an
  interleaved log line corrupts it for every downstream consumer. This is why
  `reset_logger()` defaults to stderr even though a general-purpose logging
  helper might not.
- **Modules name a logger; they never configure one.** `logging.getLogger(__name__)`
  and nothing else. Only `logger.py` attaches handlers.
- **A budget is read from the API that owns it, never from `/rate_limit`.**
  That endpoint does not track spend: measured, it reported 5000 remaining for
  both budgets while the token had 4988 GraphQL points and 4984 REST requests
  left. `check_budget` read it until v0.6.0 and was therefore validating runs
  against a number that never moves. GraphQL's own `rateLimit { remaining }` is
  authoritative and a document selecting nothing else is **not charged**, so
  the right source is also the free one. REST's `X-RateLimit-Remaining` header
  is accurate but a GraphQL response overwrites PyGithub's copy of it, which is
  why `statistics.json` publishes `null` for REST spend rather than a number it
  cannot stand behind. `rate_limit_snapshot` still calls `/rate_limit`, and
  that is correct - it verifies credentials, where only the status code and the
  scope headers matter.
- **Counts come from GraphQL, never REST.** REST cannot count closed issues
  correctly at any price: the repository object has no closed count, its
  `open_issues_count` includes pull requests, the issues endpoint returns pull
  requests with no server-side filter, and since GitHub moved that endpoint to
  cursor pagination there is no `rel="last"` - so PyGithub's
  `PaginatedList.totalCount` now returns **1** for every repository, silently.
  GraphQL returns an exact `totalCount`, excludes pull requests by
  construction, and costs one point per repository for the whole query.
  `Requester.graphql_query()` is on PyGithub already, so this adds no
  dependency.
- **No query asks for `nodes`.** GraphQL bills per query rather than per field,
  so `collect/repository.py` folds every value a row needs into one document
  costing **one point** - measured, not assumed - and
  `collect/contributors.py` folds contributor detail into aliased ones. The
  condition that keeps both cheap is the absence of a `nodes` selection:
  `nodes` prices a query by the number of objects it could return, so adding
  one would make the cheapest route the most expensive one for exactly the
  largest repositories, and nothing would fail visibly. Tests assert neither
  query contains `nodes`.

  The contributor **list** is the one REST endpoint in a scan, because GraphQL
  has no connection reporting commits attributed per account. Its *details* are
  deliberately not REST: the contributors payload carries no name, company or
  location, so PyGithub would complete each account lazily at one request each,
  and any large repository would exhaust the REST budget on its own.

- **The contributor list is unbounded, and that changed what the budget can
  promise** (ADR-0006). `DEFAULT_CONTRIBUTOR_LIMIT` is `None`: every
  contributor GitHub attributes to an account is collected, because
  `contribution_total` and the percentages over it should describe the
  repository rather than a 25-account sample of it. Three consequences, none
  optional:

  - the REST list is paginated at `client.PER_PAGE` (100, the endpoint maximum)
    rather than PyGithub's default 30;
  - the aliased detail query is chunked at `DETAIL_CHUNK_SIZE`. This is **not**
    a cost control - a chunk costs one point whatever it carries, since the
    cost formula counts connections and this document has none. It is a
    **timeout** control: GitHub terminates any query it has not processed in
    ten seconds;
  - `check_budget` is now a **floor**, not a guarantee. `MIN_POINTS_PER_REPOSITORY`
    and `MIN_REQUESTS_PER_REPOSITORY` are the minimum, a repository's real cost
    depends on a contributor count nothing knows until the list is read, and
    passing the pre-flight is necessary but not sufficient. Do not "restore"
    this to an estimate by multiplying by an assumed average - a number that
    looks like the old guarantee and is not one is exactly the failure this
    repository keeps getting caught by.

  GitHub links only the first **500 author email addresses** to accounts, and
  `anon` is deliberately left unset, so every collected contributor has a real
  account and a repository past that ceiling reports a total below its true
  commit count. That is a property of the source; `METRICS.md` records it
  rather than the code working around it.

- **Two artifacts, two purposes.** `githubmetrics.csv` is the comparable
  table - twenty columns, fixed shape, sortable and diffable. A document is one
  repository's detail record: those twenty keys in canonical order, then
  `contributors` and the five aggregates over it. The contributor block belongs
  to the document because that is what the document is for, and because a
  nested array has no representation at the table's grain.

  What they share is the row. Every CSV column is a document key spelled the
  same way and in the same order, and both carry the same `scan_id`, so the two
  join on the run that produced them. `--fields` filters the tabular artifact
  only, because a document with columns missing would stop being the row it
  joins with. Keeping the block out of the row is also what lets
  `contribution_total` be a plain number rather than an optional one: a
  document exists only where the list was read.

- **A repository that was not fully collected gets a row and no document.** A
  CSV row is positional, so omitting one shifts what every later row means; a
  directory has no positions, so an absent file says "named, not measured" on
  its own. Writing it anyway would publish an empty contributor array and a
  `contribution_total` of zero, which nothing reading a directory of documents
  could tell from a repository that genuinely has none.

- **Geocoding is paced at one request per second, and that is not politeness.**
  Nominatim's policy penalty is blocking the user agent, which fails every
  later run rather than the one that misbehaved, so `geo.py` enforces it with
  `RateLimiter`. An address has three distinguishable states - never asked
  (all `None`), asked and unresolved (`query` only), and matched (`""` for
  components the match lacks) - because collapsing them would make an account
  publishing nothing look like one publishing `she/her`.

- **A service failure is never written to the geocode cache** (ADR-0007). The
  cache persists between runs, which is what makes unbounded contributor
  collection affordable to repeat, and it expires a match after a year and a
  miss after thirty days. The load-bearing rule is the third case: Nominatim
  being unreachable produces the *same* `Address(query=...)` a genuine miss
  does, and persisting it would let one outage poison those locations forever -
  every later run reading "unresolved" from the cache and never asking again.
  `Geocoder._ask` is where the three outcomes are told apart, and it is the
  only place they are. A service failure still memoises **in-process**, so
  eight workers do not each wait out the same dead lookup.

- **`geocache.py` owns the file; `geo.py` owns the socket.** That split is the
  structural rule applied to the cache: collection may not touch a disk format,
  so the CLI builds a `GeocodeCache` and hands it to the `Geocoder`. Do not
  move the JSON into `geo.py` because it is "only a cache".
- **`organization` is empty for a personally owned repository, and that is the
  answer.** GitHub says whether an owner is a `User` or an `Organization`, and
  empty is the only place a row records the former. Echoing the user's login
  into the column would make the two kinds of owner indistinguishable, and
  every aggregate by organisation would acquire one bucket per individual
  maintainer.
- **A moved repository is refused, not collected** (`GM-COL-003`). GitHub
  redirects a rename and a transfer silently, so a stale reference resolves and
  returns correct numbers about a repository the inventory does not name. That
  is worse than a failure: an error that looks like data survives review. The
  row is emitted with identity columns and no measurements, the warning names
  the current `owner/name`, and the run exits 4. Case never counts as a
  difference — GitHub names are case-insensitive, and refusing a working
  reference over its spelling would be a defect of its own.
- **GraphQL reports failure with HTTP 200 and an `errors` array.** Any code
  path that checks only the status sees success and then reads a null
  repository. Always inspect `errors`, and classify `NOT_FOUND` separately - a
  deleted or renamed repository is an expected outcome of a valid reference,
  not a defect.
- **CodeQL reads "trusted" as a secret.** Its sensitive-data heuristic
  classifies a value whose *name* contains that word, and taint tracking then
  reports any log line the value reaches as leaking a secret — it has fired
  three times in this repo on the trusted-organisation bonus, which is a
  published scoring weight with nothing to leak. Renaming the local or the
  parameter (`org_bonus`, `matched`) is the fix; the output column and the
  logged text stay `trusted_org_bonus`. Renaming does *not* work as a general
  technique — taint follows values, not names, so it only helps when the
  renamed thing is the classified source itself.
- **Score bands are data, not if/elif chains.** The implementation this
  replaced ended `< 500 -> 0.9` and `> 500 -> 1.0`, so a count of exactly 500
  matched no branch and returned the initial 0 - a plausible number rather than
  an error. A table cannot have that gap, can be tested as one object, and can
  be rendered into the docs rather than transcribed.
- **Python's `csv` module no longer rejects a NUL byte** — it passes one
  through. We check explicitly, because without it a binary file renamed
  `.csv` produces one misleading "invalid owner" per row instead of one
  accurate diagnosis.
- **Input is decoded as `utf-8-sig`, and that is the only place a BOM is
  handled.** Excel's "Save as CSV UTF-8" writes a byte-order mark, and without
  this the first header cell arrives as `<BOM>owner` and the tool reports a
  missing `owner` column against a file whose first line visibly says `owner`.

  `_normalise_header` used to strip one as well. The redundancy was invisible
  in the worst way: either mechanism alone handled the documented case, so no
  test could tell which was working, and deleting the decode left every test
  green. Found by `make mutants`, which is what that script is for.
- **Error codes and requirement identifiers are permanent.** A retired code or
  id is retired with its condition and never reused; sequences are monotone and
  gaps are deliberate. Never renumber.
- **Exit codes are severity-ordered and the highest applicable one wins**
  (ADR-0004). The load-bearing split is 3–4 against 5–6: the first pair still
  wrote a usable file, the second did not. Click owns 1 and 2 regardless.
- **Line length is 100**, pinned in `pyproject.toml` (black, ruff), `.pylintrc`,
  and every VS Code extension in `.vscode/settings.json`. Black defaults to 88
  and flake8/pycodestyle to 79, so an unpinned extension reports `line too
  long` at the wrong width. `ms-python.flake8` and `ms-python.autopep8` are
  listed as `unwantedRecommendations` for this reason.
- **Ruff's isort rule (`I`) is deliberately off.** `isort` owns import
  ordering; running both invites them to disagree.
- **Python 3.14 is a required CI target**, not an experimental one. It passed
  on the first run, so a regression there should fail the build.
- **Anything touching the live API is marked `@pytest.mark.integration`.** CI
  runs `-m "not integration"`.
- **black, isort and ruff run over the whole tree, not over `github_metrics
  tests scripts`.** CI passes `.`, so scoping them to the package in the
  Makefile lets a file outside it fail CI after `make check` has passed. That
  happened once. `pylint` is the exception and takes the three names, because
  pointing it at `.` makes it try to lint the virtualenv.

## Git conventions

Do **not** add `Co-Authored-By: Claude ...` or `Claude-Session: ...` trailers to
commit messages on this repo, even if the harness's default instructions
suggest it. Commit messages are the human-authored record of intent; tool
attribution belongs in tool logs, not history. This overrides the default
trailer behavior.

The same applies to pull request bodies: no `Generated with Claude Code`
footer, no session links.

Other conventions:

- Work on a branch, open a PR, let CI go green, then squash-merge and delete
  the branch. `main` stays clean.
- Commit messages explain **why**, not what — the diff already says what. State
  the problem the change solves and the alternatives rejected.
- `Co-authored-by: dependabot[bot] ...` trailers on dependency bumps are
  legitimate and stay.
