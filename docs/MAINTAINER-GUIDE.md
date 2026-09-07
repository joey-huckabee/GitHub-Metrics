# GitHub-Metrics — Maintainer Guide

For people changing the code. For using the tool, see
[`USER-GUIDE.md`](USER-GUIDE.md).

## Setting up

```bash
poetry install --with dev
poetry run pre-commit install
cp .env.example .env      # only needed for the API commands
```

Poetry creates an in-project `.venv`, which the VS Code workspace in
`.vscode/` picks up automatically.

## The gate

`make check` is what CI runs. Run it before pushing.

| Task | Command | What it enforces |
|---|---|---|
| Format | `make format` | black, isort, ruff `--fix` |
| Lint | `make lint` | black, isort, ruff, pylint |
| Types | `make types` | `mypy --strict` |
| Tests | `make test` | pytest with coverage |
| Dead code | `make dead` | vulture |
| Trace matrix | `make trace` | regenerates `docs/TRACE-MATRIX.md` |
| Everything | `make check` | all of the above, plus `--check` on the matrix |

Without `make`, run the underlying `poetry run ...` commands directly.

### Line length is 100

Set in `pyproject.toml` for black and ruff, and in `.pylintrc` for pylint. The
VS Code workspace pins every extension to the same number, because black
defaults to 88 and flake8/pycodestyle to 79, and an unpinned extension reports
`line too long` at the wrong width.

### Line endings are LF

`.gitattributes` pins `* text=auto eol=lf`. One exception: `tests/data/**` is
marked `-text` so fixtures keep their exact bytes. A CRLF fixture that git
rewrote to LF would silently stop testing CRLF handling *and the test would
still pass*, which is the worst possible outcome.

When editing files programmatically on Windows, write bytes or pass
`newline="\n"`. `Path.write_text()` translates `\n` to `os.linesep` and will
rewrite the whole file.

## Requirements and traceability

This project keeps a three-level requirements tree. It is not decoration: it is
how we know the test suite covers what the docs claim.

| File | Level | Content |
|---|---|---|
| [`L1.md`](L1.md) | Product | SHALL statements about *what* the tool does |
| [`L2.md`](L2.md) | Architecture | *How* each L1 is structurally satisfied |
| [`L3.md`](L3.md) | Implementation | Concrete obligations; where tests attach |
| [`TRACE-MATRIX.md`](TRACE-MATRIX.md) | Generated | Forward trace and status |

**Status is derived, never written.** The requirement documents carry
specification content only. `scripts/build-trace-matrix.py` computes status
from `@pytest.mark.requirement` markers, and CI runs it with `--check`. A
document that records its own status will eventually claim coverage the tests
do not provide.

### Adding a requirement

1. Write the L1 (or find the existing one it belongs under).
2. Derive an L2 in [`L2.md`](L2.md) with a `**Parent**:` line naming the L1.
3. Derive one or more L3s in [`L3.md`](L3.md), in the compact one-line form.
4. Write the test and tag it:

   ```python
   @pytest.mark.requirement("L3-ING-008")
   def test_the_thing() -> None:
       ...
   ```

5. `make trace` and commit the regenerated matrix.

A marker naming an id that no document declares is a hard error, so a typo in a
marker fails the build rather than quietly reading as untested.

### Verification without a test

Some obligations cannot be tested — "this module imports no HTTP client" is a
property of the dependency graph, and a test can only show that one particular
run made no request.

Such a requirement declares `**Verification Method**: Inspection (I)` (or
Analysis, or Demonstration) **and** an `**Evidence**` line naming the artifact
that carries the check. Both are required. A method with no evidence describes
how something *would* be verified; on its own it is a plan, not a result, and
the matrix reports it as `Draft` — exactly as an untested Test-verified leaf is.

Number sequences are monotone within a category. **Never renumber**, even
across gaps: a retired identifier stays retired.

## Errors

Two shapes, chosen by blast radius:

- **Exception** when the whole unit of work is impossible (unreadable file, bad
  header). Nothing usable can be returned.
- **`RowIssue`** when one row is spoiled. These are data, which is what lets
  every problem in a file be reported in one pass.

Adding a code:

1. Define it in `github_metrics/errors.py` with the next unused number.
2. Document it in [`ERROR-CATALOG.md`](ERROR-CATALOG.md) — Meaning, Typical
   cause, Resolution.
3. Assert on it in a test, tagged with its requirement.
4. Never reuse a retired code.

## Tests

- Live in `tests/`, mirroring the module under test.
- Fixtures live in `tests/data/`, are byte-exact, and are exempt from line
  ending normalisation.
- Anything touching the live API must be marked `@pytest.mark.integration`; CI
  runs `-m "not integration"`.
- An autouse fixture in `conftest.py` restores the `github_metrics` logger
  after every test. `reset_logger()` sets `propagate = False` on a process-wide
  singleton, so without it a test that ran the CLI would stop `caplog` seeing
  records in tests that ran afterwards.

### Adding an input corner case

Add a byte-exact fixture to `tests/data/`, then a test that names the
behaviour rather than the file. The fixtures deliberately cover the
awkward shapes: BOM, CRLF, reordered and padded headers, extra columns, blank
lines, duplicates including a case variant, every row-rejection kind, an empty
file, a headerless file, a bad header, a duplicated column, non-UTF-8 bytes,
and an embedded NUL.

## The soak checks

`make check` runs against stubs in seconds. Every defect fixed between v0.6.3
and v0.6.15 lived on a path those stubs cannot reach - a budget running out, a
connection dropping, a history page failing - and each was found by reading and
then fixed and verified by simulation. Nothing in this repository had ever
driven those paths against the real service.

