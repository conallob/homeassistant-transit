"""Tests for the Transit App API client.

These mock the aiohttp session directly (rather than using a library like
aioresponses that intercepts real request/response internals), since
aioresponses' last release (0.7.9) does not track aiohttp's ClientResponse
constructor signature and breaks on newer aiohttp/Python versions - see the
regression this fixed for details.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import MagicMock

import aiohttp
import pytest

from custom_components.transit_app.api import (
    TransitAppApiError,
    TransitAppAuthError,
    TransitAppClient,
)


class _FakeResponse:
    def __init__(self, status: int, json_data: Any = None, text_data: str = "") -> None:
        self.status = status
        self._json_data = json_data
        self._text_data = text_data

    async def json(self) -> Any:
        return self._json_data

    async def text(self) -> str:
        return self._text_data


def _session_returning(status: int, json_data: Any = None, text_data: str = "") -> MagicMock:
    """A mock aiohttp session whose .get() yields a canned response."""

    @asynccontextmanager
    async def _get(*args: Any, **kwargs: Any):
        yield _FakeResponse(status, json_data, text_data)

    session = MagicMock()
    session.get = MagicMock(side_effect=_get)
    return session


def _session_raising(exc: Exception) -> MagicMock:
    """A mock aiohttp session whose .get() raises before a response exists."""
    session = MagicMock()
    session.get = MagicMock(side_effect=exc)
    return session


async def test_nearby_stops_list_response(nearby_stops_response) -> None:
    """A bare-list nearby_stops response is returned as-is."""
    client = TransitAppClient(_session_returning(200, json_data=nearby_stops_response), "key")
    stops = await client.async_get_nearby_stops(53.2, -6.1, 500)
    assert stops == nearby_stops_response


async def test_nearby_stops_dict_wrapped_response(nearby_stops_response) -> None:
    """A {"stops": [...]} nearby_stops response is unwrapped."""
    client = TransitAppClient(
        _session_returning(200, json_data={"stops": nearby_stops_response}), "key"
    )
    stops = await client.async_get_nearby_stops(53.2, -6.1, 500)
    assert stops == nearby_stops_response


async def test_stop_departures_returns_route_departures(stop_departures_response) -> None:
    """stop_departures returns the route_departures list from the payload."""
    client = TransitAppClient(
        _session_returning(200, json_data={"route_departures": stop_departures_response}),
        "key",
    )
    departures = await client.async_get_stop_departures(["DUBIE:72440"])
    assert departures == stop_departures_response


async def test_auth_error_on_401() -> None:
    """A 401 response raises TransitAppAuthError, not the generic error."""
    client = TransitAppClient(_session_returning(401), "bad-key")
    with pytest.raises(TransitAppAuthError):
        await client.async_get_nearby_stops(53.2, -6.1, 500)


async def test_auth_error_on_403() -> None:
    """A 403 response also raises TransitAppAuthError."""
    client = TransitAppClient(_session_returning(403), "bad-key")
    with pytest.raises(TransitAppAuthError):
        await client.async_get_nearby_stops(53.2, -6.1, 500)


async def test_generic_error_on_500() -> None:
    """A 500 response raises the generic TransitAppApiError."""
    client = TransitAppClient(_session_returning(500, text_data="boom"), "key")
    with pytest.raises(TransitAppApiError):
        await client.async_get_nearby_stops(53.2, -6.1, 500)


async def test_connection_error_wrapped() -> None:
    """A low-level connection failure is wrapped in TransitAppApiError."""
    client = TransitAppClient(
        _session_raising(aiohttp.ClientConnectionError("no route to host")), "key"
    )
    with pytest.raises(TransitAppApiError):
        await client.async_get_nearby_stops(53.2, -6.1, 500)
