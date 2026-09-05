"""Record real API traffic so the conformance suite can replay it.

The conformance suite asserts that a known input produces byte-identical
artifacts. That needs real data - stubs written from documentation prove only
that the code agrees with what someone believed - but it must not need the
network, because a suite that reaches GitHub is slow, rate-limited, and fails
for reasons unrelated to the change under test.

So the traffic is recorded once, by hand, against the live API, and replayed
forever after. Re-recording is a deliberate act: run this script, inspect the
diff, and decide whether the change in the golden artifacts is the change you
meant to make.

    poetry run python scripts/record-conformance.py

What is recorded
----------------
Everything `GitHubClient` is asked for, keyed by the request:

- GraphQL documents, by query and variables;
- contributor pages, by slug, page, page size and whether anonymous entries
  were requested;
- the derived contributor list from PyGithub's paginated object, because the
  object itself cannot be serialised;
- the budget readings.

Geocoding is not recorded here. It already has a durable cache, so the suite
ships a pre-populated one and the geocoder never reaches Nominatim.

Choosing the repositories
-------------------------
Small, stable and public. `octocat/Hello-World` and `octocat/Spoon-Knife` are
GitHub's own demonstration repositories: a handful of contributors between
them, last pushed in 2024, and about as unlikely to change as a public
repository gets. One deliberately absent repository exercises the path where a
reference is valid and the repository is not.
"""

# Hyphenated to match every other script here - build-trace-matrix.py,
# mutation-check.py. These are commands rather than importable modules, and
# pylint only notices this one because it is the first to import the package.
# pylint: disable=invalid-name
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from github.GithubException import GithubException

from github_metrics.client import GitHubClient
from github_metrics.collect.anonymous import collect_anonymous
from github_metrics.collect.census import count_identities
from github_metrics.collect.contributors import build_contributors, get_contributors
from github_metrics.collect.history import attribute_from_history
from github_metrics.collect.repository import get_repository
from github_metrics.config import Settings
from github_metrics.errors import CollectionError

ROOT = Path(__file__).resolve().parent.parent / "tests" / "conformance"
RECORDING = ROOT / "recording.json"

REPOSITORIES = [
    ("octocat", "Hello-World"),
    ("octocat", "Spoon-Knife"),
    # Valid reference, absent repository: a row with identity and no
    # measurements, no document, and exit 4.
    ("ghost", "no-such-repository-conformance"),
    # The deep-attribution set. Chosen for the two things nothing else in the
    # fixtures has: a **bot** contributor, and a history short enough to record
    # - 333 commits is four pages, against 321 for the repository this feature
    # was measured on. 15 contributor identities, none anonymous.
    ("hukkin", "tomli"),
]


class Recorder:
    """Wraps a real client and remembers everything it was asked."""

    def __init__(self, client: GitHubClient) -> None:
        self.client = client
        self.graphql_calls: dict[str, Any] = {}
        self.pages: dict[str, Any] = {}
        self.contributors: dict[str, Any] = {}

    def graphql(self, query: str, variables: dict[str, Any]) -> tuple[Any, Any]:
        """Record one GraphQL exchange, error or not."""
        key = graphql_key(query, variables)
        try:
            headers, payload = self.client.graphql(query, variables)
        except GithubException as exc:
            self.graphql_calls[key] = {"error": type(exc).__name__, "data": _data_of(exc)}
            raise
        self.graphql_calls[key] = {"payload": payload}
        return headers, payload

    def contributors_page(self, slug: str, **kwargs: Any) -> tuple[Any, Any]:
        """Record one raw contributors page, headers included."""
        key = page_key(slug, kwargs)
        headers, payload = self.client.contributors_page(slug, **kwargs)
        self.pages[key] = {"headers": {"link": headers.get("link", "")}, "payload": payload}
        return headers, payload

    def repository(self, slug: str) -> Any:
        """Record the account list PyGithub's paginated object yields.

        The object cannot be serialised, so what is kept is the derived list -
        exactly the fields `get_contributor_accounts` reads from it - and a
        stand-in is handed back so the caller behaves normally.
        """
        repository = self.client.repository(slug)
        self.contributors[slug] = [
            {
                "login": account.login,
                "id": account.id,
                "contributions": account.contributions,
                "type": account.type,
            }
            for account in repository.get_contributors()
        ]
        return repository


def graphql_key(query: str, variables: dict[str, Any]) -> str:
    """Key one GraphQL exchange by what was asked, not how it was spelled."""
    return json.dumps({"query": " ".join(query.split()), "variables": variables}, sort_keys=True)


def page_key(slug: str, kwargs: dict[str, Any]) -> str:
    """Key one contributors page by slug and pagination."""
    return json.dumps(
        {
            "slug": slug,
            "page": kwargs.get("page", 1),
            "per_page": kwargs.get("per_page", 100),
            "anonymous": bool(kwargs.get("anonymous", False)),
        },
        sort_keys=True,
    )


def _data_of(exc: Exception) -> Any:
    """The response body a PyGithub exception carries, if it carries one."""
    return getattr(exc, "data", None)


def main() -> int:
    """Record every repository in the conformance set."""
    settings = Settings.from_env()
    with GitHubClient(settings) as client:
        recorder = Recorder(client)
        budget = client.graphql_points_remaining()

        for owner, repoid in REPOSITORIES:
            slug = f"{owner}/{repoid}"
            print(f"recording {slug}")
            try:
                get_repository(recorder, owner, repoid)  # type: ignore[arg-type]
            except CollectionError as exc:
                print(f"   metrics: {type(exc).__name__} (recorded)")
                continue

            # The whole contributor path, so the aliased detail query is
            # recorded too - recording only the account list leaves the
            # replay with a hole exactly where the interesting data is.
            get_contributors(recorder, owner, repoid)  # type: ignore[arg-type]
            count_identities(recorder, owner, repoid)  # type: ignore[arg-type]
            collect_anonymous(recorder, owner, repoid)  # type: ignore[arg-type]
            walked = attribute_from_history(recorder, owner, repoid)  # type: ignore[arg-type]
            # And the detail query the *deep* route issues. It ranks by the
            # history's own commit counts rather than the endpoint's, so the
            # logins arrive in a different order and the recorded list-route
            # query does not match it.
            build_contributors(recorder, walked.accounts, slug=slug)  # type: ignore[arg-type]
            print(f"   {len(recorder.contributors.get(slug, []))} contributors")

        spent = budget - client.graphql_points_remaining()

    ROOT.mkdir(parents=True, exist_ok=True)
    RECORDING.write_text(
        json.dumps(
            {
                "repositories": [list(item) for item in REPOSITORIES],
                "graphql": recorder.graphql_calls,
                "pages": recorder.pages,
                "contributors": recorder.contributors,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        # `write_text` translates to os.linesep otherwise, which on Windows
        # rewrites the whole fixture as CRLF and makes every re-recording a
        # whole-file diff. The repository is LF everywhere.
        newline="\n",
    )
    print(f"\nwrote {RECORDING} ({RECORDING.stat().st_size:,} bytes, {spent} GraphQL points)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
