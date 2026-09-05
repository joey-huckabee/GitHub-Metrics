"""Turning a contributor's self-reported location into a structured address.

A GitHub location is free text. `Austin, TX`, `earth`, `Bengaluru/SF` and
`she/her` are all things accounts actually publish, so this module's job is
not to parse but to ask a gazetteer and record what came back.

What a lookup produces
----------------------
An `Address`, in one of three states, and the three are deliberately
distinguishable:

- **Never asked** — every field `None`. The account published no location, so
  there was nothing to look up.
- **Asked, no match** — every field `None` except `query`, which records what
  was asked. The distinction from the first state is what separates "publishes
  nothing" from "publishes something unresolvable", and those are different
  facts about a contributor.
- **Matched** — `query`, `formatted_address` and the components the result
  carries are filled; components the result does not carry are `""`, because
  a country-level match genuinely has no city and saying so is a measurement.

Coordinates are `None` unless a match supplied them. Never `0.0`: 0,0 is a
real position in the Gulf of Guinea, so a zeroed pair plots as Null Island and
reads as data rather than as a failure.

Two APIs that do not agree, and how they are joined
---------------------------------------------------
GitHub hands over one free-text string with no schema. Nominatim answers with
a component map whose *keys vary by country*, and neither side guarantees the
other anything. Three things bridge them, and each fixes a way the naive
mapping is wrong:

**Names are pinned to English.** Nominatim returns place names in the local
language unless asked otherwise, so the same rule applied to two contributors
would see `Germany` for one and `Deutschland` for another, and `country`
would silently become unusable as a key. `LANGUAGE` forces one spelling.
`country_code` is better still - it is ISO 3166-1 alpha-2 and has no language
at all - which is why it is the field a residency rule should key on.

**A settlement is named by its kind, not by a fixed key.** Nominatim reports
`city` only for places it classes as cities; a town is `town`, a village is
`village`, and elsewhere it is `municipality`. Reading only `city` leaves the
field empty for most of the world. The fallback chains below take the first
key present, in decreasing specificity.

**The ISO 3166-2 level is not fixed either.** A US state arrives as
`ISO3166-2-lvl4`, but the first-level subdivision sits at a different admin
level in other countries, so a hard-coded `lvl4` finds nothing for them. The
code scans for every `ISO3166-2-lvl*` key and takes the **coarsest**, which is
the subdivision the `state` field names.

Cost, and why this is the slow part of a run
--------------------------------------------
Nominatim's usage policy permits **one request per second**, and that is
enforced here rather than trusted to politeness - the penalty for exceeding it
is the service blocking the user agent, which fails every later run rather
than the one that misbehaved. One second per distinct location means the
geocoder, not the GitHub API, sets the pace of a large scan.

The cache is what makes that survivable, and it is keyed on a **normalised,
case-folded** form of the location. Contributor locations repeat heavily
across a portfolio but rarely byte-for-byte: `San Francisco, CA`,
`san francisco, ca` and `San  Francisco,  CA` are one place typed three ways,
and Nominatim would answer them identically. Folding them into one cache entry
turns three seconds into one, and the `query` each contributor records is
still the string that contributor's own location produced.

The cache also **survives the run**. `geocache.py` owns the file - this module
reaches the network and never parses a file format, which is the structural
rule of the package applied to its slowest component - and what expires, and
what is deliberately never written, is documented there and in
`docs/adr/0007-persistent-geocode-cache.md`. Since contributor collection is
unbounded, a scan's geocoding cost is the number of locations *never seen
before* rather than the number of distinct locations in the inventory.
"""

from __future__ import annotations

import logging
import re
import threading
import unicodedata
from typing import Any, Final

from geopy.exc import GeocoderServiceError
from geopy.extra.rate_limiter import RateLimiter
from geopy.geocoders import Nominatim

from github_metrics.geocache import GeocodeCache
from github_metrics.model.contributor import Address, Coordinates

LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS: Final = 10
"""How long one lookup may take before it is abandoned."""

MIN_SECONDS_BETWEEN_REQUESTS: Final = 1.0
"""Nominatim's published limit. Exceeding it gets the user agent blocked."""

MAX_RETRIES: Final = 2
"""Attempts after the first, for a lookup the service failed to answer.

Nominatim is a free service and refuses transiently. Retrying is worth it
because the alternative is an address recorded as unresolvable for a reason
that had nothing to do with the location.
"""

ERROR_WAIT_SECONDS: Final = 5.0
"""Pause before a retry. Long enough to be worth making, short enough to bound
the cost of a location that will never resolve at `MAX_RETRIES` times this."""


LANGUAGE: Final = "en"
"""Forces one spelling per place. See the module docstring."""

