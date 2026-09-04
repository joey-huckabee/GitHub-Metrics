"""Tests for :mod:`github_metrics.geo`.

The distinction under test throughout is between three states that a naive
implementation collapses into one: never asked, asked and unresolved, and
resolved. Only the third is a measurement, and only keeping them apart lets a
contributor who publishes nothing be told from one who publishes `earth`.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from geopy.exc import GeocoderServiceError

from github_metrics import geo
from github_metrics.geo import Geocoder
from github_metrics.model.contributor import Address

GEO_LOGGER = "github_metrics.geo"

AUSTIN = {
    "address": {
        "road": "Congress Avenue",
        "house_number": "100",
        "neighbourhood": "Downtown",
        "postcode": "78701",
        "state": "Texas",
        "ISO3166-2-lvl4": "US-TX",
        "county": "Travis County",
        "country": "United States",
        "country_code": "us",
        "city": "Austin",
    }
}

COUNTRY_ONLY = {"address": {"country": "United States", "country_code": "us"}}


class _Match:
    """A geopy `Location`, as far as this module reads one."""

    def __init__(self, address: str, latitude: float, longitude: float, raw: Any) -> None:
        self.address = address
        self.latitude = latitude
        self.longitude = longitude
        self.raw = raw


class _Nominatim:
    """Stands in for the gazetteer. Records what it was asked."""

    def __init__(self, result: Any = None, *, fails: bool = False) -> None:
        self.result = result
        self.fails = fails
        self.asked: list[str] = []
        self.options: list[dict[str, Any]] = []

    def geocode(self, query: str, **kwargs: Any) -> Any:
        """Mimic Nominatim.geocode, recording what it was asked and how."""
        self.asked.append(query)
        self.options.append(kwargs)
        if self.fails:
            raise GeocoderServiceError("upstream is unwell")
        return self.result


@pytest.fixture
def instant(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop the pacing and the retry backoff; neither is what is under test."""
    monkeypatch.setattr(geo, "MIN_SECONDS_BETWEEN_REQUESTS", 0.0)
    monkeypatch.setattr(geo, "ERROR_WAIT_SECONDS", 0.0)


def build(monkeypatch: pytest.MonkeyPatch, locator: _Nominatim) -> Geocoder:
    """Create a geocoder bound to a stub gazetteer."""
    monkeypatch.setattr(geo, "Nominatim", lambda **_kwargs: locator)
    return Geocoder("github-metrics-tests")


# ---------------------------------------------------------------------------
# The three states
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-MET-018")
@pytest.mark.usefixtures("instant")
def test_nothing_published_is_never_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    locator = _Nominatim()
    geocoder = build(monkeypatch, locator)

    assert geocoder.locate("") == Address()
    assert geocoder.locate("   ") == Address()
    assert not locator.asked


@pytest.mark.requirement("L3-MET-018")
@pytest.mark.usefixtures("instant")
def test_asked_and_unresolved_records_the_question(monkeypatch: pytest.MonkeyPatch) -> None:
    """Different from publishing nothing, and the difference is a fact.

    `query` is what separates "this account published no location" from "this
    account published something no gazetteer recognises".
    """
    geocoder = build(monkeypatch, _Nominatim(result=None))

    address = geocoder.locate("she/her")

    assert address.query == "she/her"
    assert address.country is None
    assert address.internal_location.latitude is None


@pytest.mark.requirement("L3-MET-018")
@pytest.mark.usefixtures("instant")
def test_a_match_is_decomposed_into_components(monkeypatch: pytest.MonkeyPatch) -> None:
    geocoder = build(
        monkeypatch,
        _Nominatim(
            _Match("Austin, Travis County, Texas, United States", 30.2711, -97.7437, AUSTIN)
        ),
    )

    address = geocoder.locate("Austin, TX")

    assert address.query == "Austin, TX"
    assert address.formatted_address == "Austin, Travis County, Texas, United States"
    assert address.street == "Congress Avenue"
    assert address.house_number == "100"
    assert address.suburb == "Downtown"
    assert address.post_code == "78701"
    assert address.state == "Texas"
    assert address.state_code == "US-TX"
    assert address.county == "Travis County"
    assert address.country == "United States"
    assert address.country_code == "us"
    assert address.city == "Austin"
    assert address.internal_location.latitude == 30.2711
    assert address.internal_location.longitude == -97.7437


