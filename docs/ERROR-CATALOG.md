# GitHub-Metrics — Error Catalog

Every failure this tool reports carries a stable code of the form
`GM-<AREA>-<NNN>`. Codes are **permanent**: a retired code is retired with its
condition and is never reused, so a log line, a runbook entry and a support
conversation written years apart all refer to the same thing.

This catalog is the authoritative description of each code. The codes
themselves are defined in `github_metrics/errors.py`.

## How to read a diagnostic

File-level failures render as:

```
Error: [GM-ING-004] inventory.csv: header is missing required column(s) 'repoid'; expected 'owner', 'repoid' but found ['organisation', 'project']
```

Row-level issues render in the usual compiler shape, `path:line: [CODE] message`:

```
inventory.csv:14: [GM-ING-013] invalid owner 'https://github.com/pypa': may only contain letters, digits and hyphens
```

## Two kinds of failure

The distinction is structural, not cosmetic, and it decides what happens to the
rest of your file.

| | **File-level** | **Row-level** |
|---|---|---|
| Shape | Raised exception | `RowIssue` record on the result |
| Effect | Nothing is read | Other rows are still read |
| Codes | `GM-ING-001`–`006`, `020` | `GM-ING-010`–`015` |
| CLI exit | 2 | 3 |

Collection errors (`GM-COL-*`) are a third kind: they concern one repository
rather than a whole file, so the run continues and the affected repository
yields a row with empty metrics.

Row-level issues become fatal under `--strict`, which converts the first one
into `GM-ING-020`. See
[`adr/0003-lenient-ingestion-by-default-with-strict-opt-in.md`](adr/0003-lenient-ingestion-by-default-with-strict-opt-in.md).

---

## File-level errors

### `GM-ING-001` — Source not found

**Class**: `SourceNotFoundError`
**Meaning**: The named path does not exist.
**Typical cause**: A typo, or a relative path resolved against a different
working directory than expected.
**Resolution**: Check the path. `github-metrics validate ./inventory.csv` is
resolved relative to where you ran it, not to the file's directory.

### `GM-ING-002` — Source unreadable

**Class**: `SourceUnreadableError`
**Meaning**: The path exists but cannot be opened as a file.
**Typical cause**: A directory was named instead of a file; the file's
permissions deny reading; a symlink points at nothing.
**Resolution**: Name a file rather than a directory. Note that this tool does
not walk directories — pass the CSVs explicitly, or expand a glob in your
shell.

### `GM-ING-003` — Source empty

**Class**: `SourceEmptyError`
**Meaning**: The file contains no header row. A zero-byte file and a file of
nothing but blank lines both land here.
**Typical cause**: A truncated download, a failed export, or a file created but
never written.
**Resolution**: Confirm the export produced content. This is reported rather
than treated as "zero repositories" because an empty inventory is almost always
an accident, and silently analysing nothing is worse than saying so.

### `GM-ING-004` — Header invalid

**Class**: `HeaderError`
**Meaning**: The header does not declare both required columns, or declares one
of them twice.
**Typical cause**: Different column names (`organisation`, `project`, `repo`),
or a file that is not an inventory at all.
**Resolution**: The header must name `owner` and `repoid`. Case, surrounding
whitespace and column order do not matter, and extra columns are ignored — so
`  RepoID , Owner , steward ` is accepted. A column named twice is rejected
rather than guessed at, because either position could be the intended one.

### `GM-ING-005` — Source not UTF-8

**Class**: `SourceDecodeError`
**Meaning**: The file's bytes are not valid UTF-8. The message names the byte
offset where decoding failed.
**Typical cause**: An export in Latin-1 or Windows-1252, which is what several
older tools still produce by default.
**Resolution**: Re-export as UTF-8. A UTF-8 byte-order mark is *not* an error —
it is consumed transparently.

### `GM-ING-006` — Malformed CSV

**Class**: `MalformedCsvError`
**Meaning**: The content cannot be parsed as CSV. Raised for an embedded NUL
byte, and for anything Python's `csv` module itself rejects.
**Typical cause**: A binary file renamed to `.csv` — a spreadsheet saved in its
native format rather than exported, most often.
**Resolution**: Export to CSV rather than renaming. NUL is detected explicitly
rather than left to the parser, which now passes it through; without the check
a binary file would produce one misleading "invalid owner" per row instead of
one accurate diagnosis.

### `GM-ING-020` — Strict mode abort

**Class**: `StrictModeError`
**Meaning**: `--strict` was in effect and a row-level issue was found. The
message embeds the underlying issue, including its own code and line number.
**Typical cause**: Running a pipeline over an inventory that needs cleaning.
**Resolution**: Re-run without `--strict` to see *every* problem at once rather
than only the first, fix them, then re-run strictly.

---

## Row-level issues

These are collected on the result and reported together. Each names the line
and the offending value.

### `GM-ING-010` — Wrong field count

**Meaning**: The row has fewer fields than the header requires.
**Typical cause**: A missing separator, or a row that is a stray note rather
than data.
**Resolution**: Every data row needs a cell for each required column. Note that
a quoted field containing a comma is one cell, not two, so
`"pypa,inc",virtualenv` is a two-field row with an invalid owner
(`GM-ING-013`), not a three-field row.