CITY_KEYS: Final = (
    "city",
    "town",
    "village",
    "hamlet",
    "locality",
    "municipality",
)
"""Settlement, most to least specific to a US address.

Nominatim names a settlement by the kind of thing it is rather than under a
fixed key, so the first present one wins. The order is US-first because that
is the residency question the contributor block is collected to answer:
`city` covers most incorporated places, `town` is ubiquitous in New England
and the Mid-Atlantic, `village` and `hamlet` cover New York's tiers, and
`locality` catches the unincorporated places that carry a name and no
government.

`municipality` is last rather than absent. It is rare in the United States and
common elsewhere - Brazil, the Nordics, the Philippines - and dropping it would
break exactly the contributors a foreign-residency rule exists to identify.
Tuning for US addresses cannot mean only US addresses.
"""

SUBURB_KEYS: Final = (
    "neighbourhood",
    "suburb",
    "borough",
    "city_district",
    "quarter",
)
"""The division below a settlement, same rule and same reasoning.

`neighbourhood` leads because it is what Nominatim returns for most named
areas inside a US city. `borough` is here for New York, where an address in
Brooklyn comes back as `city` "City of New York" with `borough` "Brooklyn" -
without it the borough, which is how anyone would actually name the place, is
dropped. It belongs in this chain rather than the settlement one for the same
reason: the city is still New York.

`quarter` and `city_district` are the non-US equivalents, kept for the reason
`municipality` is.
"""

STATE_CODE_PREFIX: Final = "ISO3166-2-lvl"
"""Nominatim reports an ISO 3166-2 code at whichever level the country uses."""

_COLLAPSIBLE = re.compile(r"\s+")
"""Any run of whitespace, including the tabs and newlines a bio can carry."""


def _normalise(location: str) -> str:
    """Reduce a free-text location to the form that is actually asked.

    Args:
        location: The account's self-reported location, verbatim.

    Returns:
        The same string with format characters removed and every run of
        whitespace collapsed to one space, trimmed. Not a parse and not a
        correction - two spellings of one place stay two spellings unless they
        differed only in whitespace.
    """
    # Zero-width joiners and direction marks travel with copied text and are
    # invisible, so two locations that look identical can differ by one.
    stripped = "".join(char for char in location if unicodedata.category(char) != "Cf")
    return _COLLAPSIBLE.sub(" ", stripped).strip()


def _first(components: dict[str, Any], keys: tuple[str, ...]) -> str:
    """Return the first key present in `components`, or `""` when none are.

    Args:
        components: Nominatim's `address` mapping.
        keys: Candidate keys, in preference order.

    Returns:
        The matched value as a string, or `""` when the result carries none of
        them - which says the match has no component of that kind, rather than
        that nothing was asked.
    """
    for key in keys:
        value = components.get(key)
        if value:
            return str(value)
    return ""


def _state_code(components: dict[str, Any]) -> str:
    """Return the ISO 3166-2 code of the first-level subdivision.

    Nominatim keys this by administrative level, and the level a country uses
    for its first-level subdivision varies - 4 for a US state, but not
    everywhere. A hard-coded `ISO3166-2-lvl4` therefore finds nothing outside
    the countries that happen to use it.

    Args:
        components: Nominatim's `address` mapping.

    Returns:
        The code at the coarsest level present, which is the subdivision
        `state` names; `""` when the match carries none.
    """
    levels: list[tuple[int, str]] = []
    for key, value in components.items():
        if not key.startswith(STATE_CODE_PREFIX) or not value:
            continue
        suffix = key[len(STATE_CODE_PREFIX) :]
        if suffix.isdigit():
            levels.append((int(suffix), str(value)))

    if not levels:
        return ""
    # Lowest admin level is the largest area, which is the first-level
    # subdivision rather than a county inside it.
    return min(levels)[1]


