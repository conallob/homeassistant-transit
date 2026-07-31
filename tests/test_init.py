"""Tests for Transit App integration setup/unload."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.transit_app.const import (
    ATTR_CONFIG_ENTRY_ID,
    CONF_API_KEY,
    CONF_GLOBAL_STOP_ID,
    CONF_STOP_NAME,
    CONF_STOPS,
    DOMAIN,
    SERVICE_REFRESH,
)


async def test_setup_and_unload_entry(hass: HomeAssistant, config_entry) -> None:
    """The entry loads, registers a coordinator, and cleans up on unload."""
    with patch(
        "custom_components.transit_app.coordinator.TransitAppClient.async_get_stop_departures",
        AsyncMock(return_value=[]),
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    assert config_entry.entry_id in hass.data[DOMAIN]

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.NOT_LOADED
    assert config_entry.entry_id not in hass.data[DOMAIN]


async def test_setup_entry_auth_failure_retries(hass: HomeAssistant, config_entry) -> None:
    """An auth failure on first refresh leaves the entry in a retryable state."""
    from custom_components.transit_app.api import TransitAppAuthError

    with patch(
        "custom_components.transit_app.coordinator.TransitAppClient.async_get_stop_departures",
        AsyncMock(side_effect=TransitAppAuthError("bad key")),
    ):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_registers_refresh_service(hass: HomeAssistant, config_entry) -> None:
    """Setting up an entry registers the transit_app.refresh action."""
    with patch(
        "custom_components.transit_app.coordinator.TransitAppClient.async_get_stop_departures",
        AsyncMock(return_value=[]),
    ):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert hass.services.has_service(DOMAIN, SERVICE_REFRESH)


async def test_unload_removes_refresh_service_when_last_entry(
    hass: HomeAssistant, config_entry
) -> None:
    """Unloading the only entry removes the service too."""
    with patch(
        "custom_components.transit_app.coordinator.TransitAppClient.async_get_stop_departures",
        AsyncMock(return_value=[]),
    ):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        await hass.config_entries.async_unload(config_entry.entry_id)
        await hass.async_block_till_done()

    assert not hass.services.has_service(DOMAIN, SERVICE_REFRESH)


async def test_refresh_service_unknown_entry_id_raises(
    hass: HomeAssistant, config_entry
) -> None:
    """Targeting a config_entry_id that isn't loaded is a validation error."""
    with patch(
        "custom_components.transit_app.coordinator.TransitAppClient.async_get_stop_departures",
        AsyncMock(return_value=[]),
    ):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_REFRESH,
                {ATTR_CONFIG_ENTRY_ID: "not-a-real-entry-id"},
                blocking=True,
            )


async def test_refresh_service_targets_all_entries_when_omitted(
    hass: HomeAssistant, config_entry, freezer
) -> None:
    """Calling the action with no target refreshes every loaded entry."""
    other_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Transit App (second)",
        data={
            CONF_API_KEY: "other-api-key",
            CONF_STOPS: [
                {CONF_GLOBAL_STOP_ID: "GOIE:466", CONF_STOP_NAME: "Oldcourt"}
            ],
        },
        options={},
    )
    other_entry.add_to_hass(hass)

    with patch(
        "custom_components.transit_app.coordinator.TransitAppClient.async_get_stop_departures",
        AsyncMock(return_value=[]),
    ) as mocked_get_departures:
        # Setting up the first entry of a not-yet-set-up domain sets up the
        # *component*, which sets up every entry of that domain in one go -
        # a second explicit async_setup() call for other_entry would raise
        # OperationNotAllowed since it's already loaded by this point.
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        assert other_entry.state is ConfigEntryState.LOADED
        mocked_get_departures.reset_mock()

        # Past the default 5 calls/minute rate limit's 12s floor, so this
        # manual refresh isn't itself blocked by the hard rate-limit guard.
        freezer.tick(15)
        await hass.services.async_call(DOMAIN, SERVICE_REFRESH, {}, blocking=True)

    assert mocked_get_departures.await_count == 2


async def test_refresh_service_targets_specific_entry(
    hass: HomeAssistant, config_entry, freezer
) -> None:
    """Calling the action with config_entry_id only refreshes that entry."""
    other_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Transit App (second)",
        data={
            CONF_API_KEY: "other-api-key",
            CONF_STOPS: [
                {CONF_GLOBAL_STOP_ID: "GOIE:466", CONF_STOP_NAME: "Oldcourt"}
            ],
        },
        options={},
    )
    other_entry.add_to_hass(hass)

    with patch(
        "custom_components.transit_app.coordinator.TransitAppClient.async_get_stop_departures",
        AsyncMock(return_value=[]),
    ) as mocked_get_departures:
        # Setting up the first entry of a not-yet-set-up domain sets up the
        # *component*, which sets up every entry of that domain in one go.
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        assert other_entry.state is ConfigEntryState.LOADED
        mocked_get_departures.reset_mock()

        freezer.tick(15)
        await hass.services.async_call(
            DOMAIN,
            SERVICE_REFRESH,
            {ATTR_CONFIG_ENTRY_ID: config_entry.entry_id},
            blocking=True,
        )

    assert mocked_get_departures.await_count == 1
