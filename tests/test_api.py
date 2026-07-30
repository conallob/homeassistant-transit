"""Tests for the Transit App API client."""
from __future__ import annotations

import aiohttp
from aioresponses import aioresponses
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest

from custom_components.transit_app.api import (
    TransitAppApiError,
    TransitAppAuthError,
    TransitAppClient,
)
from custom_components.transit_app.const import (
    NEARBY_STOPS_ENDPOINT,
    STOP_DEPARTURES_ENDPOINT,
)


async def test_nearby_stops_list_response(hass: HomeAssistant, nearby_stops_response) -> None:
    """A bare-list nearby_stops response is returned as-is."""
    client = TransitAppClient(async_get_clientsession(hass), "key")
    with aioresponses() as mocked:
        mocked.get(
            f"{NEARBY_STOPS_ENDPOINT}?lat=53.2&lon=-6.1&max_distance=500",
            payload=nearby_stops_response,
        )
        stops = await client.async_get_nearby_stops(53.2, -6.1, 500)
    assert stops == nearby_stops_response


async def test_nearby_stops_dict_wrapped_response(
    hass: HomeAssistant, nearby_stops_response
) -> None:
    """A {"stops": [...]} nearby_stops response is unwrapped."""
    client = TransitAppClient(async_get_clientsession(hass), "key")
    with aioresponses() as mocked:
        mocked.get(
            f"{NEARBY_STOPS_ENDPOINT}?lat=53.2&lon=-6.1&max_distance=500",
            payload={"stops": nearby_stops_response},
        )
        stops = await client.async_get_nearby_stops(53.2, -6.1, 500)
    assert stops == nearby_stops_response


async def test_stop_departures_returns_route_departures(
    hass: HomeAssistant, stop_departures_response
) -> None:
    """stop_departures returns the route_departures list from the payload."""
    client = TransitAppClient(async_get_clientsession(hass), "key")
    with aioresponses() as mocked:
        mocked.get(
            f"{STOP_DEPARTURES_ENDPOINT}?global_stop_ids=DUBIE%3A72440&remove_cancelled=true",
            payload={"route_departures": stop_departures_response},
        )
        departures = await client.async_get_stop_departures(["DUBIE:72440"])
    assert departures == stop_departures_response


async def test_auth_error_on_401(hass: HomeAssistant) -> None:
    """A 401 response raises TransitAppAuthError, not the generic error."""
    client = TransitAppClient(async_get_clientsession(hass), "bad-key")
    with aioresponses() as mocked:
        mocked.get(
            f"{NEARBY_STOPS_ENDPOINT}?lat=53.2&lon=-6.1&max_distance=500",
            status=401,
        )
        with pytest.raises(TransitAppAuthError):
            await client.async_get_nearby_stops(53.2, -6.1, 500)


async def test_auth_error_on_403(hass: HomeAssistant) -> None:
    """A 403 response also raises TransitAppAuthError."""
    client = TransitAppClient(async_get_clientsession(hass), "bad-key")
    with aioresponses() as mocked:
        mocked.get(
            f"{NEARBY_STOPS_ENDPOINT}?lat=53.2&lon=-6.1&max_distance=500",
            status=403,
        )
        with pytest.raises(TransitAppAuthError):
            await client.async_get_nearby_stops(53.2, -6.1, 500)


async def test_generic_error_on_500(hass: HomeAssistant) -> None:
    """A 500 response raises the generic TransitAppApiError."""
    client = TransitAppClient(async_get_clientsession(hass), "key")
    with aioresponses() as mocked:
        mocked.get(
            f"{NEARBY_STOPS_ENDPOINT}?lat=53.2&lon=-6.1&max_distance=500",
            status=500,
            body="boom",
        )
        with pytest.raises(TransitAppApiError):
            await client.async_get_nearby_stops(53.2, -6.1, 500)


async def test_connection_error_wrapped(hass: HomeAssistant) -> None:
    """A low-level connection failure is wrapped in TransitAppApiError."""
    client = TransitAppClient(async_get_clientsession(hass), "key")
    with aioresponses() as mocked:
        mocked.get(
            f"{NEARBY_STOPS_ENDPOINT}?lat=53.2&lon=-6.1&max_distance=500",
            exception=aiohttp.ClientConnectionError("no route to host"),
        )
        with pytest.raises(TransitAppApiError):
            await client.async_get_nearby_stops(53.2, -6.1, 500)
