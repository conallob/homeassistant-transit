"""Tests for TransitAppDataUpdateCoordinator."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest

from custom_components.transit_app.api import TransitAppApiError, TransitAppAuthError
from custom_components.transit_app.const import (
    CONF_GLOBAL_STOP_ID,
    CONF_PRESENCE_ENTITIES,
    CONF_QUIET_DAYS,
    CONF_QUIET_HOURS_END,
    CONF_QUIET_HOURS_START,
    CONF_STOP_NAME,
    CONF_STOPS,
    MAX_GLOBAL_STOP_IDS_PER_REQUEST,
)
from custom_components.transit_app.coordinator import TransitAppDataUpdateCoordinator


async def test_update_data_indexes_by_stop(
    hass: HomeAssistant, config_entry, stop_departures_response
) -> None:
    """Departures are grouped by global_stop_id, including stops with none."""
    coordinator = TransitAppDataUpdateCoordinator(hass, config_entry)
    coordinator.client.async_get_stop_departures = AsyncMock(
        return_value=stop_departures_response
    )

    data = await coordinator._async_update_data()

    assert data == {"DUBIE:72440": stop_departures_response}
    coordinator.client.async_get_stop_departures.assert_awaited_once_with(
        ["DUBIE:72440"]
    )


async def test_no_stops_configured_skips_api_call(
    hass: HomeAssistant, config_entry
) -> None:
    """With no tracked stops, no API call is made at all."""
    hass.config_entries.async_update_entry(
        config_entry, data={**config_entry.data, CONF_STOPS: []}
    )
    coordinator = TransitAppDataUpdateCoordinator(hass, config_entry)
    coordinator.client.async_get_stop_departures = AsyncMock()

    data = await coordinator._async_update_data()

    assert data == {}
    coordinator.client.async_get_stop_departures.assert_not_awaited()


async def test_auth_error_raises_update_failed(hass: HomeAssistant, config_entry) -> None:
    """An auth error from the API surfaces as UpdateFailed."""
    coordinator = TransitAppDataUpdateCoordinator(hass, config_entry)
    coordinator.client.async_get_stop_departures = AsyncMock(
        side_effect=TransitAppAuthError("nope")
    )

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_generic_api_error_raises_update_failed(
    hass: HomeAssistant, config_entry
) -> None:
    """A generic API error also surfaces as UpdateFailed."""
    coordinator = TransitAppDataUpdateCoordinator(hass, config_entry)
    coordinator.client.async_get_stop_departures = AsyncMock(
        side_effect=TransitAppApiError("boom")
    )

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_stops_are_chunked_across_requests(
    hass: HomeAssistant, config_entry
) -> None:
    """More stops than the per-request limit are split into multiple calls."""
    many_stops = [
        {CONF_GLOBAL_STOP_ID: f"AGENCY:{i}", CONF_STOP_NAME: f"Stop {i}"}
        for i in range(MAX_GLOBAL_STOP_IDS_PER_REQUEST + 5)
    ]
    hass.config_entries.async_update_entry(
        config_entry, data={**config_entry.data, CONF_STOPS: many_stops}
    )
    coordinator = TransitAppDataUpdateCoordinator(hass, config_entry)
    coordinator.client.async_get_stop_departures = AsyncMock(return_value=[])

    await coordinator._async_update_data()

    assert coordinator.client.async_get_stop_departures.await_count == 2
    first_call_ids = coordinator.client.async_get_stop_departures.await_args_list[0].args[0]
    second_call_ids = coordinator.client.async_get_stop_departures.await_args_list[1].args[0]
    assert len(first_call_ids) == MAX_GLOBAL_STOP_IDS_PER_REQUEST
    assert len(second_call_ids) == 5


async def test_fewer_stops_than_limit_uses_single_request(
    hass: HomeAssistant, config_entry
) -> None:
    """A typical config entry batches all of its stops into one request."""
    coordinator = TransitAppDataUpdateCoordinator(hass, config_entry)
    coordinator.client.async_get_stop_departures = AsyncMock(return_value=[])

    await coordinator._async_update_data()

    assert coordinator.client.async_get_stop_departures.await_count == 1


async def test_quiet_day_skips_polling(hass: HomeAssistant, config_entry, freezer) -> None:
    """A configured quiet day skips polling and keeps previous data."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to(datetime(2026, 8, 1, 12, 0, 0))  # a Saturday
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_QUIET_DAYS: ["sat"]}
    )
    coordinator = TransitAppDataUpdateCoordinator(hass, config_entry)
    coordinator.data = {"DUBIE:72440": ["stale-but-previous-data"]}
    coordinator.client.async_get_stop_departures = AsyncMock(return_value=[])

    data = await coordinator._async_update_data()

    coordinator.client.async_get_stop_departures.assert_not_awaited()
    assert data == coordinator.data


