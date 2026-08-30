"""Tests for :mod:`github_metrics.metrics`."""

from __future__ import annotations

import logging
from typing import cast

import pytest
from github import GithubException
from github.Repository import Repository

from github_metrics.client import GitHubClient
from github_metrics.geo import Geocoder
from github_metrics.metrics import _license_id, collect_repository_metrics


class _StubLicense:
    def __init__(self, spdx_id: str) -> None:
        self.spdx_id = spdx_id


class _StubLicenseFile:
    def __init__(self, license_: _StubLicense | None) -> None:
        self.license = license_


class _StubUser:
    def __init__(self, login: str, location: str | None = None) -> None:
        self.login = login
        self.location = location


class _StubStats:
    def __init__(self, weeks: list[int] | None) -> None:
        self.all = weeks


class _StubRepo:
    """The slice of PyGithub's Repository that the collectors actually touch."""

    def __init__(
        self,
        *,
        contributors: list[_StubUser] | None = None,
        license_file: _StubLicenseFile | GithubException | None = None,
        weeks: list[int] | None = None,
    ) -> None:
        self.full_name = "owner/repo"
        self.stargazers_count = 10
        self.forks_count = 2
        self.subscribers_count = 3
        self.open_issues_count = 1
        self.language = "Python"
        self.archived = False
        self.created_at = None
        self.pushed_at = None
        self._contributors = contributors or []
        self._license_file = license_file
        self._weeks = weeks

    def get_license(self) -> _StubLicenseFile:
        """Mimic `Repository.get_license`, raising if configured to."""
        if isinstance(self._license_file, GithubException):
            raise self._license_file
        assert self._license_file is not None
        return self._license_file

    def get_contributors(self) -> list[_StubUser]:
        """Mimic `Repository.get_contributors`; a plain list slices the same way."""
        return self._contributors

    def get_stats_participation(self) -> _StubStats:
        """Mimic `Repository.get_stats_participation`."""
        return _StubStats(self._weeks)


class _StubGeocoder:
    """Resolves one known location and nothing else."""

    def __init__(self, known: dict[str, tuple[float, float]]) -> None:
        self._known = known

    def locate(self, location: str) -> tuple[float, float] | None:
        """Mimic `Geocoder.locate`."""
        return self._known.get(location)


class _StubClient:
    def __init__(self, repo: _StubRepo) -> None:
        self._repo = repo

    def repository(self, full_name: str) -> _StubRepo:
        """Mimic `GitHubClient.repository`."""
        assert full_name
        return self._repo


def _as_repo(stub: _StubRepo) -> Repository:
    return cast(Repository, stub)


def test_license_id_returns_the_spdx_identifier() -> None:
    repo = _StubRepo(license_file=_StubLicenseFile(_StubLicense("Apache-2.0")))

    assert _license_id(_as_repo(repo)) == "Apache-2.0"


def test_license_id_is_none_when_the_repository_declares_none() -> None:
    repo = _StubRepo(license_file=_StubLicenseFile(None))

    assert _license_id(_as_repo(repo)) is None


def test_license_lookup_failure_is_logged_rather_than_swallowed_silently(
    caplog: pytest.LogCaptureFixture,
) -> None:
    repo = _StubRepo(license_file=GithubException(403, {"message": "rate limited"}, None))

    with caplog.at_level(logging.DEBUG, logger="github_metrics.metrics"):
        assert _license_id(_as_repo(repo)) is None

    assert "No license resolved for owner/repo" in caplog.text
    assert "rate limited" in caplog.text


def test_collect_warns_when_the_contributor_list_is_truncated(
    caplog: pytest.LogCaptureFixture,
) -> None:
    repo = _StubRepo(
        contributors=[_StubUser(f"dev{i}") for i in range(3)],
        license_file=_StubLicenseFile(_StubLicense("MIT")),
        weeks=[1, 2, 3],
    )
    client = cast(GitHubClient, _StubClient(repo))

    with caplog.at_level(logging.DEBUG, logger="github_metrics.metrics"):
        metrics = collect_repository_metrics(client, "owner/repo", contributor_limit=3)

    assert metrics.contributors == 3
    assert metrics.commits_last_year == 6
    assert metrics.license_id == "MIT"
    assert "truncated at the --contributors limit of 3" in caplog.text


def test_collect_is_quiet_when_the_contributor_list_is_complete(
    caplog: pytest.LogCaptureFixture,
) -> None:
    repo = _StubRepo(
        contributors=[_StubUser("dev0")],
        license_file=_StubLicenseFile(_StubLicense("MIT")),
        weeks=None,
    )
    client = cast(GitHubClient, _StubClient(repo))

    with caplog.at_level(logging.DEBUG, logger="github_metrics.metrics"):
        metrics = collect_repository_metrics(client, "owner/repo", contributor_limit=25)

    assert metrics.contributors == 1
    assert metrics.commits_last_year == 0
    assert "truncated" not in caplog.text


def test_collect_geocodes_contributor_locations_when_asked() -> None:
    repo = _StubRepo(
        contributors=[
            _StubUser("mapped", "Austin, TX"),
            _StubUser("unresolvable", "Atlantis"),
            _StubUser("blank", None),
        ],
        license_file=_StubLicenseFile(_StubLicense("MIT")),
    )
    client = cast(GitHubClient, _StubClient(repo))
    geocoder = cast(Geocoder, _StubGeocoder({"Austin, TX": (30.27, -97.74)}))

    metrics = collect_repository_metrics(client, "owner/repo", geocoder=geocoder)

    mapped, unresolvable, blank = metrics.contributor_locations
    assert (mapped.latitude, mapped.longitude) == (30.27, -97.74)
    # A location the geocoder cannot resolve keeps its raw text and no coordinates.
    assert unresolvable.raw_location == "Atlantis"
    assert unresolvable.latitude is None
    # A contributor with no location is recorded, not dropped.
    assert blank.raw_location is None
    assert blank.latitude is None


def test_collect_skips_geocoding_when_no_geocoder_is_supplied() -> None:
    repo = _StubRepo(
        contributors=[_StubUser("dev0", "Austin, TX")],
        license_file=_StubLicenseFile(_StubLicense("MIT")),
    )
    client = cast(GitHubClient, _StubClient(repo))

    metrics = collect_repository_metrics(client, "owner/repo")

    only = metrics.contributor_locations[0]
    assert only.raw_location == "Austin, TX"
    assert only.latitude is None
