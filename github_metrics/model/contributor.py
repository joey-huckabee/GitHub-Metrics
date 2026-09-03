"""One contributor, and the address a location resolves to.

These types exist for the per-repository JSON document. Nothing here reaches
`githubmetrics.csv`: the CSV's grain is one row per repository, and a
contributor list has no representation at that grain that would not either
break the one-row-per-input-row contract or bury a nested structure in a cell.
What the CSV carries instead are the five aggregates on `SoftwareRow`, which
are per repository and so belong there.

Empty against null
------------------
Both appear in an address and they do not mean the same thing.

- `""` — the lookup ran and returned no such component. A city-level result
  genuinely has no `county`, and saying so is a measurement.
- `null` — no lookup ran, or it failed. Nothing is known.

That is the same distinction `SoftwareRow` draws between a zero and a `None`,
applied to a string. Collapsing the two would make a contributor whose
location resolved to a country look identical to one who never published a
location at all, and any aggregate over `country` would absorb the difference
without saying so.

Coordinates are `None` when unresolved, never `0.0`. 0,0 is a real position in
the Gulf of Guinea, so zeroes plot: the defect would survive review by looking
like data rather than announcing itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from github_metrics.model.software import jsonable


@dataclass(frozen=True, slots=True)
class Coordinates:
    """A resolved latitude and longitude.

    Attributes:
        latitude: Degrees north, or `None` when the location was not resolved.
        longitude: Degrees east, or `None` when the location was not resolved.
    """

    latitude: float | None = None
    longitude: float | None = None

    def to_mapping(self) -> dict[str, Any]:
        """Render as a JSON-ready mapping."""
        return {"latitude": self.latitude, "longitude": self.longitude}


@dataclass(frozen=True, slots=True)
class Address:
    """A geocoded location, decomposed into its components.

    Every field is `None` until a lookup runs. A lookup that succeeds fills
    `query`, `formatted_address` and whichever components the result carries,
    and sets the rest to `""` — the component was asked for and does not
    exist at that resolution.

    Attributes:
        query: The location string that was looked up, verbatim.
        formatted_address: The geocoder's single-line rendering of the match.
        street: Road or street name.
        house_number: Street number.
        suburb: Suburb, neighbourhood or district within a city.
        post_code: Postal or ZIP code.
        state: First-level administrative division.
        state_code: ISO 3166-2 code for `state`, e.g. `US-KS`.
        state_district: Second-level division, where the country has one.
        county: County or equivalent.
        country: Country name, as the geocoder spells it.
        country_code: ISO 3166-1 alpha-2, lower case.
        city: City, town or village.
        internal_location: The coordinates of the match.
    """

    query: str | None = None
    formatted_address: str | None = None
    street: str | None = None
    house_number: str | None = None
    suburb: str | None = None
    post_code: str | None = None
    state: str | None = None
    state_code: str | None = None
    state_district: str | None = None
    county: str | None = None
    country: str | None = None
    country_code: str | None = None
    city: str | None = None
    internal_location: Coordinates = field(default_factory=Coordinates)

    def to_mapping(self) -> dict[str, Any]:
        """Render as a JSON-ready mapping, components before coordinates."""
        return {
            "query": self.query,
            "formatted_address": self.formatted_address,
            "street": self.street,
            "house_number": self.house_number,
            "suburb": self.suburb,
            "post_code": self.post_code,
            "state": self.state,
            "state_code": self.state_code,
            "state_district": self.state_district,
            "county": self.county,
            "country": self.country,
            "country_code": self.country_code,
            "city": self.city,
            "internal_location": self.internal_location.to_mapping(),
        }


@dataclass(frozen=True, slots=True)
class Contributor:
    """One person's contribution to one repository, at one point in time.

    Carries the run identity for the same reason every CSV row does: a
    contributor record that cannot be attributed to a run cannot be grouped
    with the rows measured beside it.

    Attributes:
        scan_id: UUID4 of the run that collected this record.
        scan_date: Start of that run, UTC.
        github_id: The account's numeric GitHub id, as a string. Stable across
            a rename, which the login is not.
        name: The account's display name, falling back to its login when the
            account publishes no name.
        organization: The account's self-reported company, or `""` when it
            publishes none.
        location: The account's self-reported location, or `None` when it
            publishes none. Free text: GitHub does not validate it.
        internal_address: What `location` resolved to.
        contribution: Commits attributed to this account in this repository.
        foreign: Whether the contributor is foreign to the United States.
            **Undefined**; always `None` until `docs/METRICS.md` settles it.
        adversarial: Whether the contributor is adversarial. **Undefined**;
            always `None` until `docs/METRICS.md` settles it.
    """

    github_id: str = ""
    name: str = ""
    organization: str = ""
    location: str | None = None
    internal_address: Address = field(default_factory=Address)
    contribution: int | None = None
    foreign: bool | None = None
    adversarial: bool | None = None
    scan_id: UUID | None = None
    scan_date: datetime | None = None

    def to_mapping(self) -> dict[str, Any]:
        """Render as a JSON-ready mapping, identity first.

        The key order is `docs/example.json`'s, not the field order: the run
        identity leads, as it does when reading a record, while the dataclass
        puts it last so that every other field can be positional.
        """
        return {
            "scan_id": jsonable(self.scan_id),
            "scan_date": jsonable(self.scan_date),
            "github_id": self.github_id,
            "name": self.name,
            "organization": self.organization,
            "location": self.location,
            "internal_address": self.internal_address.to_mapping(),
            "contribution": self.contribution,
            "foreign": self.foreign,
            "adversarial": self.adversarial,
        }
