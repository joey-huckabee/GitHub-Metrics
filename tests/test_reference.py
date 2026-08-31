"""Tests for :mod:`github_metrics.sources.reference`."""

from __future__ import annotations

import pytest

from github_metrics.errors import (
    ISSUE_FOREIGN_HOST,
    ISSUE_INVALID_OWNER,
    ISSUE_INVALID_REPOID,
    ISSUE_MALFORMED_REFERENCE,
    RowIssue,
)
from github_metrics.sources.csv_inventory import RepositoryRef
from github_metrics.sources.reference import looks_like_a_url, parse_reference

VIRTUALENV = RepositoryRef(owner="pypa", repoid="virtualenv")


def parsed(text: str) -> RepositoryRef:
    """Parse a reference that is expected to be accepted."""
    result = parse_reference(text)
    assert isinstance(result, RepositoryRef), result
    return result


def refused(text: str) -> RowIssue:
    """Parse a reference that is expected to be refused."""
    result = parse_reference(text)
    assert isinstance(result, RowIssue), result
    return result


# ---------------------------------------------------------------------------
# The forms an analyst actually types
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-SRC-001")
@pytest.mark.parametrize(
    "text",
    [
        "pypa/virtualenv",
        "https://github.com/pypa/virtualenv",
        "http://github.com/pypa/virtualenv",
        "https://www.github.com/pypa/virtualenv",
        "https://github.com/pypa/virtualenv/",
        "https://github.com/pypa/virtualenv.git",
        "github.com/pypa/virtualenv",
        "git@github.com:pypa/virtualenv.git",
        "ssh://git@github.com/pypa/virtualenv.git",
        "  pypa/virtualenv  ",
    ],
)
def test_every_usual_form_names_the_same_repository(text: str) -> None:
    """Refusing any of these would be refusing a correct answer."""
    assert parsed(text) == VIRTUALENV


@pytest.mark.requirement("L3-SRC-001")
@pytest.mark.parametrize(
    "text",
    [
        "https://github.com/pypa/virtualenv/tree/main",
        "https://github.com/pypa/virtualenv/issues/42",
        "https://github.com/pypa/virtualenv/blob/main/docs/index.rst",
    ],
)
def test_browsing_debris_is_dropped(text: str) -> None:
    # A URL naming a file still names the repository, and this is what gets
    # pasted out of a browser.
    assert parsed(text) == VIRTUALENV


@pytest.mark.requirement("L3-SRC-001")
def test_case_is_preserved_rather_than_normalised() -> None:
    # The reference records what the input asked for. Case-insensitive
    # comparison happens at `key`, where it belongs.
    reference = parsed("PyPA/VirtualEnv")

    assert reference.owner == "PyPA"
    assert reference.key == VIRTUALENV.key


# ---------------------------------------------------------------------------
# Refusals, each with a reason
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-SRC-002")
def test_another_host_is_refused_rather_than_assumed() -> None:
    """`gitlab.com/foo/bar` is not `foo/bar`.

    Reading it as one would turn a mistake into a plausible row.
    """
    issue = refused("https://gitlab.com/foo/bar")

    assert issue.code == ISSUE_FOREIGN_HOST
    assert "gitlab.com is not GitHub" in issue.message
    # The message says what to do instead, because GitHub Enterprise is the
    # legitimate reason someone lands here.
    assert "GITHUB_API_URL" in issue.message


@pytest.mark.requirement("L3-SRC-002")
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("notaslug", "no '/'"),
        ("a/b/c", "2 '/' separators"),
        ("", "empty reference"),
        ("   ", "empty reference"),
    ],
)
def test_a_malformed_reference_says_what_is_wrong_with_it(text: str, expected: str) -> None:
    issue = refused(text)

    assert issue.code == ISSUE_MALFORMED_REFERENCE
    assert expected in issue.message


@pytest.mark.requirement("L3-SRC-002")
def test_a_url_naming_no_repository_is_refused() -> None:
    issue = refused("https://github.com/pypa")

    assert issue.code == ISSUE_MALFORMED_REFERENCE
    assert "needs owner and name" in issue.message


@pytest.mark.requirement("L3-SRC-002")
def test_the_name_grammar_is_the_same_one_ingestion_uses() -> None:
    assert refused("my org/x").code == ISSUE_INVALID_OWNER
    assert refused("pypa/no spaces").code == ISSUE_INVALID_REPOID


@pytest.mark.requirement("L3-SRC-002")
def test_an_issue_from_an_argument_carries_no_line_number() -> None:
    issue = refused("notaslug")

    assert issue.line is None
    assert str(issue).startswith("<argument>: [GM-ING-016]")


# ---------------------------------------------------------------------------
# Telling the forms apart
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-SRC-001")
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://github.com/a/b", True),
        ("github.com/a/b", True),
        ("git@github.com:a/b", True),
        ("https://gitlab.com/a/b", True),
        ("pypa/virtualenv", False),
        ("inventory.csv", False),
    ],
)
def test_a_url_is_recognised_before_it_is_parsed(text: str, expected: bool) -> None:
    # A foreign host still looks like a URL: it has to, or it would be read as
    # a slug and refused for the wrong reason.
    assert looks_like_a_url(text) is expected
