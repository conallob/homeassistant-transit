"""Tests for the Transit App config and options flows."""
from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.transit_app.api import TransitAppApiError, TransitAppAuthError
from custom_components.transit_app.config_flow import _stop_label, _stop_matches_filter
from custom_components.transit_app.const import (
    CONF_API_KEY,
    CONF_GLOBAL_STOP_ID,
    CONF_MONTHLY_QUOTA,
    CONF_PRESENCE_ENTITIES,
    CONF_QUIET_DAYS,
    CONF_RATE_LIMIT_PER_MINUTE,
    CONF_SEARCH_FILTER,
    CONF_STOP_NAME,
    CONF_STOPS,
    DEFAULT_MONTHLY_QUOTA,
    DEFAULT_RATE_LIMIT_PER_MINUTE,
    DOMAIN,
)

_PATCH_NEARBY_STOPS = (
    "custom_components.transit_app.config_flow.TransitAppClient.async_get_nearby_stops"
)


async def _start_user_flow(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_full_user_flow_creates_entry(
    hass: HomeAssistant, nearby_stops_response
) -> None:
    """The happy path: user step -> stops step -> filters step -> entry."""
    result = await _start_user_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    with patch(_PATCH_NEARBY_STOPS, return_value=nearby_stops_response):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_API_KEY: "test-key",
                "latitude": 53.2,
                "longitude": -6.1,
                "radius": 500,
            },
        )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "stops"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_STOPS: ["DUBIE:72440", "DUBIE:72429"]}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "filters"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_API_KEY] == "test-key"
    assert {s[CONF_GLOBAL_STOP_ID] for s in result["data"][CONF_STOPS]} == {
        "DUBIE:72440",
        "DUBIE:72429",
    }
    # Optional filters were left blank, so no stray empty keys should linger,
    # but the quota fields always get their plan-default values.
    assert result["options"] == {
        CONF_PRESENCE_ENTITIES: [],
        CONF_QUIET_DAYS: [],
        CONF_MONTHLY_QUOTA: DEFAULT_MONTHLY_QUOTA,
        CONF_RATE_LIMIT_PER_MINUTE: DEFAULT_RATE_LIMIT_PER_MINUTE,
    }


async def test_invalid_auth_shows_error(hass: HomeAssistant) -> None:
    """A rejected API key surfaces as an error on the user step."""
    result = await _start_user_flow(hass)

    with patch(_PATCH_NEARBY_STOPS, side_effect=TransitAppAuthError("bad key")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_API_KEY: "bad-key",
                "latitude": 53.2,
                "longitude": -6.1,
                "radius": 500,
            },
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "invalid_auth"}


async def test_cannot_connect_shows_error(hass: HomeAssistant) -> None:
    """A connection failure surfaces as an error on the user step."""
    result = await _start_user_flow(hass)

    with patch(_PATCH_NEARBY_STOPS, side_effect=TransitAppApiError("down")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_API_KEY: "key",
                "latitude": 53.2,
                "longitude": -6.1,
                "radius": 500,
            },
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_no_stops_found_shows_error(hass: HomeAssistant) -> None:
    """An empty nearby_stops result surfaces as an error on the user step."""
    result = await _start_user_flow(hass)

    with patch(_PATCH_NEARBY_STOPS, return_value=[]):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_API_KEY: "key",
                "latitude": 53.2,
                "longitude": -6.1,
                "radius": 500,
            },
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "no_stops_found"}


async def test_no_stops_selected_shows_error(
    hass: HomeAssistant, nearby_stops_response
) -> None:
    """Submitting the stops step with nothing selected is rejected."""
    result = await _start_user_flow(hass)
    with patch(_PATCH_NEARBY_STOPS, return_value=nearby_stops_response):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_API_KEY: "key",
                "latitude": 53.2,
                "longitude": -6.1,
                "radius": 500,
            },
        )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_STOPS: []}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "stops"
    assert result["errors"] == {"base": "no_stops_selected"}