@pytest.mark.requirement("L3-MET-018")
@pytest.mark.usefixtures("instant")
def test_a_component_the_match_lacks_is_empty_not_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A country-level match genuinely has no city, and saying so is a measurement.

    `""` means the lookup ran and found no such component; `None` means no
    lookup ran. Collapsing the two would make a contributor whose location
    resolved to a country look like one who published nothing.
    """
    geocoder = build(monkeypatch, _Nominatim(_Match("United States", 39.78, -100.44, COUNTRY_ONLY)))

    address = geocoder.locate("United States")

    assert address.country == "United States"
    assert address.city == ""
    assert address.state == ""
    assert address.street == ""


# ---------------------------------------------------------------------------
# Null Island, and the failure that would produce it
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-MET-018")
@pytest.mark.usefixtures("instant")
def test_an_unresolved_location_never_plots(monkeypatch: pytest.MonkeyPatch) -> None:
    """0,0 is a real position in the Gulf of Guinea, so zeroes read as data."""
    geocoder = build(monkeypatch, _Nominatim(result=None))

    coordinates = geocoder.locate("nowhere").internal_location

    assert coordinates.latitude is None
    assert coordinates.longitude is None


@pytest.mark.requirement("L3-MET-018")
@pytest.mark.usefixtures("instant")
def test_a_service_failure_is_a_warning_not_a_crash(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """One unreachable gazetteer must not end a run that has been paid for."""
    geocoder = build(monkeypatch, _Nominatim(fails=True))

    with caplog.at_level(logging.WARNING, logger=GEO_LOGGER):
        address = geocoder.locate("Austin, TX")

    # The address still records the spelling this contributor used.
    assert address.query == "Austin, TX"
    assert address.country is None
    # The log names the normalised form, because that is what was asked and
    # because one line per distinct location is the useful cardinality.
    assert "austin, tx" in caplog.text


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-MET-018")
@pytest.mark.usefixtures("instant")
def test_a_repeated_location_is_asked_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cache is what makes one request per second survivable.

    Contributor locations repeat heavily across a portfolio, so the cost of a
    run is the number of distinct locations rather than of contributors.
    """
    locator = _Nominatim(_Match("Austin", 30.2711, -97.7437, AUSTIN))
    geocoder = build(monkeypatch, locator)

    first = geocoder.locate("Austin, TX")
    second = geocoder.locate("Austin, TX")

    assert locator.asked == ["austin, tx"]
    assert first == second


@pytest.mark.requirement("L3-MET-018")
@pytest.mark.usefixtures("instant")
def test_a_cached_address_cannot_be_mutated_by_a_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cache hands the same object to every caller, so it has to be frozen."""
    geocoder = build(monkeypatch, _Nominatim(_Match("Austin", 30.2711, -97.7437, AUSTIN)))

    address = geocoder.locate("Austin, TX")

    with pytest.raises(AttributeError):
        address.country = "Elsewhere"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Joining two APIs that do not agree
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-MET-018")
@pytest.mark.usefixtures("instant")
def test_place_names_are_pinned_to_one_language(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nominatim answers in the local language unless told otherwise.

    Left alone, `country` would be `Germany` for one contributor and
    `Deutschland` for another, and any rule keyed on it would silently apply
    to some accounts and not others.
    """
    locator = _Nominatim(_Match("Austin", 30.2711, -97.7437, AUSTIN))
    geocoder = build(monkeypatch, locator)

    geocoder.locate("Austin, TX")

    assert locator.options[0]["language"] == geo.LANGUAGE
    assert locator.options[0]["addressdetails"] is True


