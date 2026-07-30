"""Config flow for the Transit App integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TransitAppApiError, TransitAppAuthError, TransitAppClient
from .const import (
    CONF_API_KEY,
    CONF_GLOBAL_STOP_ID,
    CONF_RADIUS,
    CONF_STOP_NAME,
    CONF_STOPS,
    DEFAULT_RADIUS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _stop_label(stop: dict[str, Any]) -> str:
    name = stop.get("stop_name", stop.get("global_stop_id"))
    code = stop.get("stop_code")
    routes = stop.get("route_short_names") or stop.get("routes")
    parts = [name]
    if code:
        parts.append(f"#{code}")
    if isinstance(routes, list) and routes:
        route_names = ", ".join(str(r) for r in routes[:6])
        parts.append(f"({route_names})")
    distance = stop.get("distance")
    if distance is not None:
        parts.append(f"- {int(distance)}m")
    return " ".join(str(p) for p in parts)


class TransitAppFlowMixin:
    """Shared nearby-stop search logic for the config and options flows."""

    _stops_by_id: dict[str, dict[str, Any]]

    async def _async_search_nearby_stops(
        self, api_key: str, latitude: float, longitude: float, radius: int
    ) -> dict[str, str]:
        session = async_get_clientsession(self.hass)  # type: ignore[attr-defined]
        client = TransitAppClient(session, api_key)
        stops = await client.async_get_nearby_stops(latitude, longitude, radius)
        self._stops_by_id = {stop["global_stop_id"]: stop for stop in stops if stop.get("global_stop_id")}
        return {stop_id: _stop_label(stop) for stop_id, stop in self._stops_by_id.items()}


class TransitAppConfigFlow(ConfigFlow, TransitAppFlowMixin, domain=DOMAIN):
    """Handle a config flow for Transit App."""

    VERSION = 1

    def __init__(self) -> None:
        self._api_key: str | None = None
        self._latitude: float | None = None
        self._longitude: float | None = None
        self._radius: int = DEFAULT_RADIUS
        self._stop_choices: dict[str, str] = {}
        self._stops_by_id: dict[str, dict[str, Any]] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._api_key = user_input[CONF_API_KEY]
            self._latitude = user_input["latitude"]
            self._longitude = user_input["longitude"]
            self._radius = user_input[CONF_RADIUS]
            try:
                self._stop_choices = await self._async_search_nearby_stops(
                    self._api_key, self._latitude, self._longitude, self._radius
                )
            except TransitAppAuthError:
                errors["base"] = "invalid_auth"
            except TransitAppApiError:
                errors["base"] = "cannot_connect"
            else:
                if not self._stop_choices:
                    errors["base"] = "no_stops_found"
                else:
                    return await self.async_step_stops()

        schema = vol.Schema(
            {
                vol.Required(CONF_API_KEY): str,
                vol.Required(
                    "latitude", default=self.hass.config.latitude
                ): vol.Coerce(float),
                vol.Required(
                    "longitude", default=self.hass.config.longitude
                ): vol.Coerce(float),
                vol.Required(CONF_RADIUS, default=DEFAULT_RADIUS): vol.Coerce(int),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_stops(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        errors: dict[str, str] = {}
        if user_input is not None:
            selected = user_input[CONF_STOPS]
            if not selected:
                errors["base"] = "no_stops_selected"
            else:
                stops = [
                    {
                        CONF_GLOBAL_STOP_ID: stop_id,
                        CONF_STOP_NAME: self._stops_by_id[stop_id].get(
                            "stop_name", stop_id
                        ),
                    }
                    for stop_id in selected
                ]
                await self.async_set_unique_id(f"{self._api_key}:{','.join(sorted(selected))}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="Transit App",
                    data={CONF_API_KEY: self._api_key, CONF_STOPS: stops},
                )

        schema = vol.Schema(
            {vol.Required(CONF_STOPS): selector.selector(
                {"select": {"options": [
                    {"value": stop_id, "label": label}
                    for stop_id, label in self._stop_choices.items()
                ], "multiple": True, "mode": "list"}}
            )}
        )
        return self.async_show_form(
            step_id="stops", data_schema=schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return TransitAppOptionsFlow(config_entry)


class TransitAppOptionsFlow(OptionsFlow, TransitAppFlowMixin):
    """Handle options: change search location/radius and pick stops again."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        # `self.config_entry` is provided by the base OptionsFlow class; it
        # must not be reassigned here (recent Home Assistant versions warn
        # or fail if a subclass does so in __init__).
        self._radius: int = DEFAULT_RADIUS
        self._stop_choices: dict[str, str] = {}
        self._stops_by_id: dict[str, dict[str, Any]] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._radius = user_input[CONF_RADIUS]
            try:
                self._stop_choices = await self._async_search_nearby_stops(
                    self.config_entry.data[CONF_API_KEY],
                    user_input["latitude"],
                    user_input["longitude"],
                    self._radius,
                )
            except TransitAppAuthError:
                errors["base"] = "invalid_auth"
            except TransitAppApiError:
                errors["base"] = "cannot_connect"
            else:
                if not self._stop_choices:
                    errors["base"] = "no_stops_found"
                else:
                    return await self.async_step_stops()

        schema = vol.Schema(
            {
                vol.Required(
                    "latitude", default=self.hass.config.latitude
                ): vol.Coerce(float),
                vol.Required(
                    "longitude", default=self.hass.config.longitude
                ): vol.Coerce(float),
                vol.Required(CONF_RADIUS, default=DEFAULT_RADIUS): vol.Coerce(int),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

    async def async_step_stops(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        errors: dict[str, str] = {}
        current = {
            stop[CONF_GLOBAL_STOP_ID]
            for stop in self.config_entry.options.get(
                CONF_STOPS, self.config_entry.data.get(CONF_STOPS, [])
            )
        }
        if user_input is not None:
            selected = user_input[CONF_STOPS]
            if not selected:
                errors["base"] = "no_stops_selected"
            else:
                stops = [
                    {
                        CONF_GLOBAL_STOP_ID: stop_id,
                        CONF_STOP_NAME: self._stops_by_id[stop_id].get(
                            "stop_name", stop_id
                        ),
                    }
                    for stop_id in selected
                ]
                return self.async_create_entry(title="", data={CONF_STOPS: stops})

        schema = vol.Schema(
            {vol.Required(CONF_STOPS, default=list(current & self._stop_choices.keys())): selector.selector(
                {"select": {"options": [
                    {"value": stop_id, "label": label}
                    for stop_id, label in self._stop_choices.items()
                ], "multiple": True, "mode": "list"}}
            )}
        )
        return self.async_show_form(
            step_id="stops", data_schema=schema, errors=errors
        )
