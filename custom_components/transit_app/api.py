"""API client for the Transit App (transitapp.com) public API v3."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .const import NEARBY_STOPS_ENDPOINT, STOP_DEPARTURES_ENDPOINT

_LOGGER = logging.getLogger(__name__)


class TransitAppApiError(Exception):
    """Raised when the Transit App API returns an unexpected error."""


class TransitAppAuthError(TransitAppApiError):
    """Raised when the Transit App API rejects the configured API key."""


class TransitAppClient:
    """Thin async wrapper around the Transit App public API."""

    def __init__(self, session: aiohttp.ClientSession, api_key: str) -> None:
        self._session = session
        self._api_key = api_key

    @property
    def _headers(self) -> dict[str, str]:
        return {"apiKey": self._api_key, "Accept-Language": "en"}

    async def _get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            async with self._session.get(
                url, headers=self._headers, params=params
            ) as resp:
                if resp.status in (401, 403):
                    raise TransitAppAuthError(
                        f"Transit App API rejected the API key (HTTP {resp.status})"
                    )
                if resp.status != 200:
                    body = await resp.text()
                    raise TransitAppApiError(
                        f"Transit App API returned HTTP {resp.status}: {body}"
                    )
                return await resp.json()
        except aiohttp.ClientError as err:
            raise TransitAppApiError(f"Error communicating with Transit App API: {err}") from err

    async def async_get_nearby_stops(
        self, latitude: float, longitude: float, max_distance: int
    ) -> list[dict[str, Any]]:
        """Return stops near the given coordinates.

        The API may return either a bare list or a dict with a "stops" key
        depending on version; both shapes are handled here.
        """
        data = await self._get(
            NEARBY_STOPS_ENDPOINT,
            {"lat": latitude, "lon": longitude, "max_distance": max_distance},
        )
        if isinstance(data, list):
            return data
        return data.get("stops", [])

    async def async_get_stop_departures(
        self, global_stop_ids: list[str]
    ) -> list[dict[str, Any]]:
        """Return route_departures for the given global_stop_ids."""
        data = await self._get(
            STOP_DEPARTURES_ENDPOINT,
            {
                "global_stop_ids": ",".join(global_stop_ids),
                "remove_cancelled": "true",
            },
        )
        return data.get("route_departures", [])
