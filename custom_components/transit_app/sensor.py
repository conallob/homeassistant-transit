"""Sensor platform for the Transit App integration.

Creates one 'next departure' sensor per (stop, route, direction) that Transit
App reports departures for. New sensors are added automatically as the
coordinator discovers routes/directions it hasn't seen before (e.g. a route
that only runs at certain times of day).
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_DIRECTION_HEADSIGN,
    ATTR_GLOBAL_STOP_ID,
    ATTR_ROUTE_LONG_NAME,
    ATTR_ROUTE_SHORT_NAME,
    ATTR_STOP_NAME,
    ATTR_UPCOMING_DEPARTURES,
    CONF_GLOBAL_STOP_ID,
    CONF_STOP_NAME,
    CONF_STOPS,
    DOMAIN,
    MAX_UPCOMING_DEPARTURES,
)
from .coordinator import TransitAppDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


def _route_key(route: dict[str, Any]) -> str:
    return str(route.get("route_id") or route.get("route_short_name") or "unknown")


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Transit App departure sensors, adding new ones as discovered."""
    coordinator: TransitAppDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    known_keys: set[tuple[str, str, str]] = set()

    def _stop_name(stop_id: str) -> str:
        stops = entry.options.get(CONF_STOPS, entry.data.get(CONF_STOPS, []))
        for stop in stops:
            if stop[CONF_GLOBAL_STOP_ID] == stop_id:
                return stop.get(CONF_STOP_NAME, stop_id)
        return stop_id

    @callback
    def _async_add_new_entities() -> None:
        new_entities: list[TransitAppDepartureSensor] = []
        for stop_id, routes in (coordinator.data or {}).items():
            for route in routes:
                route_key = _route_key(route)
                for itinerary in route.get("itineraries", []):
                    direction = str(itinerary.get("direction_headsign") or "")
                    key = (stop_id, route_key, direction)
                    if key in known_keys:
                        continue
                    known_keys.add(key)
                    new_entities.append(
                        TransitAppDepartureSensor(
                            coordinator, entry, stop_id, _stop_name(stop_id), route_key, direction
                        )
                    )
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_entities))
    _async_add_new_entities()


class TransitAppDepartureSensor(CoordinatorEntity[TransitAppDataUpdateCoordinator], SensorEntity):
    """Next-departure sensor for a single route/direction at a stop."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:bus-clock"

    def __init__(
        self,
        coordinator: TransitAppDataUpdateCoordinator,
        entry: ConfigEntry,
        stop_id: str,
        stop_name: str,
        route_key: str,
        direction: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._stop_id = stop_id
        self._stop_name = stop_name
        self._route_key = route_key
        self._direction = direction
        self._attr_unique_id = f"{entry.entry_id}_{stop_id}_{route_key}_{direction}".replace(" ", "_")
        self._attr_name = f"{route_key} to {direction}" if direction else route_key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, stop_id)},
            name=f"Transit App - {stop_name}",
            manufacturer="Transit App",
            entry_type=DeviceEntryType.SERVICE,
        )

    def _current_route(self) -> dict[str, Any] | None:
        for route in (self.coordinator.data or {}).get(self._stop_id, []):
            if _route_key(route) == self._route_key:
                return route
        return None

    def _current_itinerary(self) -> dict[str, Any] | None:
        route = self._current_route()
        if route is None:
            return None
        for itinerary in route.get("itineraries", []):
            if str(itinerary.get("direction_headsign") or "") == self._direction:
                return itinerary
        return None

    def _upcoming_departure_times(self) -> list[datetime]:
        itinerary = self._current_itinerary()
        if itinerary is None:
            return []
        times: list[datetime] = []
        for item in itinerary.get("schedule_items", []):
            if item.get("is_cancelled"):
                continue
            departure_time = item.get("departure_time")
            if departure_time is None:
                continue
            times.append(datetime.fromtimestamp(departure_time, tz=timezone.utc))
        return sorted(times)

    @property
    def available(self) -> bool:
        return super().available and self._current_itinerary() is not None

    @property
    def native_value(self) -> datetime | None:
        times = self._upcoming_departure_times()
        return times[0] if times else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        route = self._current_route() or {}
        times = self._upcoming_departure_times()
        return {
            ATTR_GLOBAL_STOP_ID: self._stop_id,
            ATTR_STOP_NAME: self._stop_name,
            ATTR_ROUTE_SHORT_NAME: route.get("route_short_name"),
            ATTR_ROUTE_LONG_NAME: route.get("route_long_name"),
            ATTR_DIRECTION_HEADSIGN: self._direction,
            ATTR_UPCOMING_DEPARTURES: [
                dt.isoformat() for dt in times[:MAX_UPCOMING_DEPARTURES]
            ],
        }
