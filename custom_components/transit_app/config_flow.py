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
    CONF_PRESENCE_ENTITIES,
    CONF_QUIET_DAYS,
    CONF_QUIET_HOURS_END,
    CONF_QUIET_HOURS_START,
    CONF_RADIUS,
    CONF_STOP_NAME,
    CONF_STOPS,
    DEFAULT_RADIUS,
    DOMAIN,
    WEEKDAYS,
)

_LOGGER = logging.getLogger(__name__)

_WEEKDAY_LABELS = {
    "mon": "Monday",
    "tue": "Tuesday",
    "wed": "Wednesday",
    "thu": "Thursday",
    "fri": "Friday",
    "sat": "Saturday",
    "sun": "Sunday",
}


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


def _time_field(key: str, current: dict[str, Any]) -> vol.Marker:
    """An optional time field that only gets a default when one is already set.

    A TimeSelector rejects "" as an invalid time, so - unlike the list-typed
    fields below, where an empty list is a perfectly valid "default" - this
    field must be left without a default entirely when unset, so voluptuous
    doesn't try to validate an empty placeholder against the time selector.
    """
    if current.get(key):
        return vol.Optional(key, default=current[key])
    return vol.Optional(key)


def _filters_schema(current: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(
                CONF_PRESENCE_ENTITIES, default=current.get(CONF_PRESENCE_ENTITIES, [])
            ): selector.selector(
                {
                    "entity": {
                        "multiple": True,
                        "domain": ["person", "device_tracker"],
                    }
                }
            ),
            _time_field(CONF_QUIET_HOURS_START, current): selector.selector({"time": {}}),
            _time_field(CONF_QUIET_HOURS_END, current): selector.selector({"time": {}}),
            vol.Optional(
                CONF_QUIET_DAYS, default=current.get(CONF_QUIET_DAYS, [])
            ): selector.selector(
                {
                    "select": {
                        "options": [
                            {"value": day, "label": _WEEKDAY_LABELS[day]}
                            for day in WEEKDAYS
                        ],
                        "multiple": True,
                        "mode": "list",
                    }
                }
            ),
        }
    )


def _clean_filters(user_input: dict[str, Any]) -> dict[str, Any]:
    """Drop empty optional values so unset filters don't leave stray keys."""
    filters = dict(user_input)
    if not filters.get(CONF_QUIET_HOURS_START):
        filters.pop(CONF_QUIET_HOURS_START, None)
    if not filters.get(CONF_QUIET_HOURS_END):
        filters.pop(CONF_QUIET_HOURS_END, None)
    return filters


class TransitAppFlowMixin:
    """Shared nearby-stop search logic for the config and options flows."""

    _stops_by_id: dict[str, dict[str, Any]]
    _stop_choices: dict[str, str]

    async def _async_search_nearby_stops(
        self, api_key: str, latitude: float, longitude: float, radius: int
    ) -> dict[str, str]:
        session = async_get_clientsession(self.hass)  # type: ignore[attr-defined]
        client = TransitAppClient(session, api_key)
        stops = await client.async_get_nearby_stops(latitude, longitude, radius)
        self._stops_by_id = {stop["global_stop_id"]: stop for stop in stops if stop.get("global_stop_id")}
        return {stop_id: _stop_label(stop) for stop_id, stop in self._stops_by_id.items()}

    def _merge_previously_tracked_stops(self, existing_stops: list[dict[str, Any]]) -> None:
        """Keep already-tracked stops selectable even if a new search doesn't find them.

        A re-search (different location/radius, or the same stop temporarily
        missing from a nearby_stops response) must not silently drop stops a
        user is already tracking - otherwise growing a single config entry to
        cover multiple areas (and so keep them all in one batched API
        request) would lose earlier picks every time you search again.
        """
        for stop in existing_stops:
            stop_id = stop[CONF_GLOBAL_STOP_ID]
            if stop_id in self._stops_by_id:
                continue
            name = stop.get(CONF_STOP_NAME, stop_id)
            self._stops_by_id[stop_id] = {"global_stop_id": stop_id, "stop_name": name}
            self._stop_choices[stop_id] = f"{name} (previously added)"


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
        self._selected_stops: list[dict[str, Any]] = []

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
                self._selected_stops = [
                    {
                        CONF_GLOBAL_STOP_ID: stop_id,
                        CONF_STOP_NAME: self._stops_by_id[stop_id].get(
                            "stop_name", stop_id
                        ),
                    }
                    for stop_id in selected
                ]
                return await self.async_step_filters()

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

    async def async_step_filters(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        if user_input is not None:
            selected_ids = sorted(s[CONF_GLOBAL_STOP_ID] for s in self._selected_stops)
            await self.async_set_unique_id(f"{self._api_key}:{','.join(selected_ids)}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title="Transit App",
                data={CONF_API_KEY: self._api_key, CONF_STOPS: self._selected_stops},
                options=_clean_filters(user_input),
            )

        return self.async_show_form(
            step_id="filters", data_schema=_filters_schema({})
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return TransitAppOptionsFlow(config_entry)


class TransitAppOptionsFlow(OptionsFlow, TransitAppFlowMixin):
    """Handle options: change/add tracked stops, or edit quota-saving filters."""

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
        return self.async_show_menu(
            step_id="init", menu_options=["search_stops", "filters"]
        )

    async def async_step_search_stops(
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
                existing_stops = self.config_entry.options.get(
                    CONF_STOPS, self.config_entry.data.get(CONF_STOPS, [])
                )
                self._merge_previously_tracked_stops(existing_stops)
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
        return self.async_show_form(
            step_id="search_stops", data_schema=schema, errors=errors
        )

    async def async_step_stops(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        errors: dict[str, str] = {}
        # All currently-tracked stops were merged into self._stop_choices by
        # async_step_search_stops, so this is safe to pre-check in full -
        # nothing gets silently dropped unless the user unchecks it.
        current = [
            stop[CONF_GLOBAL_STOP_ID]
            for stop in self.config_entry.options.get(
                CONF_STOPS, self.config_entry.data.get(CONF_STOPS, [])
            )
        ]
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
                return self.async_create_entry(
                    title="", data={**self.config_entry.options, CONF_STOPS: stops}
                )

        schema = vol.Schema(
            {vol.Required(CONF_STOPS, default=current): selector.selector(
                {"select": {"options": [
                    {"value": stop_id, "label": label}
                    for stop_id, label in self._stop_choices.items()
                ], "multiple": True, "mode": "list"}}
            )}
        )
        return self.async_show_form(
            step_id="stops", data_schema=schema, errors=errors
        )

    async def async_step_filters(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={**self.config_entry.options, **_clean_filters(user_input)},
            )

        return self.async_show_form(
            step_id="filters", data_schema=_filters_schema(self.config_entry.options)
        )
