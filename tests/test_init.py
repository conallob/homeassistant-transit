"""Tests for Transit App integration setup/unload."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.transit_app.const import DOMAIN


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
