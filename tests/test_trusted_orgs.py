"""Tests for :mod:`github_metrics.analysis.trusted_orgs`."""

from __future__ import annotations

import logging

import pytest

from github_metrics.analysis.trusted_orgs import (
    DEFAULT_TRUSTED_ORGANIZATIONS,
    TRUSTED_ORG_BONUS,
    TrustedOrganizations,
    is_trusted_org,
    score_trusted_org_bonus,
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
    assert registry.institution_for("spring-projects") == "VMware"
    assert registry.institution_for("hibernate") == "Red Hat"
    assert registry.institution_for("google") == "Google"


@pytest.mark.requirement("L3-TRU-003")
def test_an_untrusted_owner_has_no_institution() -> None:
    assert TrustedOrganizations().institution_for("cline") is None


@pytest.mark.requirement("L3-TRU-003")
def test_the_institutions_are_written_as_the_institutions_write_them() -> None:
    """These names end up in a report, so their spelling is the product.

    An earlier revision carried a trailing colon on `VMware:` and an unspaced
    `Redhat`; both were transcription slips and are corrected here.
    """
    assert DEFAULT_TRUSTED_ORGANIZATIONS["spring-projects"] == "VMware"
    assert DEFAULT_TRUSTED_ORGANIZATIONS["hibernate"] == "Red Hat"
    assert DEFAULT_TRUSTED_ORGANIZATIONS["google"] == "Google"

    assert not any(value.endswith(":") for value in DEFAULT_TRUSTED_ORGANIZATIONS.values())


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


# ---------------------------------------------------------------------------
# The bonus
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-TRU-005")
@pytest.mark.parametrize("owner", ["spring-projects", "google", "hibernate"])
def test_a_trusted_owner_earns_the_bonus(owner: str) -> None:
    assert score_trusted_org_bonus(owner) == TRUSTED_ORG_BONUS


@pytest.mark.requirement("L3-TRU-005")
@pytest.mark.parametrize("owner", ["cline", "pypa", "urllib3", "torvalds"])
def test_an_untrusted_owner_earns_nothing(owner: str) -> None:
    assert score_trusted_org_bonus(owner) == 0.0


@pytest.mark.requirement("L3-TRU-005")
def test_the_bonus_is_ten_points() -> None:
    # The reference row carries is_trusted_org false and a bonus of 0, giving
    # a total_score of 72. The same repository under a trusted owner would
    # score 82.
    assert TRUSTED_ORG_BONUS == 10.0


@pytest.mark.requirement("L3-TRU-005")
def test_the_bonus_is_all_or_nothing() -> None:
    # Trust is a yes-or-no judgement, so unlike every other component there is
    # nothing for a weight to interpolate between.
    awarded = {score_trusted_org_bonus(owner) for owner in ("google", "cline", "pypa")}

    assert awarded == {TRUSTED_ORG_BONUS, 0.0}


@pytest.mark.requirement("L3-TRU-005")
@pytest.mark.parametrize("spelling", ["google", "Google", "GOOGLE", "  google  "])
def test_the_bonus_follows_the_same_case_rules_as_the_check(spelling: str) -> None:
    assert score_trusted_org_bonus(spelling) == TRUSTED_ORG_BONUS


@pytest.mark.requirement("L3-TRU-005")
def test_the_bonus_and_the_column_cannot_disagree() -> None:
    # Both resolve through the same registry, so there is one answer to "is
    # this owner trusted" rather than two that might drift.
    registry = TrustedOrganizations({"apache": "ASF"})

    for owner in ("apache", "google", "cline"):
        expected = TRUSTED_ORG_BONUS if is_trusted_org(owner, registry) else 0.0
        assert score_trusted_org_bonus(owner, registry) == expected


@pytest.mark.requirement("L3-TRU-005")
def test_a_caller_supplied_registry_changes_who_is_paid() -> None:
    registry = TrustedOrganizations({"apache": "ASF"})

    assert score_trusted_org_bonus("apache", registry) == TRUSTED_ORG_BONUS
    assert score_trusted_org_bonus("google", registry) == 0.0


@pytest.mark.requirement("L3-TRU-006")
def test_an_awarded_bonus_names_the_institution(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        score_trusted_org_bonus("hibernate")

    # The institution is why the bonus was paid, so it belongs in the record.
    assert "Red Hat" in caplog.text
    assert "hibernate" in caplog.text

    # The amount deliberately does not: it is an invariant, so logging it adds
    # a number that never varies and can only go stale against the documented
    # value. See the module docstring.
    assert str(TRUSTED_ORG_BONUS) not in caplog.text


@pytest.mark.requirement("L3-TRU-006")
def test_a_refused_bonus_is_logged_at_debug(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        score_trusted_org_bonus("cline")

    assert "No trusted-organisation bonus" in caplog.text
    assert "cline" in caplog.text
