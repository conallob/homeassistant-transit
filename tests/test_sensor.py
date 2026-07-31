"""Tests for the Transit App sensor platform."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.transit_app.const import DOMAIN


@pytest.fixture(name="client_get_departures")
def client_get_departures_fixture():
    with patch(
        "custom_components.transit_app.coordinator.TransitAppClient.async_get_stop_departures",
    ) as mocked:
        yield mocked


async def test_sensor_created_with_expected_state_and_attributes(
    hass: HomeAssistant, config_entry, stop_departures_response, client_get_departures
) -> None:
    """A route/direction found in stop_departures becomes a timestamp sensor."""
    client_get_departures.return_value = stop_departures_response

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    unique_id = f"{config_entry.entry_id}_DUBIE:72440_E1_City_Centre"
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None

    expected_next_departure = datetime.fromtimestamp(2000000000, tz=timezone.utc)
    assert datetime.fromisoformat(state.state) == expected_next_departure

    assert state.attributes["route_short_name"] == "E1"
    assert state.attributes["route_long_name"] == "Example Route 1"
    assert state.attributes["direction_headsign"] == "City Centre"
    assert state.attributes["stop_name"] == "Killarney Road"
    assert state.attributes["global_stop_id"] == "DUBIE:72440"
    # The cancelled schedule_item is excluded, leaving the other two departures.
    assert len(state.attributes["upcoming_departures"]) == 2


async def test_no_departures_yields_no_sensors(
    hass: HomeAssistant, config_entry, client_get_departures
) -> None:
    """If the API reports no routes for a stop, no sensors are created."""
    client_get_departures.return_value = []

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, config_entry.entry_id)
    assert entities == []


async def test_new_route_discovered_on_later_refresh_adds_sensor(
    hass: HomeAssistant, config_entry, stop_departures_response, client_get_departures, freezer
) -> None:
    """A route that only appears on a later poll gets its own sensor added."""
    client_get_departures.return_value = []
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    assert er.async_entries_for_config_entry(registry, config_entry.entry_id) == []

    client_get_departures.return_value = stop_departures_response
    coordinator = hass.data[DOMAIN][config_entry.entry_id]
    # Past the default 5 calls/minute rate limit's 12s floor, so this
    # second real poll isn't itself blocked by the hard rate-limit guard.
    freezer.tick(15)
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    unique_id = f"{config_entry.entry_id}_DUBIE:72440_E1_City_Centre"
    assert registry.async_get_entity_id("sensor", DOMAIN, unique_id) is not None


async def test_sensor_becomes_unavailable_when_route_disappears(
    hass: HomeAssistant, config_entry, stop_departures_response, client_get_departures, freezer
) -> None:
    """If a route stops being reported, its sensor goes unavailable (not deleted)."""
    client_get_departures.return_value = stop_departures_response
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    unique_id = f"{config_entry.entry_id}_DUBIE:72440_E1_City_Centre"
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)

    client_get_departures.return_value = []
    coordinator = hass.data[DOMAIN][config_entry.entry_id]
    freezer.tick(15)
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == "unavailable"
