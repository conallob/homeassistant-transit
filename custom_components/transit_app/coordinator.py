"""DataUpdateCoordinator for the Transit App integration."""
from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import TransitAppApiError, TransitAppAuthError, TransitAppClient
from .const import CONF_API_KEY, CONF_GLOBAL_STOP_ID, CONF_STOPS, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class TransitAppDataUpdateCoordinator(DataUpdateCoordinator[dict[str, list[dict[str, Any]]]]):
    """Poll stop_departures for every configured stop and index by global_stop_id."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        self.client = TransitAppClient(
            async_get_clientsession(hass), entry.data[CONF_API_KEY]
        )
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )

    @property
    def global_stop_ids(self) -> list[str]:
        stops = self.entry.options.get(CONF_STOPS, self.entry.data.get(CONF_STOPS, []))
        return [stop[CONF_GLOBAL_STOP_ID] for stop in stops]

    async def _async_update_data(self) -> dict[str, list[dict[str, Any]]]:
        global_stop_ids = self.global_stop_ids
        if not global_stop_ids:
            return {}
        try:
            route_departures = await self.client.async_get_stop_departures(global_stop_ids)
        except TransitAppAuthError as err:
            raise UpdateFailed(f"Authentication failed: {err}") from err
        except TransitAppApiError as err:
            raise UpdateFailed(f"Error fetching departures: {err}") from err

        by_stop: dict[str, list[dict[str, Any]]] = {stop_id: [] for stop_id in global_stop_ids}
        for route in route_departures:
            stop_id = route.get("global_stop_id")
            if stop_id in by_stop:
                by_stop[stop_id].append(route)
        return by_stop
