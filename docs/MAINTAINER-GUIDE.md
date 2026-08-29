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

## The offline/online boundary

**`ingest` never touches the network. `metrics` never touches a disk format.**
Neither imports the other; they meet only in `cli`.

This is the rule most likely to be broken by a well-meaning change — adding an
existence check to ingestion is an obvious-looking improvement. It would cost
the offline guarantee (L1-ING-002), the credential-free CLI path (L2-CLI-002),
and the ability to diagnose a bad inventory before spending API quota. Discuss
it in an ADR before doing it.

## Releasing

1. Update `CHANGELOG.md` under `Unreleased`.
2. Bump the version in `pyproject.toml`. It is exposed as
   `github_metrics.__version__`, shown by `--version` and `--help`, and stamped
   into every `RepositoryMetrics` snapshot as `tool_version` — so a released
   artefact stays attributable.
3. `make check`.
4. Tag and push. CI builds the distribution.

## Architecture decisions

Anything with a real alternative goes in [`adr/`](adr/), in MADR format with
YAML frontmatter (`status`, `date`, `decision-makers`). Record the options that
were rejected and *why*; a decision without its alternatives is unreviewable
later.

Current: the CSV contract (0001), concurrency placement (0002), lenient-by-default
ingestion (0003).
