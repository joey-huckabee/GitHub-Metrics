"""Tests for :mod:`github_metrics.validation`."""

from __future__ import annotations

import pytest

from github_metrics.validation import (
    MAX_OWNER_LENGTH,
    MAX_REPOID_LENGTH,
    validate_owner,
    validate_repoid,
)


@pytest.mark.requirement("L3-VAL-001")
@pytest.mark.parametrize(
    "owner",
    [
        "pypa",
        "urllib3",
        "bokeh",
        "a",
        "Netflix",
        "python-attrs",
        "0",
        "a" * MAX_OWNER_LENGTH,
    ],
)
def test_well_formed_owners_are_accepted(owner: str) -> None:
    assert validate_owner(owner) is None


@pytest.mark.requirement("L3-VAL-001", "L3-VAL-003")
@pytest.mark.parametrize(
    ("owner", "expected_fragment"),
    [
        ("", "is empty"),
        ("a" * (MAX_OWNER_LENGTH + 1), "the limit is 39"),
        ("py pa", "letters, digits and hyphens"),
        ("pypa/virtualenv", "letters, digits and hyphens"),
        ("https://github.com/pypa", "letters, digits and hyphens"),
        ("py_pa", "letters, digits and hyphens"),
        ("pypa.org", "letters, digits and hyphens"),
        ("-pypa", "begin or end with a hyphen"),
        ("pypa-", "begin or end with a hyphen"),
        ("py--pa", "consecutive hyphens"),
    ],
)
def test_malformed_owners_are_rejected_with_a_reason(owner: str, expected_fragment: str) -> None:
    problem = validate_owner(owner)

    assert problem is not None
    assert expected_fragment in problem


@pytest.mark.requirement("L3-VAL-002")
@pytest.mark.parametrize(
    "repoid",
    [
        "virtualenv",
        "urllib3",
        "python-dotenv",
        "some_repo",
        "docs.v2",
        "a",
        "gitignore",
        "not.git.really",
        "a" * MAX_REPOID_LENGTH,
    ],
)
def test_well_formed_repoids_are_accepted(repoid: str) -> None:
    assert validate_repoid(repoid) is None


@pytest.mark.requirement("L3-VAL-002", "L3-VAL-003")
@pytest.mark.parametrize(
    ("repoid", "expected_fragment"),
    [
        ("", "is empty"),
        ("a" * (MAX_REPOID_LENGTH + 1), "the limit is 100"),
        (".", "is reserved"),
        ("..", "is reserved"),
        ("has space", "letters, digits, hyphens, underscores and dots"),
        ("has/slash", "letters, digits, hyphens, underscores and dots"),
        ("virtualenv.git", "may not end in '.git'"),
        ("virtualenv.GIT", "may not end in '.git'"),
    ],
)
def test_malformed_repoids_are_rejected_with_a_reason(repoid: str, expected_fragment: str) -> None:
    problem = validate_repoid(repoid)

    assert problem is not None
    assert expected_fragment in problem


@pytest.mark.requirement("L3-VAL-004")
def test_dot_git_rejection_is_case_insensitive_but_does_not_over_reach() -> None:
    # The suffix is what GitHub rejects. A repository merely containing ".git"
    # elsewhere in the name is legitimate and must survive.
    assert validate_repoid("gitignore") is None
    assert validate_repoid("dot.git.files") is None
    assert validate_repoid("dotfiles.git") is not None
