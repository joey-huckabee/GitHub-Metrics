"""Tests for token resolution, verification, and the exit codes they carry."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

import pytest
from click.testing import CliRunner
from github.GithubException import BadCredentialsException, GithubException

from github_metrics.cli import EXIT_BAD_CREDENTIALS, EXIT_NO_CREDENTIALS, main
from github_metrics.client import GitHubClient
from github_metrics.collect.credentials import (
    TOKEN_KINDS,
    describe_token_kind,
    verify_credentials,
)
from github_metrics.config import Settings
from github_metrics.errors import InvalidCredentialsError

LOGGER_NAME = "github_metrics.collect.credentials"

SECRET = "ghp_thisMustNeverAppearInALogLine12345678"


class _StubClient:
    """Returns a canned rate-limit response, or raises."""

    def __init__(
        self,
        headers: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.headers = headers or {}
        self.payload = payload or {}
        self.raises = raises
        self.closed = False

    def rate_limit_snapshot(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """Mimic `GitHubClient.rate_limit_snapshot`."""
        if self.raises is not None:
            raise self.raises
        return self.headers, self.payload

    def close(self) -> None:
        """Mimic `GitHubClient.close`."""
        self.closed = True


def limits(core: int = 5000, graphql: int = 5000) -> dict[str, Any]:
    """A rate-limit payload."""
    return {"resources": {"core": {"remaining": core}, "graphql": {"remaining": graphql}}}


def check(stub: _StubClient, token: str = SECRET) -> Any:
    """Run verification against a stub client."""
    return verify_credentials(Settings(github_token=token), cast(GitHubClient, stub))


# ---------------------------------------------------------------------------
# Describing a token without revealing it
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-CFG-002")
@pytest.mark.parametrize(("prefix", "expected"), list(TOKEN_KINDS.items()))
def test_every_documented_prefix_is_recognised(prefix: str, expected: str) -> None:
    assert describe_token_kind(f"{prefix}whatever") == expected


@pytest.mark.requirement("L3-CFG-002")
def test_an_unknown_prefix_is_reported_as_such() -> None:
    assert describe_token_kind("something-else") == "unrecognised prefix"


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-CFG-003")
def test_a_good_token_reports_scopes_and_budgets() -> None:
    stub = _StubClient({"x-oauth-scopes": "repo, read:org"}, limits(4321, 1234))

    result = check(stub)

    assert result.scopes == ["repo", "read:org"]
    assert result.core_remaining == 4321
    assert result.graphql_remaining == 1234
    assert result.token_kind == "classic personal access token"


@pytest.mark.requirement("L3-CFG-003")
def test_a_fine_grained_token_reporting_no_scopes_is_not_a_failure() -> None:
    # Fine-grained tokens carry permissions rather than scopes, so an empty
    # scope list is expected rather than a fault.
    stub = _StubClient({"x-oauth-scopes": ""}, limits())

    result = check(stub, "github_pat_abc")

    assert not result.scopes
    assert result.token_kind == "fine-grained personal access token"


@pytest.mark.requirement("L3-CFG-004")
def test_a_rejected_token_raises_with_its_code() -> None:
    stub = _StubClient(raises=BadCredentialsException(401, {"message": "Bad credentials"}, {}))

    with pytest.raises(InvalidCredentialsError) as caught:
        check(stub)

    assert "GM-CFG-002" in str(caught.value)
    assert "401" in str(caught.value)


@pytest.mark.requirement("L3-CFG-004")
def test_any_other_api_failure_also_raises_rather_than_passing() -> None:
    # A check that cannot complete has not established anything, so it must
    # not read as success.
    stub = _StubClient(raises=GithubException(500, {"message": "server error"}, {}))

    with pytest.raises(InvalidCredentialsError):
        check(stub)


@pytest.mark.requirement("L3-CFG-004")
def test_a_borrowed_client_is_not_closed_by_the_check() -> None:
    stub = _StubClient({}, limits())

    check(stub)

    # The caller owns a client it passed in.
    assert stub.closed is False


# ---------------------------------------------------------------------------
# What the logs may and may not contain
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-CFG-005")
def test_the_token_never_appears_in_any_log_record(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stub = _StubClient({"x-oauth-scopes": "repo"}, limits())

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        check(stub)

    # Not in full, and not in part: a masked secret in a log is still a secret
    # in a log.
    assert SECRET not in caplog.text
    assert SECRET[:12] not in caplog.text
    assert SECRET[-12:] not in caplog.text


@pytest.mark.requirement("L3-CFG-005")
def test_the_diagnostics_that_replace_it_are_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stub = _StubClient({"x-oauth-scopes": "repo, gist"}, limits(4000, 3000))

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        check(stub)

    # Enough to diagnose a credential problem without the credential.
    assert "classic personal access token" in caplog.text
    assert str(len(SECRET)) in caplog.text
    assert "repo" in caplog.text
    assert "4000" in caplog.text
    assert "3000" in caplog.text


@pytest.mark.requirement("L3-CFG-005")
def test_an_empty_scope_list_is_explained_rather_than_left_bare(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stub = _StubClient({}, limits())

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        check(stub, "github_pat_abc")

    assert "Fine-grained tokens carry permissions" in caplog.text


# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------


def run(args: list[str]) -> Any:
    """Invoke the CLI against an env file that supplies nothing."""
    return CliRunner().invoke(main, ["--env-file", str(Path("nonexistent.env")), *args])


@pytest.mark.requirement("L3-CFG-006")
def test_no_token_anywhere_exits_seven() -> None:
    result = run(["rate-limit"])

    assert result.exit_code == EXIT_NO_CREDENTIALS
    assert "GM-CFG-001" in result.output
    # The message has to say what to do about it.
    assert "--token" in result.output
    assert "GITHUB_TOKEN" in result.output


@pytest.mark.requirement("L3-CFG-006")
def test_a_rejected_token_exits_eight(monkeypatch: pytest.MonkeyPatch) -> None:
    def _reject(_settings: Settings) -> None:
        raise InvalidCredentialsError("GitHub rejected the token (401)")

    monkeypatch.setattr("github_metrics.cli.verify_credentials", _reject)

    result = run(["--token", SECRET, "rate-limit"])

    assert result.exit_code == EXIT_BAD_CREDENTIALS
    assert "GM-CFG-002" in result.output


@pytest.mark.requirement("L3-CFG-006")
def test_the_two_credential_failures_have_different_codes() -> None:
    # "configure a token" and "your token stopped working" are different
    # problems with different fixes.
    assert EXIT_NO_CREDENTIALS != EXIT_BAD_CREDENTIALS


@pytest.mark.requirement("L3-CFG-007")
def test_verification_can_be_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def _record(_settings: Settings) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr("github_metrics.cli.verify_credentials", _record)
    monkeypatch.setattr("github_metrics.cli.GitHubClient", lambda _s: _Unused())

    run(["--token", SECRET, "--no-verify-token", "rate-limit"])

    assert called is False


@pytest.mark.requirement("L3-CFG-007")
def test_ingest_needs_no_token_and_is_never_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _explode(_settings: Settings) -> None:
        raise AssertionError("ingest must not reach the credential check")

    monkeypatch.setattr("github_metrics.cli.verify_credentials", _explode)

    result = run(["ingest", str(Path("tests") / "data" / "repositories.csv")])

    assert result.exit_code == 0


class _Unused:
    """A client standing in for one the test does not expect to be used."""

    def __enter__(self) -> _Unused:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    @staticmethod
    def rate_limit_remaining() -> int:
        """Stand in for the real call."""
        return 0
