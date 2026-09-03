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

Cost, and why this is the slow part of a run
--------------------------------------------
Nominatim's usage policy permits **one request per second**, and that is
enforced here rather than trusted to politeness - the penalty for exceeding it
is the service blocking the user agent, which fails every later run rather
than the one that misbehaved. One second per distinct location means the
geocoder, not the GitHub API, sets the pace of a large scan.

The cache is what makes that survivable. Contributor locations repeat heavily
across a portfolio - `San Francisco, CA` appears once per hundred contributors
- and only distinct strings reach the network, so the cost is the number of
distinct locations rather than the number of contributors.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Final

from geopy.exc import GeocoderServiceError
from geopy.extra.rate_limiter import RateLimiter
from geopy.geocoders import Nominatim

from github_metrics.model.contributor import Address, Coordinates

LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS: Final = 10
"""How long one lookup may take before it is abandoned."""

MIN_SECONDS_BETWEEN_REQUESTS: Final = 1.0
"""Nominatim's published limit. Exceeding it gets the user agent blocked."""

CACHE_SIZE: Final = 4096
"""Distinct locations held per run. Well above what an inventory produces."""

MAX_RETRIES: Final = 2
"""Attempts after the first, for a lookup the service failed to answer.

Nominatim is a free service and refuses transiently. Retrying is worth it
because the alternative is an address recorded as unresolvable for a reason
that had nothing to do with the location.
"""

ERROR_WAIT_SECONDS: Final = 5.0
"""Pause before a retry. Long enough to be worth making, short enough to bound
the cost of a location that will never resolve at `MAX_RETRIES` times this."""

CITY_KEYS: Final = ("city", "town", "village", "municipality")
"""Nominatim names a settlement by its kind, so the first present one wins."""

SUBURB_KEYS: Final = ("suburb", "neighbourhood", "quarter")
"""As above, for the division below a settlement."""

STATE_CODE_KEYS: Final = ("ISO3166-2-lvl4", "ISO3166-2-lvl3", "ISO3166-2-lvl6")
"""Nominatim reports an ISO 3166-2 code at whichever level the country uses."""


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


class Geocoder:
    """Resolve free-text locations to structured addresses, once each."""

    def __init__(self, user_agent: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> None:
        """Create a geocoder bound to a Nominatim user agent.

        Args:
            user_agent: Identifies this tool to Nominatim, as its policy
                requires. A shared or absent agent is what gets blocked.
            timeout: Seconds one lookup may take.
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

    @lru_cache(maxsize=CACHE_SIZE)  # noqa: B019 - one geocoder per run; cache dies with it
    def locate(self, location: str) -> Address:
        """Resolve one location string.

        Args:
            location: A contributor's self-reported location, e.g. `Austin, TX`.

        Returns:
            The address it resolved to. A blank location returns an empty
            `Address`; a location that resolved to nothing returns one
            carrying only `query`, so the two remain distinguishable.
        """
        query = location.strip()
        if not query:
            return Address()

        try:
            match = self._geocode(query, addressdetails=True)
        except GeocoderServiceError:
            LOGGER.warning("Geocoding failed for %r", query)
            return Address(query=query)

        if match is None:
            LOGGER.debug("No match for location %r", query)
            return Address(query=query)

        return _address(query, match)


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
        street=_first(components, ("road",)),
        house_number=_first(components, ("house_number",)),
        suburb=_first(components, SUBURB_KEYS),
        post_code=_first(components, ("postcode",)),
        state=_first(components, ("state",)),
        state_code=_first(components, STATE_CODE_KEYS),
        state_district=_first(components, ("state_district",)),
        county=_first(components, ("county",)),
        country=_first(components, ("country",)),
        country_code=_first(components, ("country_code",)),
        city=_first(components, CITY_KEYS),
        internal_location=Coordinates(
            latitude=float(match.latitude),
            longitude=float(match.longitude),
        ),
    )
