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

    def geocode(self, query: str, **kwargs: Any) -> Any:
        """Mimic Nominatim.geocode, recording what it was asked."""
        del kwargs
        self.asked.append(query)
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

    assert address.query == "Austin, TX"
    assert address.country is None
    assert "Austin, TX" in caplog.text


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

    assert locator.asked == ["Austin, TX"]
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
