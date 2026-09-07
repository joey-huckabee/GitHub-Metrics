"""The exit-status scheme: the numbers, and the exceptions that produce them.

Its own module because the scheme had no owner, and that is how it came to
publish a status nothing could raise. The numbers lived in `cli.py`, the
conditions in `errors.py`, and the contract in four documents; `EXIT_RATE_LIMITED`
was declared before any code could reach its condition and never acquired a
raiser, so `--on-exhaustion fail` exited 1 with a traceback for every release
up to v0.6.3 while `ERROR-CATALOG.md` promised 5.

Two rules hold here, and `tests/test_cli.py` enforces both:

1. every status declared here is loaded somewhere in the package - by a
   class below, or by `ctx.exit` in the command - so a number cannot be
   published with nothing delivering it;
2. every `ClickException` subclass here is raised somewhere in the package,
   so a class cannot outlive the path that used it.

See `docs/adr/0004-exit-code-scheme.md` for the scheme and its amendments.
"""

from __future__ import annotations

import click

# Exit statuses, severity-ordered; the highest applicable one wins. Codes 1 and
# 2 belong to click (ClickException and UsageError) and are listed for
# completeness rather than chosen. See docs/adr/0004-exit-code-scheme.md.
EXIT_ROWS_REJECTED = 3
"""Degraded: the input was read but at least one row was rejected."""

EXIT_DEGRADED = 4
"""Degraded: a usable file was written, and something in it is missing.

**One code for every incomplete outcome**, whatever made it incomplete: a
repository that could not be read, one the run never reached, or one whose
contributor list failed so no document was written. Which of those happened is
in `statistics.json`, per repository, where a caller can act on it.

That is deliberate rather than lazy. The scheme's whole value is the boundary
at 5 - `$? -ge 3` means something was wrong and `$? -ge 5` means nothing usable
came out - and the degraded band is only 3 and 4 wide. A third degraded code
would have to sit above the aborted ones and would break the second test for
every caller, which is exactly what exit 9 did between v0.6.0 and v0.6.2. See
`docs/adr/0011-one-degraded-exit-status.md`.
"""

# Exit 9 is **retired**. It meant "the budget ran out and `--on-exhaustion
# partial` stopped the run", and it was wrong: it sat above the documented
# "nothing usable came out" boundary at 5 while producing a perfectly usable
# file, so a caller following the published test would have discarded it. That
# outcome now exits 4 like every other degraded one. The number is not reused.

EXIT_RATE_LIMITED = 5
"""Aborted: the API budget was exhausted, or pre-flight refused the run."""

EXIT_INPUT_UNREADABLE = 6
"""Aborted: the input file could not be read at all."""

EXIT_NO_CREDENTIALS = 7
"""Aborted: no GitHub token was supplied, by flag or by environment."""

EXIT_BAD_CREDENTIALS = 8
"""Aborted: GitHub rejected the token that was supplied."""


class InputError(click.ClickException):
    """A CLI error that exits with `EXIT_INPUT_UNREADABLE`.

    Click's own exit code for a `ClickException` is 1, which a shell cannot
    tell apart from a generic failure. Reading the input is the one thing that
    must be distinguishable, so it gets its own status.
    """

    exit_code = EXIT_INPUT_UNREADABLE


class NoCredentialsError(click.ClickException):
    """A CLI error that exits with `EXIT_NO_CREDENTIALS`.

    Separate from a rejected token because the fix differs: this one means
    "configure a token", not "your token stopped working".
    """

    exit_code = EXIT_NO_CREDENTIALS


class BadCredentialsError(click.ClickException):
    """A CLI error that exits with `EXIT_BAD_CREDENTIALS`."""

    exit_code = EXIT_BAD_CREDENTIALS


class RateLimitedError(click.ClickException):
    """A CLI error that exits with `EXIT_RATE_LIMITED`.

    `RateLimitExhaustedError` is a `CollectionError`, not a `ClickException`,
    so nothing mapped it to a status: it propagated out of the command and the
    operator got a traceback and exit 1 - a code that fails both published
    tests, `$? -ge 3` and `$? -ge 5`, so a pipeline read a refused run as a
    clean one. Exit 5 was declared in the scheme before any code could raise
    it and never acquired a raiser; this class is the raiser.

    It covers both places the budget can stop a run under `fail`, because both
    end the same way: the pre-flight refusing before anything is spent, and
    `BudgetGuard` stopping mid-run. Neither reaches `_emit`, so neither writes
    a file, which is what makes 5 - "nothing usable came out" - the honest
    status for both.
    """

    exit_code = EXIT_RATE_LIMITED
