"""Tests for :mod:`github_metrics.analysis.trusted_orgs`."""

from __future__ import annotations

import logging

import pytest

from github_metrics.analysis.trusted_orgs import (
    DEFAULT_TRUSTED_ORGANIZATIONS,
    TrustedOrganizations,
    is_trusted_org,
)

LOGGER_NAME = "github_metrics.analysis.trusted_orgs"


@pytest.mark.requirement("L3-TRU-001")
@pytest.mark.parametrize("owner", ["spring-projects", "google", "hibernate"])
def test_the_listed_owners_are_trusted(owner: str) -> None:
    assert is_trusted_org(owner) is True


@pytest.mark.requirement("L3-TRU-001")
@pytest.mark.parametrize("owner", ["cline", "pypa", "urllib3", "torvalds", "bokeh"])
def test_an_unlisted_owner_is_not_trusted(owner: str) -> None:
    assert is_trusted_org(owner) is False


@pytest.mark.requirement("L3-TRU-001")
def test_the_default_list_holds_exactly_the_three_agreed_entries() -> None:
    assert set(DEFAULT_TRUSTED_ORGANIZATIONS) == {"spring-projects", "google", "hibernate"}


@pytest.mark.requirement("L3-TRU-002")
@pytest.mark.parametrize("spelling", ["google", "Google", "GOOGLE", "  GoOgLe  "])
def test_matching_ignores_case_and_padding(spelling: str) -> None:
    # GitHub account names are case-insensitive, and an inventory typed by
    # hand will carry every spelling of them.
    assert is_trusted_org(spelling) is True


@pytest.mark.requirement("L3-TRU-002")
def test_a_registry_folds_the_case_of_keys_it_is_given() -> None:
    registry = TrustedOrganizations({"Acme-Corp": "Acme"})

    assert registry.is_trusted("acme-corp") is True
    assert registry.is_trusted("ACME-CORP") is True


@pytest.mark.requirement("L3-TRU-003")
def test_the_institution_behind_an_owner_is_recoverable() -> None:
    registry = TrustedOrganizations()

    # The values are the institution behind the organisation, which is not
    # what GitHub reports as the organisation's name: GitHub says "Spring"
    # for spring-projects and "Hibernate" for hibernate.
    assert registry.institution_for("spring-projects") == "VMware:"
    assert registry.institution_for("hibernate") == "Redhat"
    assert registry.institution_for("google") == "Google"


@pytest.mark.requirement("L3-TRU-003")
def test_an_untrusted_owner_has_no_institution() -> None:
    assert TrustedOrganizations().institution_for("cline") is None


@pytest.mark.requirement("L3-TRU-003")
def test_the_values_are_preserved_exactly_as_supplied() -> None:
    """The trailing colon and the unspaced Redhat are kept on purpose.

    Both look like slips, but this is reference data rather than code.
    Correcting it silently would change output someone may already be matching
    on, so it is preserved until the change is asked for.
    """
    assert DEFAULT_TRUSTED_ORGANIZATIONS["spring-projects"].endswith(":")
    assert DEFAULT_TRUSTED_ORGANIZATIONS["hibernate"] == "Redhat"


@pytest.mark.requirement("L3-TRU-004")
def test_a_caller_can_supply_its_own_list() -> None:
    # Trust is policy. An analysis that trusts a different set of institutions
    # is a different analysis, not a different program.
    registry = TrustedOrganizations({"apache": "ASF"})

    assert registry.is_trusted("apache") is True
    assert registry.is_trusted("google") is False
    assert len(registry) == 1


@pytest.mark.requirement("L3-TRU-004")
def test_an_empty_list_trusts_nobody() -> None:
    registry = TrustedOrganizations({})

    assert registry.is_trusted("google") is False
    assert len(registry) == 0


@pytest.mark.requirement("L3-TRU-004")
def test_the_default_list_cannot_be_mutated_by_a_caller() -> None:
    # A mutable default would let one run's edit leak into the next in the
    # same process.
    with pytest.raises(TypeError):
        DEFAULT_TRUSTED_ORGANIZATIONS["evil"] = "no"  # type: ignore[index]

    with pytest.raises(TypeError):
        TrustedOrganizations().entries["evil"] = "no"  # type: ignore[index]


@pytest.mark.requirement("L3-TRU-002")
def test_the_lookup_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        is_trusted_org("google")

    assert "google" in caplog.text
    assert "True" in caplog.text


@pytest.mark.requirement("L3-TRU-004")
def test_the_loaded_list_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        TrustedOrganizations()

    assert "Trusted organisations loaded" in caplog.text
    assert "spring-projects" in caplog.text