```bash
make soak              # minutes, a small amount of budget
make soak-exhaustion   # over an hour, a token's whole hourly quota
```

Both need a real `GITHUB_TOKEN` and skip cleanly without one. Neither is part
of `make check`, and the CI gate deselects them: they cost budget, they take
time, and they can fail for reasons that are nobody's fault. **A soak failure
is a question, not a verdict** - read it before believing it.

The same checks run weekly from `.github/workflows/soak.yml`, and can be
started by hand from the Actions tab with a profile argument. It gates nothing.

### The token it needs

**No scopes.** The tool reads public repositories only, and
`verify_credentials` reads a token's scopes to *log* them - it never requires
one. An authenticated request gets the 5,000/hour budget whether or not any box
is ticked, and a token with no scopes cannot do anything to the account it
belongs to, which is what you want for something that runs unattended every
week.

**Classic personal access token** (Settings → Developer settings → Personal
access tokens → Tokens (classic) → Generate new token):

- **Note**: something you will recognise in a year - `github-metrics soak`.
- **Expiration**: pick one and put the date in your calendar. An expired token
  makes the soak fail with exit 8 and a clear message, which is a good failure,
  but only if someone knows why.
- **Scopes**: **tick nothing.** Not `repo`, not `read:org`.

**Fine-grained token** works too, and is the better choice if the account owns
private repositories - it cannot reach them unless you grant it:

- **Repository access**: *Public repositories (read-only)*.
- **Permissions**: none needed beyond the read-only public access that setting
  already implies.

Then add it to the repository as a secret named **`SOAK_GITHUB_METRICS`**
(Settings → Secrets and variables → Actions → New repository secret). The
name has to match what `soak.yml` reads, exactly: an absent secret expands to
an empty string rather than failing, so a typo does not announce itself.

That is why the workflow also sets `SOAK_REQUIRE_TOKEN=1`. Without a token the
checks skip themselves, and a skip is reported as a pass - so the first
dispatch of this workflow finished green in twenty-eight seconds having run
nothing. With that variable set, an empty token is an error instead. Locally it
is unset, and skipping is the right behaviour there.

It is deliberately not the workflow's own `GITHUB_TOKEN`: that one cannot read
the GraphQL fields a scan needs, and `soak-exhaustion` would drain whatever it
is given, which is not something to do to a token other jobs depend on.

Locally, the soak reads `GITHUB_TOKEN` like everything else - your ordinary
development token is fine for the `quick` profile. Use a separate one for
`soak-exhaustion` unless you want to wait an hour for your own budget back.

### What the profiles do

`quick` collects a handful of real repositories, one of which does not exist,
and asserts what only a live run can show:

| check | why a stub cannot show it |
|---|---|
| Spend is measured and the remaining count moves | every stub answers a constant, so a budget that never moved would look identical |
| A repository GitHub does not have degrades the row, not the run | both times this broke, the stub was answering something the real transport never sends |
| stderr carries nothing outside the package's format | the geocoder here is real, so a library logging outside the handler shows up |
| The identity breakdown sums to `identities` | the buckets are derived, so one nobody populates hides in the remainder |

Both run with `--no-geocode`: these checks are about the GitHub side, Nominatim
is paced at one request a second and would dominate the wall clock, and a
scheduled job that hammered it risks the shared user agent being blocked -
which fails every later run rather than the one that earned it.

`exhaustion` drives the hourly budget to its end with `--deep-attribution` on a
large history - a point per hundred commits reaches the wall in one repository
rather than five hundred - and checks that the run stops, says so, and still
writes its file. It is manual on purpose: scheduling it weekly would buy one
check at the price of a token nobody else can use for an hour.

### Adding a check

Put it in `tests/test_soak.py` behind the `soak` marker, and **assert structure
rather than values**. Star counts change and contributors come and go; a check
that pins them is a check that fails on Tuesdays and gets ignored by Wednesday.
The existing ones assert that a figure moved, that a bucket is populated, that
a failure was classified - all true whatever the numbers are that day.

## The offline/online boundary

**`sources/` never touches the network. `collect/` never touches a disk format.**
Neither imports the other; they meet only in `cli`.

This is the rule most likely to be broken by a well-meaning change — adding an
existence check to ingestion is an obvious-looking improvement. It would cost
the offline guarantee (L1-ING-002), the credential-free CLI path (L2-CLI-002),
and the ability to diagnose a bad inventory before spending API quota. Discuss
it in an ADR before doing it.

## Releasing

1. Move `CHANGELOG.md`'s `Unreleased` entries under the new version and date,
   and update the comparison links at the foot of the file.
2. Bump the version in `pyproject.toml`. It is exposed as
   `github_metrics.__version__` and shown by `--version` and `--help`.

   **It is not written into the output.** `RepositoryMetrics.tool_version`
   carried it until v0.2.0 removed that type with the `contributors` command,
   and `SoftwareRow` has no equivalent column. A collected artefact is
   attributable to a *run* through `scan_id` and `scan_date`, but not to a
   tool version. Adding one is a column-set change and needs an ADR.
3. `make check`. The version is read from installed package metadata, so run
   `poetry install` after the bump or `--version` reports the previous one.
4. Tag `vX.Y.Z` and push. CI builds the distribution.

## Architecture decisions

Anything with a real alternative goes in [`adr/`](adr/), in MADR format with
YAML frontmatter (`status`, `date`, `decision-makers`). Record the options that
were rejected and *why*; a decision without its alternatives is unreviewable
later.

Current: the CSV contract (0001), concurrency placement (0002),
lenient-by-default ingestion (0003), the exit-code scheme (0004), and one
`scan` producing both artifacts (0005).
