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
**Resolution**: Check the path. `github-metrics ingest ./inventory.csv` is
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
