# GitHub-Metrics — Architecture

## Scope of this document

How the package is put together and why. Requirements live in
[`L1.md`](L1.md), [`L2.md`](L2.md) and [`L3.md`](L3.md); this document
describes the structure that satisfies them, and records the reasoning that is
architectural rather than decision-shaped. Decisions with real alternatives are
in [`adr/`](adr/).

## The one structural rule

**Ingestion never touches the network. Collection never touches the disk
format.**

Everything else follows from that. It is worth stating first because it is the
constraint that shapes the module graph, the CLI's startup behaviour, and what
is testable without credentials.

```
                    ┌──────────────┐
                    │     cli      │  argument parsing, exit codes, rendering
                    └──────┬───────┘
             ┌─────────────┴─────────────┐
             ▼                           ▼
      ┌─────────────┐             ┌─────────────┐
      │   sources   │             │   metrics   │  collection over the API
      │  (offline)  │             │  (online)   │
      └──────┬──────┘             └──────┬──────┘
             │                           │
             ▼                     ┌─────┴─────┬──────────┐
      ┌─────────────┐              ▼           ▼          ▼
      │ validation  │        ┌──────────┐ ┌────────┐ ┌────────┐
      └─────────────┘        │  client  │ │  geo   │ │ models │
             │               └────┬─────┘ └────────┘ └────────┘
             │                    ▼
             │              ┌──────────┐
             │              │  config  │  GITHUB_TOKEN and friends
             ▼                    │
      ┌─────────────┐             │
      │   errors    │◀────────────┘  codes, exceptions, RowIssue
      └─────────────┘
             ▲
      ┌─────────────┐
      │   logger    │  configured once, at startup
      └─────────────┘
```

The two halves meet only at the CLI. `sources/` imports nothing that can open a
socket; `metrics` imports nothing that parses a file format. Neither imports
the other.

## Why the halves are separate

The separation buys four things, and the first is the one that matters
operationally.

**A malformed inventory is diagnosed in milliseconds, not after a partial run
has already spent API quota.** GitHub's rate limit is the scarcest resource
this tool consumes. Discovering on row 380 that row 12 was malformed means 379
requests were spent on an inventory that was never going to complete.

**Ingestion is testable without the network or credentials.** The entire
ingestion suite runs offline with no fixtures for HTTP and no token. That is
why it can afford to test fourteen input shapes.

**An analyst can validate a list on a machine with no GitHub access at all.**
This falls out of the above, and is the reason `github-metrics validate` must run
without a token — which in turn is why the CLI resolves configuration lazily
rather than in its group callback (L2-CLI-002).

**Neither half can grow a dependency on the other by accident.** The rule is
enforced by inspection of the import list (L2-ING-008) rather than by a runtime
check, because it is a property of the dependency graph and no test of a single
execution can establish it.

The cost is real and accepted: ingestion cannot report whether a repository
exists. `RepositoryRef` says so in its own docstring, because the type is where
someone will look.

## Modules

| Module | Responsibility | Network | Notes |
|---|---|---|---|
| `cli` | Argument parsing, rendering, exit codes | via `metrics` | The only place the two halves meet |
| `sources/` | Slugs, URLs, CSV → validated `RepositoryRef` values | never | Also owns concurrency across files |
| `validation` | Account and repository name grammar | never | Pure functions; returns reasons, not booleans |
| `errors` | Code taxonomy, exceptions, `RowIssue` | never | Imported by both halves |
| `logger` | Logging configuration | never | Configured once, at startup |
| `config` | `Settings` from environment | never | Requires `GITHUB_TOKEN` |
| `client` | Authenticated PyGithub wrapper | yes | |
| `metrics` | Collection over the API | via `client` | |
| `geo` | Location → coordinates | yes | Nominatim, paced at 1/sec |
| `geocache` | The cache file behind `geo` | never | Owns the format so `geo` need not |
| `models` | Serializable result types | never | |

## Ingestion data flow

```
bytes ─▶ NUL check ─▶ UTF-8 decode ─▶ csv.reader ─▶ header ─▶ rows ─▶ result
  │          │             │              │            │         │
  │          │             │              │            │         └─ per row:
  │          │             │              │            │            field count
  │          │             │              │            │            emptiness
  │          │             │              │            │            grammar
  │          │             │              │            │            duplicates
  │          │             │              │            └─ locate owner/repoid
  │          │             │              │               by name
  │          │             │              └─ quoting, embedded newlines
  │          │             └─ GM-ING-005          
  │          └─ GM-ING-006
  └─ GM-ING-001 / 002
```

Three properties of this pipeline are deliberate.

**The whole file is read into memory, not streamed.** Inventories are
hundreds to a few thousand short rows, so the cost is trivial, and holding the
rows is what lets a duplicate on line 900 be reported against its first
occurrence on line 12 without a second pass. L2-ING-007 bounds the scale this
assumption is made against, so it is a stated limit rather than an unexamined
one.

**Validation is layered so each failure has exactly one code.** Field count,
then emptiness, then grammar, then duplication — each check assumes the
previous one passed. A row stops at its first failure, so a row with an empty
owner is reported as `GM-ING-011` and not additionally as a grammar failure.

**Line numbers are physical.** They match what the analyst's editor shows,
which is the number they need in order to fix the file.

## Concurrency

Across files, not within one. The reasoning — including why parallelising rows
would be slower, not faster — is in
[`adr/0002-concurrency-across-files-not-within-a-file.md`](adr/0002-concurrency-across-files-not-within-a-file.md).

The determinism obligations that make it acceptable are worth repeating here,
because both are easy to lose in a refactor:

- Results come back in **input order**. `Executor.map` guarantees it;
  `as_completed` would not.
- When several sources fail, the error raised belongs to the **earliest source
  in input order**, not to whichever thread failed first. This one is invisible
  until a batch contains two bad files, and then it presents as a flaky error
  message rather than as a concurrency bug.

## The error model

Two shapes, chosen by blast radius rather than by severity.

**Exceptions** for conditions that make the whole unit of work impossible: a
missing file, an unreadable header, bytes that are not text. There is no
partial result to return, and returning an empty one would let a caller mistake
"broken" for "empty".

**`RowIssue` records** for conditions that spoil one row. These are *data*, not
control flow, which is the whole reason every problem in a file can be reported
in one pass. Strict mode is the single place the two shapes meet: it promotes
the first `RowIssue` to an exception.

Every code is stable and catalogued in [`ERROR-CATALOG.md`](ERROR-CATALOG.md).

## Logging

One handler, attached to the `github_metrics` logger by `reset_logger()` at
startup. Modules call `logging.getLogger(__name__)` and nothing else — they
name a logger, they never configure one.

Output goes to **stderr**, always. The CLI writes JSON to stdout, and a log
line interleaved into that stream would corrupt it for every downstream
consumer. This is why `reset_logger()` defaults to stderr even though a general
purpose logging helper might default to stdout.

## Requirements traceability

Requirements are specified at three levels and traced to tests by
`@pytest.mark.requirement` markers, which
`scripts/build-trace-matrix.py` harvests into
[`TRACE-MATRIX.md`](TRACE-MATRIX.md). CI runs the generator with `--check`, so
the matrix cannot drift from the suite that backs it.

Status is *derived*, never written down. A requirement document that records
its own status will eventually claim coverage the tests do not provide.
