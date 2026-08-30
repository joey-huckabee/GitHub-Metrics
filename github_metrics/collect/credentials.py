"""Verifying that the configured GitHub token actually works.

Why check at all
----------------
A token that is absent, expired, revoked, or scoped too narrowly fails at the
first API call - somewhere in the middle of a run, after an inventory has been
read and possibly after quota has been spent. Checking up front turns that into
one clear message before any work starts.

Why it is free
--------------
The check calls `GET /rate_limit`, which is the one authenticated endpoint that
**does not count against the rate limit**. Measured directly: core remaining
was 5000 before the call and 5000 after. It returns 401 for a token that is not
valid, so it is both free and definitive.

`GET /user` would also work and would give the account login, but it costs a
request against the very budget the run is about to spend.

What gets logged, and what never does
-------------------------------------
Diagnosing a credential problem needs to answer "which token, from where, with
what scopes" - and none of those answers require the token itself. Logged at
DEBUG: where the token came from, its recognised kind, its length, the scopes
GitHub reports, and the remaining budget on both APIs.

**The token value is never logged, at any level.** Not truncated, not masked.
A masked secret in a log is still a secret in a log, and the fields above
diagnose every failure this check can produce without one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Final

from github.GithubException import BadCredentialsException, GithubException

from github_metrics.client import GitHubClient
from github_metrics.config import Settings
from github_metrics.errors import InvalidCredentialsError

LOGGER = logging.getLogger(__name__)

TOKEN_KINDS: Final[dict[str, str]] = {
    "ghp_": "classic personal access token",
    "github_pat_": "fine-grained personal access token",
    "gho_": "OAuth access token",
    "ghu_": "GitHub App user token",
    "ghs_": "GitHub App installation token",
    "ghr_": "GitHub App refresh token",
}
"""Documented token prefixes, used to describe a token without revealing it."""


def describe_token_kind(token: str) -> str:
    """Name a token's kind from its prefix.

    The prefix is public information about the token's *type*, not about its
    secret material, and knowing it turns "authentication failed" into
    "authentication failed with a fine-grained token", which is a different
    thing to go and check.

    Args:
        token: The token. Only its prefix is inspected, and nothing derived
            from the rest of it is returned.

    Returns:
        A human-readable kind, or a note that the prefix is unrecognised.
    """
    for prefix, kind in TOKEN_KINDS.items():
        if token.startswith(prefix):
            return kind
    return "unrecognised prefix"


@dataclass(frozen=True, slots=True)
class CredentialCheck:
    """What a successful credential check learned.

    Attributes:
        token_kind: The token's kind, from its prefix.
        scopes: OAuth scopes GitHub reports for the token. Empty for a
            fine-grained token, which carries permissions rather than scopes,
            so an empty list is not by itself a problem.
        core_remaining: Requests left this hour on the REST API.
        graphql_remaining: Points left this hour on the GraphQL API.
    """

    token_kind: str
    scopes: list[str] = field(default_factory=list)
    core_remaining: int = 0
    graphql_remaining: int = 0


def verify_credentials(settings: Settings, client: GitHubClient | None = None) -> CredentialCheck:
    """Confirm the configured token is accepted by GitHub.

    Args:
        settings: Resolved settings carrying the token.
        client: An existing client to reuse. When omitted one is created and
            closed here.

    Returns:
        What the check learned, for logging and for a pre-flight budget.

    Raises:
        InvalidCredentialsError: GitHub rejected the token, or the check could
            not be completed.
    """
    kind = describe_token_kind(settings.github_token)
    LOGGER.debug(
        "Verifying credentials: %s, %d characters, against %s",
        kind,
        len(settings.github_token),
        settings.api_url,
    )

    owned = client is None
    active = client if client is not None else GitHubClient(settings)
    try:
        check = _probe(active, kind)
    except BadCredentialsException as exc:
        raise InvalidCredentialsError(
            "GitHub rejected the token (401). It may be expired, revoked, or "
            f"mistyped. Kind detected from its prefix: {kind}."
        ) from exc
    except GithubException as exc:
        raise InvalidCredentialsError(
            f"could not verify the token against {settings.api_url}: {exc.data or exc}"
        ) from exc
    finally:
        if owned:
            active.close()

    LOGGER.debug(
        "Credentials accepted: %s, scopes=[%s], core=%d/hour remaining, graphql=%d/hour remaining",
        check.token_kind,
        ", ".join(check.scopes) or "none reported",
        check.core_remaining,
        check.graphql_remaining,
    )

    if not check.scopes:
        # Fine-grained tokens report no scopes at all, so this is a note rather
        # than a fault - but it is the first thing to look at if a later call
        # is refused for permissions.
        LOGGER.debug(
            "No OAuth scopes reported. Fine-grained tokens carry permissions instead, "
            "so this is expected for one of those."
        )

    LOGGER.info(
        "GitHub credentials verified (%s); %d REST requests and %d GraphQL points available",
        check.token_kind,
        check.core_remaining,
        check.graphql_remaining,
    )
    return check


def _probe(client: GitHubClient, kind: str) -> CredentialCheck:
    """Call the rate-limit endpoint and read what it reports.

    Args:
        client: The client to use.
        kind: The token kind, carried through to the result.

    Returns:
        The parsed check.
    """
    headers, limits = client.rate_limit_snapshot()

    raw_scopes = headers.get("x-oauth-scopes", "") or ""
    scopes = [scope.strip() for scope in raw_scopes.split(",") if scope.strip()]

    resources = limits.get("resources", {})
    core = int(resources.get("core", {}).get("remaining", 0))
    graphql = int(resources.get("graphql", {}).get("remaining", 0))

    return CredentialCheck(
        token_kind=kind,
        scopes=scopes,
        core_remaining=core,
        graphql_remaining=graphql,
    )