### `GM-ING-011` — Empty owner

**Meaning**: The `owner` cell is empty or whitespace only.
**Typical cause**: A row half-filled during editing.

### `GM-ING-012` — Empty repoid

**Meaning**: The `repoid` cell is empty or whitespace only.
**Typical cause**: An owner recorded before its repository was decided.

### `GM-ING-013` — Invalid owner

**Meaning**: The `owner` cell is not a syntactically valid GitHub account name.
The message names the specific rule broken.
**Typical cause**: A whole URL pasted into the owner column; an `owner/name`
slug left unsplit; a stray comment appended to the value.
**Rules**: 1–39 characters of letters, digits and hyphens; no leading or
trailing hyphen; no consecutive hyphens.
**Resolution**: The owner is only the account segment — for
`https://github.com/pypa/virtualenv` that is `pypa`.

### `GM-ING-014` — Invalid repoid

**Meaning**: The `repoid` cell is not a syntactically valid GitHub repository
name.
**Typical cause**: A `.git` suffix left behind after stripping the host from a
clone URL. This is common enough to be worth naming: `virtualenv.git` is
rejected here rather than allowed to fail later as an unexplained 404.
**Rules**: 1–100 characters of letters, digits, hyphens, underscores and dots;
not `.` or `..`; not ending in `.git`. Only the *suffix* is excluded — a
repository named `gitignore` or `dot.git.files` is fine.

### `GM-ING-015` — Duplicate repository

**Meaning**: This row names a repository that already appeared. The message
gives the line of the first occurrence, which is the one kept.
**Typical cause**: An inventory assembled from two overlapping sources.
**Resolution**: Nothing is lost — the repository is still analysed, once. It is
reported because a duplicate usually tells you something about how the *list*
was built, and because counting it twice would inflate every aggregate.
Matching is case-insensitive, so `PyPA/virtualenv` and `pypa/virtualenv`
collide.

---

---

## Collection errors

Raised while fetching data from the GitHub API. Unlike ingestion errors, these
concern one repository rather than a whole file: the run continues, and the
affected repository produces a row with its identity columns filled and its
metrics empty.

### `GM-COL-001` - Repository not found

**Class**: `RepositoryNotFoundError`
**Meaning**: The repository does not exist, is private to this token, or was
renamed.
**Typical cause**: An inventory entry that has gone stale. Syntactic validation
at ingestion cannot detect any of these - a well-formed reference names a
*plausible* repository, not one that exists - so this is where the distinction
finally surfaces.
**Resolution**: Check the repository on github.com. If it was renamed, GitHub
redirects the web UI but the API reports the old name as absent, so the
inventory needs updating.

### `GM-COL-002` - GraphQL query failed

**Class**: `GraphQLQueryError`
**Meaning**: The API returned an `errors` array for a query, for a reason other
than the repository being absent.
**Typical cause**: A malformed query (a defect in this program), an expired or
insufficiently scoped token, or a rate-limit rejection.
**Resolution**: The message carries the API's own text. Note that GraphQL
reports failures with HTTP 200 and an `errors` array rather than an error
status, so a failure here is invisible to any check that only inspects the
status code - which is why this code exists at all.

---

### `GM-COL-003` - Repository has moved

**Class**: `RepositoryMovedError`
**Exit status**: 4
**Meaning**: GitHub reports the repository under a different owner or a
different name than the inventory asked for.
**Typical cause**: The repository was renamed or transferred after the
inventory was written. GitHub redirects both, so the reference still resolves.
**Resolution**: The message and the warning both name the current
`owner/name`; copy it into the inventory.

This is the one collection failure where nothing went wrong at the API. The
data comes back, and it is correct — about a repository the inventory does not
name. Collecting it would produce a row in which every number is right and
nothing says the reference was stale, which is an error that survives review
because it looks like data. The row is emitted with its identity columns and
no measurements, exactly as for a repository that could not be read.

Case is not a difference. GitHub account and repository names are
case-insensitive, so `PyPA/virtualenv` and `pypa/virtualenv` are the same
reference.

---

### `GM-COL-004` - Rate limit exhausted

**Class**: `RateLimitExhaustedError`
**Exit status**: 5
**Meaning**: The token has too little of one of the two hourly budgets left to
finish the run.
**Typical cause**: An earlier run in the same hour, or an inventory larger than
a full quota covers. A scan costs **at least** two GraphQL points and one REST
request per repository, so a full quota covers at most 2,500 repositories and
GraphQL binds first.
**Resolution**: The message names which budget is short and by how much. Wait
for the hourly reset, or split the inventory.

Raised **before** collection starts under `--on-exhaustion fail`, and **during**
it when the budget runs out mid-run. The other two policies do not raise at all:
`wait` sleeps to the reset and continues, `partial` stops and exits 9.

The pre-flight is a **floor rather than a guarantee**: since v0.5.0 collects every contributor, a repository's real
cost depends on a contributor count nothing knows until the list is read. A run
whose minimum fits can still exhaust the budget partway through. See
`docs/adr/0006-collect-every-contributor.md`.