async def test_options_flow_search_preserves_previously_tracked_stop(
    hass: HomeAssistant, config_entry, nearby_stops_response
) -> None:
    """Re-searching from a new area keeps already-tracked stops selected.

    config_entry already tracks DUBIE:72440 ("Killarney Road"), which is not
    included in this fixture's stop_departures results but *is* in
    nearby_stops_response - simulating a search from a different area that
    doesn't happen to return the original stop.
    """
    only_new_stop = [nearby_stops_response[1]]  # just "Fairyhill", not Killarney Road

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.MENU

    with patch(_PATCH_NEARBY_STOPS, return_value=only_new_stop):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"next_step_id": "search_stops"},
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"latitude": 53.3, "longitude": -6.2, "radius": 300},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "stops"
    # The previously-tracked stop must still be an offered (and pre-checked)
    # choice, alongside the newly found one.
    stops_selector_config = result["data_schema"].schema[CONF_STOPS].config
    offered_ids = {opt["value"] for opt in stops_selector_config["options"]}
    assert {"DUBIE:72440", "DUBIE:72429"} <= offered_ids

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_STOPS: ["DUBIE:72440", "DUBIE:72429"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert {s[CONF_GLOBAL_STOP_ID] for s in result["data"][CONF_STOPS]} == {
        "DUBIE:72440",
        "DUBIE:72429",
    }


def test_stop_label_disambiguates_same_physical_stop_across_feeds() -> None:
    """Two feeds for one physical stop must not render as identical labels.

    e.g. DUBIE:72440 and EIREANNIE:9486 both being "Killarney Road" #4180 -
    a real, documented Transit App scenario (see
    rest/transit_app_next_bus.yaml in conallob/Home-Assistant-Config).
    """
    dubie = {
        "global_stop_id": "DUBIE:72440",
        "stop_name": "Killarney Road",
        "stop_code": "4180",
        "route_short_names": ["E1", "L12"],
        "distance": 120,
    }
    eireann = {
        "global_stop_id": "EIREANNIE:9486",
        "stop_name": "Killarney Road",
        "stop_code": "4180",
        "route_short_names": ["E1"],
        "distance": 120,
    }
    assert _stop_label(dubie) != _stop_label(eireann)
    assert "[DUBIE]" in _stop_label(dubie)
    assert "[EIREANNIE]" in _stop_label(eireann)


def test_stop_matches_filter_checks_name_code_and_routes() -> None:
    """The filter matches on stop name, stop code, or any route name/number."""
    stop = {
        "stop_name": "Killarney Road",
        "stop_code": "4180",
        "route_short_names": ["E1", "L12"],
    }
    assert _stop_matches_filter(stop, "")
    assert _stop_matches_filter(stop, "killarney")
    assert _stop_matches_filter(stop, "4180")
    assert _stop_matches_filter(stop, "l12")
    assert not _stop_matches_filter(stop, "45a")


async def test_search_filter_narrows_results(
    hass: HomeAssistant, nearby_stops_response
) -> None:
    """A search_filter excludes stops that don't match it."""
    result = await _start_user_flow(hass)

    with patch(_PATCH_NEARBY_STOPS, return_value=nearby_stops_response):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_API_KEY: "test-key",
                "latitude": 53.2,
                "longitude": -6.1,
                "radius": 500,
                CONF_SEARCH_FILTER: "Fairyhill",
            },
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "stops"
    stops_selector_config = result["data_schema"].schema[CONF_STOPS].config
    offered_ids = {opt["value"] for opt in stops_selector_config["options"]}
    assert offered_ids == {"DUBIE:72429"}  # Fairyhill only, not Killarney Road


async def test_search_filter_matching_nothing_shows_specific_error(
    hass: HomeAssistant, nearby_stops_response
) -> None:
    """A filter that excludes every nearby stop gets its own error message."""
    result = await _start_user_flow(hass)

    with patch(_PATCH_NEARBY_STOPS, return_value=nearby_stops_response):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_API_KEY: "test-key",
                "latitude": 53.2,
                "longitude": -6.1,
                "radius": 500,
                CONF_SEARCH_FILTER: "no-such-route-or-stop",
            },
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "no_stops_match_filter"}


async def test_options_flow_search_no_stops_found_shows_error(hass: HomeAssistant) -> None:
    """An empty search result with nothing previously tracked shows an error.

    Uses a fresh entry with no tracked stops - unlike the `config_entry`
    fixture, there's nothing for _merge_previously_tracked_stops to fall
    back to, so an empty nearby_stops response really does leave the
    choices empty.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Transit App",
        data={CONF_API_KEY: "test-api-key", CONF_STOPS: []},
        options={},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "search_stops"}
    )

    with patch(_PATCH_NEARBY_STOPS, return_value=[]):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"latitude": 53.2, "longitude": -6.1, "radius": 500},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "search_stops"
    assert result["errors"] == {"base": "no_stops_found"}


async def test_options_flow_filters_round_trip(
    hass: HomeAssistant, config_entry
) -> None:
    """The filters step saves presence/quiet-day settings into entry options."""
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "filters"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "filters"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_PRESENCE_ENTITIES: ["person.someone"], CONF_QUIET_DAYS: ["sat", "sun"]},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PRESENCE_ENTITIES] == ["person.someone"]
    assert result["data"][CONF_QUIET_DAYS] == ["sat", "sun"]
    # Existing stops must survive editing an unrelated options step.
    assert CONF_STOPS not in result["data"]
