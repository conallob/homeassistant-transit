"""DataUpdateCoordinator for the Transit App integration."""
from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import TransitAppApiError, TransitAppAuthError, TransitAppClient
from .const import (
    CONF_API_KEY,
    CONF_GLOBAL_STOP_ID,
    CONF_PRESENCE_ENTITIES,
    CONF_QUIET_DAYS,
    CONF_QUIET_HOURS_END,
    CONF_QUIET_HOURS_START,
    CONF_STOPS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_GLOBAL_STOP_IDS_PER_REQUEST,
    STATE_HOME,
    WEEKDAYS,
)

_LOGGER = logging.getLogger(__name__)


class TransitAppDataUpdateCoordinator(DataUpdateCoordinator[dict[str, list[dict[str, Any]]]]):
    """Poll stop_departures for every configured stop and index by global_stop_id.

    To keep API usage low against a single Transit App key, this coordinator:
    - batches all of a config entry's stops into as few stop_departures
      requests as possible (splitting only if the stop count exceeds
      MAX_GLOBAL_STOP_IDS_PER_REQUEST)
    - skips polling entirely outside a configured presence filter (no
      tracked person/device_tracker is home) or during configured quiet
      hours/days, reusing the last known data instead
    """

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

    def _skip_reason(self) -> str | None:
        """Return why polling should be skipped this cycle, or None to poll."""
        quiet_days = self.entry.options.get(CONF_QUIET_DAYS, [])
        now = dt_util.now()
        if WEEKDAYS[now.weekday()] in quiet_days:
            return f"today ({WEEKDAYS[now.weekday()]}) is a configured quiet day"

        start = self.entry.options.get(CONF_QUIET_HOURS_START)
        end = self.entry.options.get(CONF_QUIET_HOURS_END)
        if start and end:
            start_time = dt_util.parse_time(start)
            end_time = dt_util.parse_time(end)
            now_time = now.time()
            if start_time != end_time:
                in_window = (
                    start_time <= now_time < end_time
                    if start_time < end_time
                    else now_time >= start_time or now_time < end_time
                )
                if in_window:
                    return f"current time is within configured quiet hours ({start}-{end})"

        presence_entities = self.entry.options.get(CONF_PRESENCE_ENTITIES, [])
        if presence_entities:
            anyone_home = any(
                (state := self.hass.states.get(entity_id)) is not None
                and state.state == STATE_HOME
                for entity_id in presence_entities
            )
            if not anyone_home:
                return "none of the configured presence entities are home"

        return None

    async def _async_update_data(self) -> dict[str, list[dict[str, Any]]]:
        global_stop_ids = self.global_stop_ids
        if not global_stop_ids:
            return {}

        skip_reason = self._skip_reason()
        if skip_reason is not None:
            _LOGGER.debug("Skipping Transit App update: %s", skip_reason)
            return self.data or {stop_id: [] for stop_id in global_stop_ids}

        by_stop: dict[str, list[dict[str, Any]]] = {stop_id: [] for stop_id in global_stop_ids}
        try:
            for i in range(0, len(global_stop_ids), MAX_GLOBAL_STOP_IDS_PER_REQUEST):
                chunk = global_stop_ids[i : i + MAX_GLOBAL_STOP_IDS_PER_REQUEST]
                route_departures = await self.client.async_get_stop_departures(chunk)
                for route in route_departures:
                    stop_id = route.get("global_stop_id")
                    if stop_id in by_stop:
                        by_stop[stop_id].append(route)
        except TransitAppAuthError as err:
            raise UpdateFailed(f"Authentication failed: {err}") from err
        except TransitAppApiError as err:
            raise UpdateFailed(f"Error fetching departures: {err}") from err

        return by_stop