async def test_non_quiet_day_polls_normally(
    hass: HomeAssistant, config_entry, freezer
) -> None:
    """A day not in quiet_days polls as normal."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to(datetime(2026, 8, 3, 12, 0, 0))  # a Monday
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_QUIET_DAYS: ["sat", "sun"]}
    )
    coordinator = TransitAppDataUpdateCoordinator(hass, config_entry)
    coordinator.client.async_get_stop_departures = AsyncMock(return_value=[])

    await coordinator._async_update_data()

    coordinator.client.async_get_stop_departures.assert_awaited_once()


@pytest.mark.parametrize(
    ("now", "should_skip"),
    [
        (datetime(2026, 8, 3, 2, 0, 0), True),  # inside overnight window
        (datetime(2026, 8, 3, 23, 30, 0), True),  # inside overnight window (late)
        (datetime(2026, 8, 3, 12, 0, 0), False),  # outside window
    ],
)
async def test_overnight_quiet_hours(
    hass: HomeAssistant, config_entry, freezer, now, should_skip
) -> None:
    """An overnight quiet-hours window (23:00-06:00) wraps past midnight."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to(now)
    hass.config_entries.async_update_entry(
        config_entry,
        options={
            CONF_QUIET_HOURS_START: "23:00:00",
            CONF_QUIET_HOURS_END: "06:00:00",
        },
    )
    coordinator = TransitAppDataUpdateCoordinator(hass, config_entry)
    coordinator.client.async_get_stop_departures = AsyncMock(return_value=[])

    await coordinator._async_update_data()

    if should_skip:
        coordinator.client.async_get_stop_departures.assert_not_awaited()
    else:
        coordinator.client.async_get_stop_departures.assert_awaited_once()


async def test_presence_filter_skips_when_nobody_home(
    hass: HomeAssistant, config_entry
) -> None:
    """Polling is skipped when none of the configured presence entities are home."""
    hass.states.async_set("person.someone", "not_home")
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_PRESENCE_ENTITIES: ["person.someone"]}
    )
    coordinator = TransitAppDataUpdateCoordinator(hass, config_entry)
    coordinator.client.async_get_stop_departures = AsyncMock(return_value=[])

    await coordinator._async_update_data()

    coordinator.client.async_get_stop_departures.assert_not_awaited()


async def test_presence_filter_polls_when_someone_home(
    hass: HomeAssistant, config_entry
) -> None:
    """Polling proceeds when at least one configured presence entity is home."""
    hass.states.async_set("person.someone", "home")
    hass.states.async_set("person.someone_else", "not_home")
    hass.config_entries.async_update_entry(
        config_entry,
        options={
            CONF_PRESENCE_ENTITIES: ["person.someone", "person.someone_else"]
        },
    )
    coordinator = TransitAppDataUpdateCoordinator(hass, config_entry)
    coordinator.client.async_get_stop_departures = AsyncMock(return_value=[])

    await coordinator._async_update_data()

    coordinator.client.async_get_stop_departures.assert_awaited_once()


async def test_no_presence_entities_configured_always_polls(
    hass: HomeAssistant, config_entry
) -> None:
    """With the presence filter unset (default), it never blocks polling."""
    coordinator = TransitAppDataUpdateCoordinator(hass, config_entry)
    coordinator.client.async_get_stop_departures = AsyncMock(return_value=[])

    await coordinator._async_update_data()

    coordinator.client.async_get_stop_departures.assert_awaited_once()