class Geocoder:
    """Resolve free-text locations to structured addresses, once each.

    Two caches sit behind one lookup, and they hold different things.

    The **in-process** cache holds every outcome for the length of the run,
    including service failures, so that eight workers asking about the same
    unreachable location wait once rather than eight times.

    The **persistent** cache holds only answers the gazetteer actually gave.
    A service failure is never written to it: an outage recorded as
    "unresolved" would be read back on every later run and never re-asked,
    publishing an unresolved address for a place that resolves perfectly well.
    See `docs/adr/0007-persistent-geocode-cache.md`.
    """

    def __init__(
        self,
        user_agent: str,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        cache: GeocodeCache | None = None,
    ) -> None:
        """Create a geocoder bound to a Nominatim user agent.

        Args:
            user_agent: Identifies this tool to Nominatim, as its policy
                requires. A shared or absent agent is what gets blocked.
            timeout: Seconds one lookup may take.
            cache: Where resolved locations are remembered between runs.
                `None` gives an in-memory cache that never persists, so a
                library caller opts in to a file rather than acquiring one.
        """
        locator = Nominatim(user_agent=user_agent, timeout=timeout)
        # The policy limit belongs here rather than at the call sites: there is
        # one geocoder per run, so this is the only place that can hold the
        # pace no matter how many workers are asking.
        self._geocode = RateLimiter(
            locator.geocode,
            min_delay_seconds=MIN_SECONDS_BETWEEN_REQUESTS,
            max_retries=MAX_RETRIES,
            error_wait_seconds=ERROR_WAIT_SECONDS,
            swallow_exceptions=False,
        )
        self.cache = cache if cache is not None else GeocodeCache(None)
        # Counters for statistics.json. `service_failures` is the one that
        # earns its place: it is the only record anywhere distinguishing "this
        # contributor published no location" from "the geocoder was
        # unreachable when we asked", because both produce the same `Address`.
        self.cache_hits = 0
        self.lookups = 0
        self.matched = 0
        self.unmatched = 0
        self.service_failures = 0
        # Guards both caches. It covers check-then-ask rather than only the
        # store, so that two workers asking about one location at the same
        # moment produce one request rather than two - which is the difference
        # between honouring one request per second and exceeding it.
        self._lock = threading.Lock()
        self._pending: dict[str, Address] = {}

    def locate(self, location: str) -> Address:
        """Resolve one location string.

        Args:
            location: A contributor's self-reported location, e.g. `Austin, TX`.

        Returns:
            The address it resolved to. A blank location returns an empty
            `Address`; a location that resolved to nothing returns one
            carrying only `query`, so the two remain distinguishable. A cached
            answer is indistinguishable from a fresh one, which is what lets
            the cache be deleted without changing any output.
        """
        cleaned = _normalise(location)
        if not cleaned:
            return Address()

        # The cache is keyed case-insensitively because Nominatim is, but the
        # address handed back records the spelling this contributor used.
        resolved = self._resolve(cleaned.casefold())
        return resolved.with_query(cleaned)

    def _resolve(self, key: str) -> Address:
        """Ask the gazetteer once per distinct location, or recall the answer.

        Args:
            key: A normalised, case-folded location.

        Returns:
            The address, with `query` set to `key`; callers replace it with the
            spelling they asked about.
        """
        with self._lock:
            remembered = self._pending.get(key)
            if remembered is not None:
                self.cache_hits += 1
                return remembered

            stored = self.cache.get(key)
            if stored is not None:
                self.cache_hits += 1
                self._pending[key] = stored
                return stored

            self.lookups += 1
            address, matched, persist = self._ask(key)
            if matched:
                self.matched += 1
            elif persist:
                self.unmatched += 1
            else:
                self.service_failures += 1
            self._pending[key] = address
            if persist:
                self.cache.put(key, address, matched=matched)
            return address

    def _ask(self, key: str) -> tuple[Address, bool, bool]:
        """Perform one lookup and classify what came back.

        The three outcomes are separated here and nowhere else. `locate`
        returns the same `Address` for the last two, because a contributor
        record must not reveal whether a gazetteer was reachable - but the
        cache has to tell them apart, because only one of them is a fact about
        the location.

        Args:
            key: A normalised, case-folded location.

        Returns:
            The address, whether the gazetteer matched it, and whether the
            answer may be persisted.
        """
        try:
            match = self._geocode(key, addressdetails=True, language=LANGUAGE)
        except GeocoderServiceError:
            # The normalised form, because that is what was asked and because
            # one line per distinct location is the useful cardinality - a
            # cache hit does not repeat it.
            LOGGER.warning("Geocoding failed for the normalised location %r", key)
            # Not persisted: this says nothing about the location, and writing
            # it would make one outage permanent for every location it touched.
            return Address(query=key), False, False

        if match is None:
            LOGGER.debug("No match for the normalised location %r", key)
            return Address(query=key), False, True

        return _address(key, match), True, True


def _address(query: str, match: Any) -> Address:
    """Build an address from a Nominatim match.

    Args:
        query: What was asked, recorded verbatim.
        match: A geopy `Location`.

    Returns:
        The decomposed address. Components the match does not carry are `""`.
    """
    raw = match.raw if isinstance(match.raw, dict) else {}
    components = raw.get("address") or {}

    return Address(
        query=query,
        formatted_address=str(match.address) if match.address else "",
        street=_first(components, ("road", "pedestrian", "residential")),
        house_number=_first(components, ("house_number",)),
        suburb=_first(components, SUBURB_KEYS),
        post_code=_first(components, ("postcode",)),
        state=_first(components, ("state", "province", "region")),
        state_code=_state_code(components),
        state_district=_first(components, ("state_district",)),
        county=_first(components, ("county",)),
        country=_first(components, ("country",)),
        # Lower-cased explicitly: ISO 3166-1 alpha-2 is conventionally lower
        # here and a rule keyed on it must not have to case-fold first.
        country_code=_first(components, ("country_code",)).lower(),
        city=_first(components, CITY_KEYS),
        internal_location=Coordinates(
            latitude=float(match.latitude),
            longitude=float(match.longitude),
        ),
    )
