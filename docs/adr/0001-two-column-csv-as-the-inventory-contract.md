---
status: accepted
date: 2026-08-29
decision-makers: Joey
---

# Use a two-column `owner,repoid` CSV as the inventory contract

## Context and Problem Statement

GitHub-Metrics analyses a *set* of repositories. Before it can collect
anything, it needs to be told which repositories, and that list has to come
from somewhere an analyst can maintain.

The lists in practice are maintained in spreadsheets, exported from issue
trackers, or pasted together from browser tabs. Whatever the origin, the
question is what shape the tool should accept, because that shape becomes a
contract: everything downstream addresses repositories the way this file
names them, and changing it later invalidates every stored inventory.

## Decision Drivers

* An analyst must be able to produce and edit the file without special tools
* The format must survive a round trip through Excel, Google Sheets, and email
* Ingestion must be unambiguous — no input should have two defensible readings
* The parsed result should need no further transformation before addressing the
  GitHub API
* Real inventories carry bookkeeping columns the tool has no interest in

## Considered Options

* **Two columns, `owner` and `repoid`** — the identifiers the API already uses
* **One column of repository URLs** — what a person actually has on hand
* **One column of `owner/name` slugs** — a compromise, one cell per repository
* **A structured format (JSON/YAML/TOML)** — richer, and unambiguous by
  construction

## Decision Outcome

Chosen option: **two columns, `owner` and `repoid`**.

The GitHub API addresses a repository by exactly these two identifiers. A file
that already carries them separated needs no parsing step between reading and
calling, which means there is no place for a parsing bug to live.

The URL column was the closest contender, because it is genuinely what a person
has in their hand after browsing. It was rejected because URL parsing is where
the ambiguity accumulates, and all of it is ambiguity the two-column form
simply does not have:

- `https://github.com/pypa/virtualenv`, `git@github.com:pypa/virtualenv.git`
  and `github.com/pypa/virtualenv` all name the same repository
- a trailing `.git` is present or absent depending on where the URL was copied
- URLs may carry credentials, a `#fragment`, or a `?query`
- a deep link such as `.../pypa/virtualenv/blob/main/README.md` contains the
  owner and name but is not a repository URL
- an enterprise host is indistinguishable from github.com without a host
  allowlist that then becomes configuration

Every one of those is a decision the tool would have to make on the analyst's
behalf, silently, per row. Producing two columns from a set of URLs is a
one-time transformation the person holding the URLs can do far more reliably
than we can guess.

The `owner/name` slug in a single cell is better than a URL but still requires
splitting on a separator that is legal in neither component, only to arrive at
the two values the two-column form already has. It buys nothing.

Structured formats were rejected on reach rather than merit. JSON and YAML are
unambiguous and would carry richer metadata, but no analyst maintains a
repository list in them, and every one of the tools they *do* use exports CSV.
A format nobody can produce without a conversion step is not a usable contract,
whatever its technical properties.

### Consequences

Good:

- The parsed reference is directly usable as an API address
- The file opens in any spreadsheet, and edits round-trip through email
- Column matching can be forgiving (case, order, whitespace, extra columns)
  without introducing ambiguity, because the *values* are unambiguous even when
  the header formatting is not

Bad:

- Analysts holding URLs must transform them once, up front
- The `.git` suffix is the fingerprint of exactly that transformation done
  carelessly, so validation rejects it explicitly and says why
  (`GM-ING-014`) rather than letting it fail later as an unexplained 404
- CSV carries no types and no schema, so every guarantee about the content has
  to be enforced by the reader rather than by the format

Neutral:

- Extra columns are ignored rather than rejected, so an analyst's real
  inventory — with its stewards, review dates and priorities — remains a single
  file rather than something they must maintain a stripped copy of

## More Information

The accepted grammar for each column is specified in L2-VAL-001 and L2-VAL-002.
Rejected alternatives are recorded as non-requirements NR-002 and NR-003 in
[`../L1.md`](../L1.md).
