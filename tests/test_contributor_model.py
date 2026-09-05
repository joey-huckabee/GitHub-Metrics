"""Tests for :mod:`github_metrics.model.contributor`.

These types are rendered by hand-written `to_mapping` methods rather than by a
serialiser, which buys explicit key order at the cost of a way to drift: a
field can be declared and never rendered, or rendered and never declared.

That second one is not hypothetical. The shape these types implement was
prototyped with `dataclasses_json`, and in that prototype `InternalAddress`
assigned `self.country` without declaring `country` as a field. Python allows
the assignment, so nothing failed - but `to_dict()` serialises declared fields
only, so **every address silently lost its country**, which is the single
component the `foreign` rule most depends on.

The first test here is the check for that. It compares the declared fields
against the rendered keys in both directions, so neither kind of drift can
reach an output file.
"""

from __future__ import annotations

from dataclasses import fields

import pytest

from github_metrics.model.contributor import (
    Address,
    Contributor,
    ContributorBlock,
    Coordinates,
)


@pytest.mark.requirement("L3-OUT-012")
@pytest.mark.parametrize(
    "record",
    [Coordinates(), Address(), Contributor(), ContributorBlock()],
    ids=["coordinates", "address", "contributor", "block"],
)
def test_every_declared_field_is_rendered_and_every_rendered_key_declared(
    record: Coordinates | Address | Contributor | ContributorBlock,
) -> None:
    """The two must agree exactly, in both directions.

    A declared field missing from `to_mapping` is dropped from every document
    without failing. A rendered key that is not a field is a value nothing
    populates. Neither announces itself.
    """
    declared = {item.name for item in fields(record)}
    rendered = set(record.to_mapping())

    assert declared - rendered == set(), "declared but never rendered"
    assert rendered - declared == set(), "rendered but never declared"


@pytest.mark.requirement("L3-OUT-012")
def test_the_address_carries_country_and_country_code() -> None:
    """Named because losing `country` is the defect this file exists for.

    `foreign` resolves against a country, so an address that drops it is not
    merely incomplete - it is unable to answer the question the block is
    collected for.
    """
    declared = {item.name for item in fields(Address)}

    assert "country" in declared
    assert "country_code" in declared
    assert "country" in Address().to_mapping()


@pytest.mark.requirement("L3-OUT-012")
def test_the_block_keys_are_derived_rather_than_restated() -> None:
    """`keys()` must follow `to_mapping`, not a second list beside it."""
    assert ContributorBlock.keys() == tuple(ContributorBlock().to_mapping())


@pytest.mark.requirement("L3-OUT-012")
def test_a_github_id_survives_beyond_the_javascript_safe_integer() -> None:
    """The id is a string, and that is what makes it safe to publish.

    Python integers are arbitrary-precision, so nothing on this side needs a
    width check. The risk is downstream: JSON numbers above 2**53 - 1 lose
    precision in any consumer backed by an IEEE-754 double, JavaScript
    included, and they do it silently. A string has no such ceiling.
    """
    huge = str(2**63 - 1)
    rendered = Contributor(github_id=huge).to_mapping()

    assert rendered["github_id"] == huge
    assert isinstance(rendered["github_id"], str)


@pytest.mark.requirement("L3-OUT-012")
def test_two_blocks_do_not_share_one_address() -> None:
    """`field(default_factory=...)`, never a shared instance.

    A default evaluated once at class-definition time is shared by every
    instance for the life of the process - the same trap that gives every
    `ScanIdentifier` one UUID if its default is written `uuid4()` rather than
    `field(default_factory=uuid4)`.
    """
    first = Contributor()
    second = Contributor()

    assert first.internal_address == second.internal_address
    assert first.internal_address is not second.internal_address


@pytest.mark.requirement("L3-MET-018")
def test_with_query_carries_every_other_field() -> None:
    """The copy is written out rather than delegated to `dataclasses.replace`.

    Writing it out is what a type checker can verify; the risk it introduces
    is that a field added to `Address` gets forgotten. This is the check, and
    it compares against the declared fields rather than a list, so it cannot
    be forgotten in turn.
    """
    original = Address(
        query="asked",
        formatted_address="Austin, Travis County, Texas, United States",
        street="Congress Avenue",
        house_number="100",
        suburb="Downtown",
        post_code="78701",
        state="Texas",
        state_code="US-TX",
        state_district="",
        county="Travis County",
        country="United States",
        country_code="us",
        city="Austin",
        internal_location=Coordinates(latitude=30.2711, longitude=-97.7437),
    )

    copied = original.with_query("as this contributor wrote it")

    assert copied.query == "as this contributor wrote it"
    for item in fields(Address):
        if item.name == "query":
            continue
        assert getattr(copied, item.name) == getattr(original, item.name), item.name


@pytest.mark.requirement("L3-MET-020")
def test_from_mapping_recovers_every_declared_field() -> None:
    """The inverse of `to_mapping`, compared against the dataclass itself.

    Like `with_query`, this is checked against the declared fields rather than
    a list, so a field added to `Address` cannot round-trip silently wrong.
    """
    populated = Address(
        **{
            item.name: f"value-{item.name}"
            for item in fields(Address)
            if item.name != "internal_location"
        },
        internal_location=Coordinates(latitude=1.5, longitude=-2.5),
    )

    assert Address.from_mapping(populated.to_mapping()) == populated


@pytest.mark.requirement("L3-MET-020")
def test_from_mapping_reads_an_absent_key_as_unknown() -> None:
    """A cache written before a component existed degrades, rather than fails."""
    restored = Address.from_mapping({"query": "austin, tx", "city": "Austin"})

    assert restored.query == "austin, tx"
    assert restored.city == "Austin"
    assert restored.country is None
    assert restored.internal_location == Coordinates(None, None)


@pytest.mark.requirement("L3-MET-020")
def test_from_mapping_refuses_to_invent_a_coordinate() -> None:
    """A non-numeric coordinate reads as absent, never as Null Island."""
    restored = Address.from_mapping({"internal_location": {"latitude": "north", "longitude": None}})

    assert restored.internal_location == Coordinates(None, None)
