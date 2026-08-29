"""Geocoding helpers for turning contributor locations into coordinates."""

from __future__ import annotations

import logging
from functools import lru_cache

from geopy.exc import GeocoderServiceError
from geopy.geocoders import Nominatim

LOGGER = logging.getLogger(__name__)

# Nominatim's usage policy allows at most one request per second.
DEFAULT_TIMEOUT_SECONDS = 10


class Geocoder:
    """Resolve free-text locations to latitude/longitude pairs."""

    def __init__(self, user_agent: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> None:
        """Create a geocoder bound to a Nominatim user agent."""
        self._locator = Nominatim(user_agent=user_agent, timeout=timeout)

    @lru_cache(maxsize=1024)  # noqa: B019 - one geocoder per run; cache dies with it
    def locate(self, location: str) -> tuple[float, float] | None:
        """Geocode a free-text location.

        Args:
            location: A user-supplied location string, e.g. `Austin, TX`.

        Returns:
            A `(latitude, longitude)` pair, or `None` when the location is
            blank or cannot be resolved.
        """
        query = location.strip()
        if not query:
            return None
        try:
            match = self._locator.geocode(query)
        except GeocoderServiceError:
            LOGGER.warning("Geocoding failed for %r", query)
            return None
        if match is None:
            return None
        return (float(match.latitude), float(match.longitude))
