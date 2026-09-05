"""The contributor block of a per-repository document.

Everything here belongs to the document and **nothing here reaches
`githubmetrics.csv`**. The two artifacts are for different things: the CSV is
the comparable table, twenty columns wide and fixed so that two runs diff and
a column sorts, while a document is one repository's detail record. A
contributor array and the totals over it are what that record carries, and a
nested array has no representation at a table's grain that would not either
break the one-row-per-input-row contract or bury a structure in a cell.

What the two share is the row. Every CSV column is a document key, spelled the
same way and in the same order, and both carry the same run identity - so the
table and the documents of one run join on the run that produced them.

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

from dataclasses import dataclass, field, fields
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

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> Coordinates:
        """Rebuild from what `to_mapping` produced.

        Args:
            payload: A mapping as `to_mapping` renders one. Absent keys read
                as `None`, which is the unresolved state rather than a defect:
                a cache written by an older version is worth reading.

        Returns:
            The coordinates.
        """
        return cls(
            latitude=_optional_float(payload.get("latitude")),
            longitude=_optional_float(payload.get("longitude")),
        )


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

    def with_query(self, query: str) -> Address:
        """Return this address with `query` replaced.

        The geocoder caches one address per distinct location and hands the
        same object to every contributor who published that place, so the
        spelling each of them used is applied on the way out rather than
        stored. `dataclasses.replace` would say this in one line; it is
        written out because a checker cannot see that `replace` preserves the
        type, and a return annotation nothing can verify is worth less than
        one that can.

        Every field is carried across by name rather than listed, so a field
        added to this class cannot be silently dropped here.

        Args:
            query: The location string as the contributor published it.

        Returns:
            A new address, identical but for `query`.
        """
        carried = {item.name: getattr(self, item.name) for item in fields(self)}
        carried["query"] = query
        return Address(**carried)

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

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> Address:
        """Rebuild from what `to_mapping` produced.

        The inverse of `to_mapping`, and the reason the geocode cache can
        store an address as JSON and read it back as the same three-state
        value. Component names are taken from the dataclass rather than
        listed again, so a field added to this class round-trips without a
        second edit - the rule `to_header` and `keys` both follow.

        Args:
            payload: A mapping as `to_mapping` renders one.

        Returns:
            The address. A key the payload does not carry reads as `None`,
            which is "not known" - so a cache file written before a component
            existed degrades to unresolved for that component rather than
            failing to load.
        """
        components = {
            item.name: _optional_str(payload.get(item.name))
            for item in fields(cls)
            if item.name != "internal_location"
        }
        location = payload.get("internal_location")
        return cls(
            **components,
            internal_location=Coordinates.from_mapping(
                location if isinstance(location, dict) else {}
            ),
        )


def _optional_str(value: object) -> str | None:
    """Read a cached component, keeping `""` and `None` distinct.

    `""` means the lookup ran and the component does not exist at that
    resolution; `None` means nothing is known. Collapsing them would undo the
    distinction the whole address type exists to preserve.
    """
    if value is None:
        return None
    return str(value)


def _optional_float(value: object) -> float | None:
    """Read a cached coordinate, keeping `None` distinct from `0.0`.

    0,0 is a real position in the Gulf of Guinea, so a coercion that turned an
    absent coordinate into a zero would plot rather than announce itself.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


@dataclass(frozen=True, slots=True)
class Contributor:
    """One person's contribution to one repository, at one point in time.

    Carries the run identity for the same reason every CSV row does: a
    contributor record that cannot be attributed to a run cannot be grouped
    with the rows measured beside it.

    Attributes:
        scan_id: UUID4 of the run that collected this record.
        scan_date: Start of that run, UTC.
        github_id: The account's numeric GitHub id, **as a string**. Stable
            across a rename, which the login is not.

            A string rather than an integer, and not because Python needs it
            to be: Python integers are arbitrary-precision, so there is no
            width to check on this side. The ceiling is downstream. A JSON
            number above 2**53 - 1 loses precision in any consumer backed by
            an IEEE-754 double - JavaScript, and every tool built on it - and
            it does so silently, producing an id that is close to the right
            one. A string has no such ceiling, and nothing arithmetic is ever
            done with an account id.
        name: The account's display name, falling back to its login when the
            account publishes no name.
        organization: The account's self-reported company, or `""` when it
            publishes none.
        location: The account's self-reported location, or `None` when it
            publishes none. Free text: GitHub does not validate it.
        internal_address: What `location` resolved to.
        contribution: Commits attributed to this account in this repository.
        is_bot: Whether GitHub reports this account as `type: "Bot"`.

            Authoritative for GitHub Apps and **nothing but** that: a bot
            running under an ordinary user account reports as a user, and this
            field does not guess from a login that merely looks automated.
            `False` is therefore "GitHub did not say so", not "definitely a
            person".

            Its commits stay in `contribution_total`, because that total is a
            raw measurement of what GitHub attributed to the repository.
            Excluding them is a judgement, and this flag is what lets an
            analysis make it. `statistics.json` publishes the adjusted figure
            beside the raw one.
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
    is_bot: bool = False
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
            "is_bot": self.is_bot,
            "foreign": self.foreign,
            "adversarial": self.adversarial,
        }


@dataclass(frozen=True, slots=True)
class ContributorBlock:
    """One repository's contributors, and the aggregates over them.

    The block exists only for a repository whose contributor list was read, so
    every aggregate here is a real measurement rather than a placeholder -
    which is what lets `contribution_total` be a plain `int`. A repository
    whose contributors could not be collected produces a CSV row and no
    document at all, so there is no state in which these numbers are unknown.

    Attributes:
        contributors: The records, most commits first.
        contribution_total: Sum of `contribution` over `contributors`. Counts
            what was collected rather than what exists: every contributor
            GitHub returns, which is not every contributor a repository has
            had — GitHub links only the first 500 author email addresses to
            accounts. `docs/METRICS.md` says so. `0` for a repository that
            genuinely has none.
        foreign_contribution: Commits by contributors foreign to the United
            States. **Undefined**; always `None` until `docs/METRICS.md`
            settles the rule.
        adversarial_contribution: Commits by adversarial contributors.
            **Undefined**, as above.
        foreign_percent: `foreign_contribution` as a percentage of
            `contribution_total`. **Undefined**, as its numerator is.
        adversarial_percent: `adversarial_contribution` as a percentage of
            `contribution_total`. **Undefined**, as its numerator is.
    """

    contributors: tuple[Contributor, ...] = ()
    contribution_total: int = 0
    foreign_contribution: int | None = None
    adversarial_contribution: int | None = None
    foreign_percent: float | None = None
    adversarial_percent: float | None = None

    def to_mapping(self) -> dict[str, Any]:
        """Render as JSON-ready keys, contributors before the aggregates.

        The order is `docs/example.json`'s: the list first, then what is
        summed over it, which is the order someone reads them in.
        """
        return {
            "contributors": [entry.to_mapping() for entry in self.contributors],
            "contribution_total": self.contribution_total,
            "foreign_contribution": self.foreign_contribution,
            "adversarial_contribution": self.adversarial_contribution,
            "foreign_percent": self.foreign_percent,
            "adversarial_percent": self.adversarial_percent,
        }

    @classmethod
    def keys(cls) -> tuple[str, ...]:
        """The document keys this block contributes, in order.

        Derived from `to_mapping` rather than restated, so a key cannot be
        added in one place and missed in the other — the same rule
        `SoftwareRow.to_header` follows.
        """
        return tuple(cls().to_mapping())