@pytest.mark.requirement("L3-MET-018")
@pytest.mark.usefixtures("instant")
@pytest.mark.parametrize(
    ("key", "expected"),
    [
        # US first: the residency question is what this block is collected for.
        ("city", "Austin"),
        ("town", "Ware"),
        ("village", "Croton-on-Hudson"),
        ("hamlet", "Wainscott"),
        ("locality", "Bandera Falls"),
        # And not only the US, because identifying who is *not* American is
        # the point of the rule these components feed.
        ("municipality", "Oeiras"),
    ],
)
def test_a_settlement_is_found_whatever_kind_it_is(
    monkeypatch: pytest.MonkeyPatch, key: str, expected: str
) -> None:
    """Nominatim names a settlement by its kind, not under a fixed `city` key.

    Reading only `city` leaves the field empty for most US addresses outside a
    large incorporated place, and for most of the world.
    """
    raw = {"address": {key: expected, "country": "Somewhere", "country_code": "xx"}}
    geocoder = build(monkeypatch, _Nominatim(_Match(expected, 1.0, 2.0, raw)))

    assert geocoder.locate("anywhere").city == expected


@pytest.mark.requirement("L3-MET-018")
@pytest.mark.usefixtures("instant")
def test_a_new_york_borough_is_kept_without_displacing_the_city(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An address in Brooklyn is in the City of New York, and in Brooklyn.

    Nominatim reports both, and dropping the borough loses how anyone would
    actually name the place. It belongs below the settlement, not instead of
    it - the city is still New York.
    """
    raw = {
        "address": {
            "borough": "Brooklyn",
            "city": "City of New York",
            "state": "New York",
            "ISO3166-2-lvl4": "US-NY",
            "country": "United States",
            "country_code": "us",
        }
    }
    geocoder = build(monkeypatch, _Nominatim(_Match("Brooklyn", 40.67, -73.94, raw)))

    address = geocoder.locate("Brooklyn, NY")

    assert address.city == "City of New York"
    assert address.suburb == "Brooklyn"
    assert address.state_code == "US-NY"


@pytest.mark.requirement("L3-MET-018")
@pytest.mark.usefixtures("instant")
def test_a_country_level_match_invents_no_state_or_county(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The components are the match's own, never the nearest place to its centre.

    A contributor who publishes "United States" has said nothing about a state,
    and the record must not acquire one. Forward-geocoding then *reverse*-
    geocoding the result does exactly that: the centroid of the contiguous US
    reverse-resolves to a county in Kansas, and every contributor naming a
    country would be recorded as living there.
    """
    raw = {"address": {"country": "United States", "country_code": "us"}}
    geocoder = build(monkeypatch, _Nominatim(_Match("United States", 39.78, -100.44, raw)))

    address = geocoder.locate("United States")

    assert address.country == "United States"
    assert address.country_code == "us"
    # Empty means "the match carries no such component", which is the truth.
    assert address.state == ""
    assert address.county == ""
    assert address.city == ""


@pytest.mark.requirement("L3-MET-018")
@pytest.mark.usefixtures("instant")
def test_coordinates_are_floats_rather_than_the_strings_the_api_sends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nominatim's raw `lat`/`lon` are strings.

    Reading them from `raw` and assigning them straight through puts strings
    in a field typed `float`, which JSON then publishes as `"39.78"`. Every
    consumer has to re-parse, and a comparison against 0 quietly stops working
    - which is how `"latitude": "0"` reached the documented example.
    """
    raw = {"lat": "39.784824", "lon": "-100.4458771", "address": {"country_code": "us"}}
    geocoder = build(monkeypatch, _Nominatim(_Match("somewhere", 39.784824, -100.4458771, raw)))

    coordinates = geocoder.locate("somewhere").internal_location

    assert isinstance(coordinates.latitude, float)
    assert isinstance(coordinates.longitude, float)


@pytest.mark.requirement("L3-MET-018")
@pytest.mark.usefixtures("instant")
def test_the_subdivision_code_is_found_at_whatever_level_a_country_uses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hard-coded `ISO3166-2-lvl4` finds nothing outside the countries using it.

    The coarsest level present is the first-level subdivision; a finer one is a
    county inside it, which is not what `state_code` names.
    """
    raw = {
        "address": {
            "ISO3166-2-lvl6": "GB-CAM",
            "ISO3166-2-lvl4": "GB-ENG",
            "state": "England",
            "country_code": "gb",
        }
    }
    geocoder = build(monkeypatch, _Nominatim(_Match("England", 52.2, 0.1, raw)))

    assert geocoder.locate("Cambridge, UK").state_code == "GB-ENG"


@pytest.mark.requirement("L3-MET-018")
@pytest.mark.usefixtures("instant")
def test_a_country_code_is_lower_cased(monkeypatch: pytest.MonkeyPatch) -> None:
    """A residency rule keys on this, and must not have to case-fold first."""
    raw = {"address": {"country": "United States", "country_code": "US"}}
    geocoder = build(monkeypatch, _Nominatim(_Match("United States", 39.0, -100.0, raw)))

    assert geocoder.locate("USA").country_code == "us"


# ---------------------------------------------------------------------------
# Normalisation, which is what the cache is keyed on
# ---------------------------------------------------------------------------


@pytest.mark.requirement("L3-MET-018")
@pytest.mark.usefixtures("instant")
def test_capitalisation_and_spacing_are_one_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """One place typed three ways costs one second, not three.

    Nominatim is case-insensitive and would answer all three identically, so
    merging them loses nothing and the geocoder is the pace of a large run.
    """
    locator = _Nominatim(_Match("Austin", 30.2711, -97.7437, AUSTIN))
    geocoder = build(monkeypatch, locator)

    geocoder.locate("San Francisco, CA")
    geocoder.locate("san francisco, ca")
    geocoder.locate("San  Francisco,   CA")
    geocoder.locate("  San Francisco, CA  ")

    assert locator.asked == ["san francisco, ca"]


@pytest.mark.requirement("L3-MET-018")
@pytest.mark.usefixtures("instant")
def test_each_contributor_records_its_own_spelling(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cache is shared; `query` is not.

    Two accounts that wrote a place differently should each see what they
    wrote, or the record stops describing the account it belongs to.
    """
    geocoder = build(monkeypatch, _Nominatim(_Match("Austin", 30.2711, -97.7437, AUSTIN)))

    first = geocoder.locate("Austin, TX")
    second = geocoder.locate("austin, tx")

    assert first.query == "Austin, TX"
    assert second.query == "austin, tx"
    assert first.city == second.city == "Austin"


@pytest.mark.requirement("L3-MET-018")
@pytest.mark.usefixtures("instant")
def test_invisible_characters_do_not_split_a_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero-width characters travel with copied text and cannot be seen.

    Two locations that look identical would otherwise be two cache entries and
    two seconds.
    """
    locator = _Nominatim(_Match("Austin", 30.2711, -97.7437, AUSTIN))
    geocoder = build(monkeypatch, locator)

    geocoder.locate("Austin, TX")
    geocoder.locate("Austin,\u200b TX")

    assert locator.asked == ["austin, tx"]


@pytest.mark.requirement("L3-MET-018")
@pytest.mark.usefixtures("instant")
def test_a_location_of_only_whitespace_is_never_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing publishable was published, so nothing is looked up."""
    locator = _Nominatim()
    geocoder = build(monkeypatch, locator)

    assert geocoder.locate("\t\n  ") == Address()
    assert not locator.asked
