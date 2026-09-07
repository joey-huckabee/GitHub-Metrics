"""Tests for :mod:`github_metrics.cli`."""

from __future__ import annotations

import ast
from pathlib import Path

from click.testing import CliRunner

import github_metrics.exit_codes
from github_metrics import __version__
from github_metrics.cli import main


def test_version_flag() -> None:
    result = CliRunner().invoke(main, ["--version"])

    assert result.exit_code == 0
    assert __version__ in result.output


def test_short_version_flag() -> None:
    result = CliRunner().invoke(main, ["-V"])

    assert result.exit_code == 0
    assert __version__ in result.output


def test_help_shows_the_version() -> None:
    result = CliRunner().invoke(main, ["-h"])

    assert result.exit_code == 0
    assert f"(v{__version__})" in result.output


def test_help_lists_commands() -> None:
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    listed = {
        line.strip().split()[0]
        for line in result.output.splitlines()
        if line.startswith("  ") and line.strip() and not line.strip().startswith("-")
    }
    for command in ("scan", "validate", "bands", "rate-limit"):
        assert command in listed, command

    # Retired names, and never recycled. `repo` was scaffolding collecting a
    # different set of fields; `metrics` became `scan` so that the command and
    # the `scan_id` it stamps share a word; `contributors` folded into `scan`
    # once the per-repository document turned out to be the metrics row plus a
    # contributor block, which one run has to produce under one identity.
    #
    # Matched against the parsed command list rather than the whole help text,
    # which is how this check previously passed for a `metrics` command that
    # had already been renamed: the word still appeared in the group summary.
    for retired in ("repo", "metrics", "contributors"):
        assert retired not in listed, retired


def test_missing_token_is_a_friendly_error(empty_env_file: Path) -> None:
    result = CliRunner().invoke(main, ["--env-file", str(empty_env_file), "rate-limit"])

    assert result.exit_code != 0
    assert "GITHUB_TOKEN" in result.output


def test_every_declared_exit_status_has_something_that_raises_it() -> None:
    """A status nobody raises is a promise the CLI does not keep.

    `EXIT_RATE_LIMITED` was declared with the rest of the scheme, before any
    code could reach the condition, and never acquired a raiser. Four
    documents named 5 as the status for an exhausted budget while the run
    exited 1 with a traceback. Nothing caught it for six releases, because a
    constant nobody reads is invisible to every kind of check the repository
    has: vulture sees a used module attribute, mypy sees a valid int, and the
    one test over the condition asserted `!= 0`, which a traceback satisfies.

    Declaring a status and wiring it up are two edits. Only one of them was
    ever checked.
    """
    module = Path(github_metrics.exit_codes.__file__)
    declared = {
        target.id
        for node in ast.parse(module.read_text(encoding="utf-8")).body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id.startswith("EXIT_")
    }
    # Across the package, not just this module: 3 and 4 are delivered by
    # `ctx.exit` in the command rather than by an exception class, and both
    # routes count as wiring a status up.
    loaded = {
        node.id
        for path in module.parent.rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }

    assert declared, "the exit statuses are module-level constants; this found none"
    assert sorted(declared - loaded) == []


def test_every_cli_error_class_is_raised_somewhere() -> None:
    """The same rule for the classes, which is how the second one was found.

    `RepositoryError` carried `EXIT_DEGRADED` and was raised nowhere: exit 4
    is delivered by `ctx.exit`, so the class had been dead since it was
    written. Harmless, unlike its sibling, and the same defect - a declared
    exit path with nothing behind it. It was removed in v0.6.3.
    """
    module = Path(github_metrics.exit_codes.__file__)
    tree = ast.parse(module.read_text(encoding="utf-8"))
    declared = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    package = module.parent
    raised = {
        node.exc.func.id
        for path in package.rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Raise)
        and isinstance(node.exc, ast.Call)
        and isinstance(node.exc.func, ast.Name)
    }

    assert declared, "this found no exception classes to check"
    assert sorted(declared - raised) == []
