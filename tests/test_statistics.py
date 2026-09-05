"""Tests for the scan statistics document.

The artifact exists to stop a sample being read as a census, so most of what is
checked here is that a number carries its bound: coverage against a real total,
exclusions counted in people *and* commits, and the unknown-location share that
limits every geographic claim.
"""

from __future__ import annotations

from typing import Any

import pytest

from github_metrics.analysis.statistics import build_repository_statistics
from github_metrics.model.contributor import Address, Contributor, Coordinates
from github_metrics.model.software import SoftwareRow
from github_metrics.model.statistics import (
    AttributionMethod,
    BudgetStatistics,
    Exclusion,
    ExclusionReason,
    IdentityGaps,
    RepositoryStatistics,
    ScanStatistics,
    percent,
)


def person(
    name: str,
    commits: int,
    *,
    country: str | None = None,
    location: str | None = None,
    is_bot: bool = False,
) -> Contributor:
    """One contributor, with as much address as the test needs."""
    if country:
        address = Address(query=location or country, country_code=country, country=country)
    elif location:
        address = Address(query=location)
    else:
        address = Address()
    return Contributor(
        github_id=name,
        name=name,
        contribution=commits,
        is_bot=is_bot,
        internal_address=address,
    )


def build(*contributors: Contributor, **kwargs: Any) -> RepositoryStatistics:
    """Summarise a repository from the contributors given."""
    return build_repository_statistics(
        SoftwareRow(owner="pypa", name="virtualenv", url="https://github.com/pypa/virtualenv"),
        contributors,
        collected=True,
        documented=True,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Coverage: the number the artifact exists for
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-STA-001")
def test_coverage_measures_attributed_commits_against_the_repository_total() -> None:
    """87% of the work characterised is a different claim from a census."""
    stats = build(person("a", 800), person("b", 70), commits_total=1000)

    mapping = stats.to_mapping()

    assert mapping["commits"]["attributed"] == 870
    assert mapping["commits"]["total"] == 1000
    assert mapping["commits"]["coverage_percent"] == 87.0


@pytest.mark.requirement("L3-STA-001")
def test_coverage_is_null_rather_than_zero_when_the_total_is_unknown() -> None:
    """A repository whose commit count could not be read has no coverage.

    Zero would say none of its work was characterised, which is a measurement
    nobody made.
    """
    mapping = build(person("a", 10)).to_mapping()

    assert mapping["commits"]["total"] is None
    assert mapping["commits"]["coverage_percent"] is None


@pytest.mark.requirement("L3-STA-001")
def test_contributor_coverage_reports_the_identities_github_knows_of() -> None:
    mapping = build(person("a", 10), person("b", 5), gaps=IdentityGaps(identities=100)).to_mapping()

    assert mapping["contributors"] == {
        "identities": 100,
        "collected": 2,
        "coverage_percent": 2.0,
    }


@pytest.mark.requirement("L3-STA-001")
def test_identities_default_to_what_was_collected() -> None:
    """Without the extra request that reveals the rest, that is all we know."""
    mapping = build(person("a", 10)).to_mapping()

    assert mapping["contributors"]["identities"] == 1
    assert mapping["contributors"]["coverage_percent"] == 100.0


@pytest.mark.requirement("L3-STA-001")
def test_a_percentage_of_nothing_is_unknown_rather_than_zero() -> None:
    assert percent(0, 0) is None
    assert percent(0, 10) == 0.0


# ---------------------------------------------------------------------------
# Exclusions: who is missing, and why
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-STA-002")
def test_the_three_location_states_are_counted_separately() -> None:
    """Never asked, asked-and-unresolved, and resolved are different facts."""
    stats = build(
        person("resolved", 100, country="us"),
        person("unresolved", 50, location="she/her"),
        person("silent", 25),
    )

    found = {item["reason"]: item for item in stats.to_mapping()["exclusions"]}

    assert found["no_location_published"] == {
        "reason": "no_location_published",
        "people": 1,
        "commits": 25,
    }
    assert found["location_unresolved"]["commits"] == 50
    assert "anonymous_no_account" not in found


@pytest.mark.requirement("L3-STA-002")
def test_an_exclusion_counts_people_and_commits_separately() -> None:
    """GitHub's ceiling drops most of the people and little of the work.

    Reporting one count without the other would make that look like either a
    catastrophe or a triviality.
    """
    stats = build(
        person("a", 900, country="us"),
        gaps=IdentityGaps(
            unrecoverable=Exclusion(ExclusionReason.ANONYMOUS_NO_ACCOUNT, people=2914, commits=100)
        ),
    )

    found = next(
        item
        for item in stats.to_mapping()["exclusions"]
        if item["reason"] == "anonymous_no_account"
    )

    assert found["people"] == 2914
    assert found["commits"] == 100


@pytest.mark.requirement("L3-STA-002")
def test_a_reason_that_did_not_apply_is_omitted_rather_than_reported_as_zero() -> None:
    """Listing every reason for every repository buries the ones that bit."""
    stats = build(person("a", 10, country="us"))

    assert not stats.to_mapping()["exclusions"]


@pytest.mark.requirement("L3-STA-002")
def test_only_the_collection_gaps_say_the_person_is_absent() -> None:
    assert ExclusionReason.ANONYMOUS_NO_ACCOUNT.is_collection_gap
    assert ExclusionReason.ACCOUNT_UNRESOLVABLE.is_collection_gap
    assert not ExclusionReason.NO_LOCATION_PUBLISHED.is_collection_gap
    assert not ExclusionReason.GEOCODER_UNAVAILABLE.is_collection_gap


# ---------------------------------------------------------------------------
# Bots
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-STA-003")
def test_bots_are_counted_but_the_attributed_total_keeps_them() -> None:
    """The raw total stays raw; the adjusted figure sits beside it.

    Changing `contribution_total` would be its third redefinition in as many
    releases and would bake one judgement into a measurement.
    """
    stats = build(
        person("human", 700),
        person("dependabot[bot]", 300, is_bot=True),
        commits_total=1000,
    )

    mapping = stats.to_mapping()

    assert mapping["commits"]["attributed"] == 1000
    assert mapping["bots"] == {
        "count": 1,
        "commits": 300,
        "logins": ["dependabot[bot]"],
        "contribution_excluding_bots": 700,
    }


@pytest.mark.requirement("L3-STA-003")
def test_a_repository_with_no_bots_reports_zero_rather_than_nothing() -> None:
    """Zero bots is a measurement; the field is always present."""
    mapping = build(person("human", 10)).to_mapping()

    assert mapping["bots"]["count"] == 0
    assert not mapping["bots"]["logins"]


# ---------------------------------------------------------------------------
# Concentration
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-STA-004")
def test_concentration_reports_where_the_work_sits() -> None:
    people = [person("top", 600), *(person(f"p{i}", 50) for i in range(8))]

    found = build(*people).to_mapping()["concentration"]

    assert found["top_1_percent"] == 60.0
    assert found["bus_factor"] == 1
    assert 0 < found["gini"] < 1


@pytest.mark.requirement("L3-STA-004")
def test_the_bus_factor_needs_more_than_half_rather_than_exactly_half() -> None:
    """A contributor holding exactly 50% does not carry the project alone.

    The boundary is worth pinning: this project has been caught once already by
    a threshold where an exact value matched no branch, and `>` against `>=`
    here is the same shape of mistake.
    """
    exactly_half = build(person("top", 500), *(person(f"p{i}", 50) for i in range(10)))
    just_over = build(person("top", 501), *(person(f"p{i}", 50) for i in range(10)))

    assert exactly_half.to_mapping()["concentration"]["bus_factor"] == 2
    assert just_over.to_mapping()["concentration"]["bus_factor"] == 1


@pytest.mark.requirement("L3-STA-004")
def test_an_even_distribution_has_a_gini_of_zero_and_a_high_bus_factor() -> None:
    found = build(*(person(f"p{i}", 10) for i in range(10))).to_mapping()["concentration"]

    assert found["gini"] == 0.0
    assert found["bus_factor"] == 6


@pytest.mark.requirement("L3-STA-004")
def test_concentration_sorts_rather_than_trusting_input_order() -> None:
    """A deep-attribution walk need not return contributors ranked."""
    unsorted = build(person("small", 10), person("big", 990))
    ranked = build(person("big", 990), person("small", 10))

    assert unsorted.to_mapping()["concentration"] == ranked.to_mapping()["concentration"]


@pytest.mark.requirement("L3-STA-004")
def test_a_repository_with_no_contributors_reports_no_concentration() -> None:
    found = build().to_mapping()["concentration"]

    assert found["top_1_percent"] is None
    assert found["bus_factor"] is None


# ---------------------------------------------------------------------------
# Geography: the error bar
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-STA-005")
def test_the_unknown_location_share_bounds_every_geographic_claim() -> None:
    """Contributors with no resolved country are not dropped.

    Dropping them would make the remaining shares sum to 100 and imply a
    completeness the data has not got.
    """
    found = build(
        person("a", 100, country="us"),
        person("b", 300),
    ).to_mapping()["geography"]

    assert found["commits_with_known_location_percent"] == 25.0
    assert found["commits_with_unknown_location_percent"] == 75.0
    assert found["countries"]["us"] == {"people": 1, "commits": 100}


@pytest.mark.requirement("L3-STA-005")
def test_countries_are_ordered_by_commits_so_the_largest_reads_first() -> None:
    found = build(
        person("a", 10, country="fr"),
        person("b", 90, country="us"),
        person("c", 50, country="de"),
    ).to_mapping()["geography"]

    assert list(found["countries"]) == ["us", "de", "fr"]
    assert found["distinct_countries"] == 3


# ---------------------------------------------------------------------------
# The run-level document
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-STA-006")
def test_the_run_counts_are_derived_rather_than_tracked_separately() -> None:
    statistics = ScanStatistics(
        tool_version="0.6.0",
        repositories_named=3,
        repositories=(
            build_repository_statistics(
                SoftwareRow(owner="a", name="a"), (), collected=True, documented=True
            ),
            build_repository_statistics(
                SoftwareRow(owner="b", name="b"), (), collected=True, documented=False
            ),
            build_repository_statistics(
                SoftwareRow(owner="c", name="c"), (), collected=False, documented=False
            ),
        ),
    )

    found = statistics.to_mapping()["repositories"]

    assert found == {
        "named": 3,
        "collected": 2,
        "documented": 1,
        "failed": 1,
        "not_attempted": 0,
    }


@pytest.mark.requirement("L3-STA-006")
def test_the_rest_budget_is_null_because_it_cannot_be_measured() -> None:
    """`/rate_limit` does not track spend and a GraphQL reply overwrites the
    header, so a number here would be a guess wearing a measurement's clothes.
    """
    found = BudgetStatistics(graphql_points_spent=9, graphql_remaining=4977).to_mapping()

    assert found["graphql_points_spent"] == 9
    assert found["rest_requests_spent"] is None
    assert found["rest_remaining"] is None


@pytest.mark.requirement("L3-STA-006")
def test_the_document_records_which_attribution_method_produced_it() -> None:
    """Two methods find different populations; a consumer must tell them apart."""
    default = build(person("a", 10)).to_mapping()
    deep = build(person("a", 10), attribution=AttributionMethod.COMMIT_HISTORY).to_mapping()

    assert default["attribution"]["method"] == "contributor_list"
    assert deep["attribution"]["method"] == "commit_history"


@pytest.mark.requirement("L3-STA-006")
def test_a_run_that_collected_nothing_still_produces_a_document() -> None:
    """Its absence would be indistinguishable from the tool never running."""
    mapping = ScanStatistics(tool_version="0.6.0", repositories_named=0).to_mapping()

    assert mapping["repositories"]["named"] == 0
    assert mapping["repository_statistics"] == []
    assert mapping["tool_version"] == "0.6.0"


@pytest.mark.requirement("L3-STA-006")
def test_coordinates_do_not_leak_into_the_country_breakdown() -> None:
    """Only `country_code` decides a bucket; a match without one is unknown."""
    located = Contributor(
        name="x",
        contribution=10,
        internal_address=Address(query="somewhere", internal_location=Coordinates(1.0, 2.0)),
    )

    found = build(located).to_mapping()["geography"]

    assert found["countries"] == {}
    assert found["commits_with_unknown_location_percent"] == 100.0
