"""The Transit App integration."""
from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import ATTR_CONFIG_ENTRY_ID, DOMAIN, SERVICE_REFRESH
from .coordinator import TransitAppDataUpdateCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]

REFRESH_SERVICE_SCHEMA = vol.Schema({vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Transit App from a config entry."""
    coordinator = TransitAppDataUpdateCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH):
        hass.services.async_register(
            DOMAIN,
            SERVICE_REFRESH,
            _make_refresh_handler(hass),
            schema=REFRESH_SERVICE_SCHEMA,
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_REFRESH)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update (e.g. stop list changed) by refreshing data."""
    coordinator: TransitAppDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    await coordinator.async_request_refresh()


def _make_refresh_handler(hass: HomeAssistant):
    """Build the transit_app.refresh action handler.

    Bypasses the quiet-hours/presence quota-saving gates for one poll -
    useful for on-demand debugging (e.g. confirming the API key and tracked
    stop IDs actually return data) without waiting for the next scheduled
    poll or temporarily disabling those filters.
    """

    async def _handle_refresh(call: ServiceCall) -> None:
        coordinators: dict[str, TransitAppDataUpdateCoordinator] = hass.data.get(DOMAIN, {})
        requested_entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)

        if requested_entry_id is not None:
            coordinator = coordinators.get(requested_entry_id)
            if coordinator is None:
                raise ServiceValidationError(
                    f"'{requested_entry_id}' is not a loaded Transit App config entry"
                )
            targets = [coordinator]
        else:
            targets = list(coordinators.values())

        for coordinator in targets:
            await coordinator.async_refresh_now()

    return _handle_refresh
