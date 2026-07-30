"""Shared fixtures for Transit App integration tests."""
from __future__ import annotations

from typing import Any

import pytest

from custom_components.transit_app.const import (
    CONF_API_KEY,
    CONF_GLOBAL_STOP_ID,
    CONF_STOP_NAME,
    CONF_STOPS,
    DOMAIN,
)

from pytest_homeassistant_custom_component.common import MockConfigEntry

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(scope="session", autouse=True)
def _warm_up_pycares_shutdown_thread() -> None:
    """Start pycares' lazily-created background thread before any test runs.

    aiohttp (via aiodns/pycares, both Home Assistant dependencies) spawns a
    daemon thread the first time a real ClientSession/connector is torn down,
    to safely destroy its DNS resolver channel. If that happens to be during
    the *first* test that calls async_get_clientsession(), pytest-home-
    assistant-custom-component's leaked-thread check flags it as new and
    fails that test - even though nothing in this integration is doing
    anything wrong. Starting it once here, before any test's thread
    before/after snapshot is taken, keeps it out of every test's diff.
    """
    try:
        from pycares import _shutdown_manager

        _shutdown_manager.start()
    except ImportError:
        pass


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Make custom_components/transit_app loadable in every test."""


@pytest.fixture(name="nearby_stops_response")
def nearby_stops_response_fixture() -> list[dict[str, Any]]:
    """Sample Transit App nearby_stops response."""
    return [
        {
            "global_stop_id": "DUBIE:72440",
            "stop_name": "Killarney Road",
            "stop_code": "4180",
            "route_short_names": ["E1", "L12"],
            "distance": 120,
        },
        {
            "global_stop_id": "DUBIE:72429",
            "stop_name": "Fairyhill",
            "stop_code": "8270",
            "route_short_names": ["E1"],
            "distance": 340,
        },
    ]


@pytest.fixture(name="stop_departures_response")
def stop_departures_response_fixture() -> list[dict[str, Any]]:
    """Sample Transit App stop_departures response's route_departures list."""
    return [
        {
            "global_stop_id": "DUBIE:72440",
            "route_id": "E1",
            "route_short_name": "E1",
            "route_long_name": "Example Route 1",
            "itineraries": [
                {
                    "direction_headsign": "City Centre",
                    "schedule_items": [
                        {"departure_time": 2000000000, "is_cancelled": False},
                        {"departure_time": 2000000600, "is_cancelled": False},
                        {"departure_time": 2000000300, "is_cancelled": True},
                    ],
                }
            ],
        }
    ]


@pytest.fixture(name="config_entry")
def config_entry_fixture(hass: Any) -> MockConfigEntry:
    """A MockConfigEntry tracking a single stop, added to hass."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Transit App",
        data={
            CONF_API_KEY: "test-api-key",
            CONF_STOPS: [
                {CONF_GLOBAL_STOP_ID: "DUBIE:72440", CONF_STOP_NAME: "Killarney Road"}
            ],
        },
        options={},
    )
    entry.add_to_hass(hass)
    return entry