The common case it was built for is unchanged. A run that discovers exhaustion
halfway has already spent what it had and produced a file that is part
measurement and part absence, with nothing in it to tell the two apart.
Refusing costs one free request and leaves the quota intact for a smaller run.

### `GM-COL-005` - Contributor list unreadable

**Class**: `ContributorCollectionError`
**Exit status**: 4
**Meaning**: The repository was read, but its contributor list was not.
**Typical cause**: A transient API failure, or a repository whose contributor
statistics GitHub is still computing - a 202 while a cache warms.
**Resolution**: Re-run. The repository's measurements were collected and are in
the CSV; only its document is missing.

Separate from `GM-COL-001` because the reference is good and only the second
half of the collection failed. The repository's row in `githubmetrics.csv` is
**complete** - every column of it was collected - so the comparable table is
unaffected and the repository still ranks correctly.

No document is written, for the reason `METRICS.md` gives: a document carrying
an empty contributor array and a `contribution_total` of zero cannot be told
from a repository that genuinely has no contributors. The absent file and the
warning are the record that the contributor half failed.

---

### `GM-ING-016` - Malformed reference

**Class**: `RowIssue`
**Meaning**: A source is not `owner/repoid` and is not a URL naming a
repository.
**Typical cause**: A missing or extra `/`, or a URL that stops at the owner.
**Resolution**: The message says what is wrong with the shape rather than only
that it is wrong — `'a/b/c' is not owner/repoid: it has 2 '/' separators`.

### `GM-ING-017` - Not a GitHub host

**Class**: `RowIssue`
**Meaning**: A source is a URL, but its host is not `github.com`.
**Typical cause**: A URL copied from another forge.
**Resolution**: `gitlab.com/foo/bar` reduces perfectly well to `foo/bar`, which
is why it is refused rather than reduced: the result would be a plausible
reference to a different repository. On GitHub Enterprise, name the repository
as `owner/repoid` and point `GITHUB_API_URL` at the instance.

---

## Output errors

Raised when a result cannot be rendered or written. The destination ones are
checked **before** collection wherever possible, because discovering an
unwritable path after a run has spent an hour of quota is the expensive way to
find out, and quota does not refill on request.

### `GM-OUT-001` - Unknown field

**Class**: `UnknownFieldError`
**Exit status**: 2
**Meaning**: `--fields` named a column that does not exist.
**Typical cause**: A typo, or a column name from an older version.
**Resolution**: The message names the closest valid column when there is one.
Silently ignoring the name would produce output missing a column the caller
believed they had asked for, discovered much later and blamed on the data.

### `GM-OUT-002` - Destination unwritable

**Class**: `OutputDestinationError`
**Exit status**: 2
**Meaning**: The file named by `--output` cannot be written.
**Typical cause**: A parent directory that does not exist.
**Resolution**: Create the directory, or name one that exists.

### `GM-OUT-003` - Document directory unusable

**Class**: `DocumentDirectoryError`
**Exit status**: 2
**Meaning**: The directory the per-repository documents go in cannot be
created, or exists as a file.
**Typical cause**: `--output` naming an existing file, or a path the process
cannot write to.
**Resolution**: Name a directory, or one that can be created.

Checked before collection alongside `GM-OUT-002`. The same code covers a single
document that cannot be written during a run; there, one failure is reported on
stderr and the remaining documents are still written, because the CSV is
already on disk and the rest are still worth having.

---

## Credential errors

Raised before any work starts. A command that needs no credentials - `validate`,
or any run with `--dry-run` - never reaches them.

### `GM-CFG-001` - No credentials

**Class**: `MissingCredentialsError`
**Exit status**: 7
**Meaning**: No token was supplied, by `--token` or by `GITHUB_TOKEN` in the
environment or a `.env` file.
**Resolution**: Supply one by any of those routes. Prefer the environment or a
`.env` file: a token passed as a command-line argument is visible to other
processes on the machine and is written to shell history.

### `GM-CFG-002` - Credentials rejected

**Class**: `InvalidCredentialsError`
**Exit status**: 8
**Meaning**: GitHub refused the token - expired, revoked, mistyped, or scoped
too narrowly. The message names the token's kind, inferred from its prefix.
**Typical cause**: An expired fine-grained token, which unlike a classic token
has a mandatory expiry.
**Resolution**: Check the token at
https://github.com/settings/tokens. Run with `LOG_LEVEL=DEBUG` to see the
kind, length and scopes the tool observed - the token value itself is never
logged.

## Reserved and unused

`GM-000` is the base code on `GitHubMetricsError` and should never be seen in
practice; if it appears, an exception was raised without its own code.
`GM-ING-000` is the corresponding base for ingestion errors.

## Adding a code

1. Define it in `github_metrics/errors.py`, taking the next unused number in
   its area.
2. Add a section here, following the Meaning / Typical cause / Resolution
   shape.
3. Add or extend a test that asserts on the code, tagged with the requirement
   it verifies.
4. Never renumber an existing code, even if the sequence has gaps.
