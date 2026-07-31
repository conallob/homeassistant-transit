"""Tests for TransitAppDataUpdateCoordinator."""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
import pytest

from custom_components.transit_app.api import TransitAppApiError, TransitAppAuthError
from custom_components.transit_app.const import (
    CONF_GLOBAL_STOP_ID,
    CONF_MONTHLY_QUOTA,
    CONF_PRESENCE_ENTITIES,
    CONF_QUIET_DAYS,
    CONF_QUIET_HOURS_END,
    CONF_QUIET_HOURS_START,
    CONF_RATE_LIMIT_PER_MINUTE,
    CONF_STOP_NAME,
    CONF_STOPS,
    DEFAULT_MONTHLY_QUOTA,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_RATE_LIMIT_PER_MINUTE,
    MAX_ADAPTIVE_INTERVAL,
    MAX_GLOBAL_STOP_IDS_PER_REQUEST,
    MIN_ADAPTIVE_INTERVAL,
    QUOTA_EXHAUSTED_RECHECK_INTERVAL,
    QUOTA_SAFETY_RESERVE,
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


async def test_refresh_now_bypasses_presence_filter(
    hass: HomeAssistant, config_entry
) -> None:
    """async_refresh_now() polls even when the presence filter would skip it."""
    hass.states.async_set("person.someone", "not_home")
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_PRESENCE_ENTITIES: ["person.someone"]}
    )
    coordinator = TransitAppDataUpdateCoordinator(hass, config_entry)
    coordinator.client.async_get_stop_departures = AsyncMock(return_value=[])

    await coordinator.async_refresh_now()
    await hass.async_block_till_done()

    coordinator.client.async_get_stop_departures.assert_awaited_once()


async def test_refresh_now_bypasses_quiet_hours(
    hass: HomeAssistant, config_entry, freezer
) -> None:
    """async_refresh_now() polls even during configured quiet hours."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to(datetime(2026, 8, 3, 2, 0, 0))  # inside an overnight window
    hass.config_entries.async_update_entry(
        config_entry,
        options={
            CONF_QUIET_HOURS_START: "23:00:00",
            CONF_QUIET_HOURS_END: "06:00:00",
        },
    )
    coordinator = TransitAppDataUpdateCoordinator(hass, config_entry)
    coordinator.client.async_get_stop_departures = AsyncMock(return_value=[])

    await coordinator.async_refresh_now()
    await hass.async_block_till_done()

    coordinator.client.async_get_stop_departures.assert_awaited_once()


async def test_refresh_now_bypass_does_not_persist_to_next_poll(
    hass: HomeAssistant, config_entry, freezer
) -> None:
    """The bypass only applies to the single forced poll, not future ones.

    Advances past the rate-limit floor before the second call so that call
    is genuinely blocked by the (still-active) presence gate, not by the
    hard rate limit - which would make this test pass for the wrong reason.
    """
    hass.states.async_set("person.someone", "not_home")
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_PRESENCE_ENTITIES: ["person.someone"]}
    )
    coordinator = TransitAppDataUpdateCoordinator(hass, config_entry)
    coordinator.client.async_get_stop_departures = AsyncMock(return_value=[])

    await coordinator.async_refresh_now()
    await hass.async_block_till_done()
    coordinator.client.async_get_stop_departures.assert_awaited_once()

    freezer.tick(15)
    await coordinator._async_update_data()
    coordinator.client.async_get_stop_departures.assert_awaited_once()  # still 1, not 2


async def test_unmatched_stop_id_is_ignored_not_dropped_silently(
    hass: HomeAssistant, config_entry
) -> None:
    """A route for a global_stop_id we didn't ask about doesn't blow up the poll."""
    coordinator = TransitAppDataUpdateCoordinator(hass, config_entry)
    coordinator.client.async_get_stop_departures = AsyncMock(
        return_value=[{"global_stop_id": "SOMETHING:UNEXPECTED", "itineraries": []}]
    )

    data = await coordinator._async_update_data()

    assert data == {"DUBIE:72440": []}


# --- Adaptive quota pacing -------------------------------------------------


async def test_rate_limit_blocks_a_second_call_too_soon(
    hass: HomeAssistant, config_entry, freezer
) -> None:
    """A poll attempted before the rate-limit floor has elapsed is skipped."""
    coordinator = TransitAppDataUpdateCoordinator(hass, config_entry)
    coordinator.client.async_get_stop_departures = AsyncMock(return_value=[])

    await coordinator._async_update_data()
    coordinator.client.async_get_stop_departures.assert_awaited_once()

    freezer.tick(1)  # well under the default 5 calls/minute -> 12s floor
    await coordinator._async_update_data()
    coordinator.client.async_get_stop_departures.assert_awaited_once()  # still 1


async def test_rate_limit_allows_a_call_after_the_floor_elapses(
    hass: HomeAssistant, config_entry, freezer
) -> None:
    """A poll after the rate-limit floor has elapsed goes through as normal."""
    coordinator = TransitAppDataUpdateCoordinator(hass, config_entry)
    coordinator.client.async_get_stop_departures = AsyncMock(return_value=[])

    await coordinator._async_update_data()
    freezer.tick(15)
    await coordinator._async_update_data()

    assert coordinator.client.async_get_stop_departures.await_count == 2


async def test_rate_limit_applies_even_to_a_forced_refresh(
    hass: HomeAssistant, config_entry, freezer
) -> None:
    """Unlike the soft quiet-hours/presence gates, the rate limit isn't bypassable."""
    coordinator = TransitAppDataUpdateCoordinator(hass, config_entry)
    coordinator.client.async_get_stop_departures = AsyncMock(return_value=[])

    await coordinator._async_update_data()
    coordinator.client.async_get_stop_departures.assert_awaited_once()

    freezer.tick(1)
    await coordinator.async_refresh_now()
    await hass.async_block_till_done()
    coordinator.client.async_get_stop_departures.assert_awaited_once()  # still 1


async def test_configurable_rate_limit(hass: HomeAssistant, config_entry, freezer) -> None:
    """A configured rate_limit_per_minute changes the required call spacing."""
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_RATE_LIMIT_PER_MINUTE: 60}  # 1 call/second
    )
    coordinator = TransitAppDataUpdateCoordinator(hass, config_entry)
    coordinator.client.async_get_stop_departures = AsyncMock(return_value=[])

    await coordinator._async_update_data()
    freezer.tick(1.5)
    await coordinator._async_update_data()

    assert coordinator.client.async_get_stop_departures.await_count == 2


async def test_quota_exhausted_skips_polling(hass: HomeAssistant, config_entry) -> None:
    """Once the monthly quota is used up, polling is skipped until it rolls over."""
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_MONTHLY_QUOTA: QUOTA_SAFETY_RESERVE}
    )
    coordinator = TransitAppDataUpdateCoordinator(hass, config_entry)
    coordinator.client.async_get_stop_departures = AsyncMock(return_value=[])
    coordinator.data = {"DUBIE:72440": ["stale-but-previous-data"]}

    data = await coordinator._async_update_data()

    coordinator.client.async_get_stop_departures.assert_not_awaited()
    assert data == coordinator.data
    assert coordinator.update_interval.total_seconds() == QUOTA_EXHAUSTED_RECHECK_INTERVAL


async def test_quota_usage_persists_across_coordinator_restarts(
    hass: HomeAssistant, config_entry
) -> None:
    """Calls used this month survive a Home Assistant restart (new coordinator)."""
    first = TransitAppDataUpdateCoordinator(hass, config_entry)
    first.client.async_get_stop_departures = AsyncMock(return_value=[])
    await first._async_update_data()
    assert first._calls_used == 1

    second = TransitAppDataUpdateCoordinator(hass, config_entry)
    await second._async_load_quota_state()

    assert second._calls_used == 1
    assert second._quota_period == first._quota_period


async def test_calls_remaining_reserves_safety_margin(
    hass: HomeAssistant, config_entry
) -> None:
    """The last QUOTA_SAFETY_RESERVE calls of the quota are never counted as usable."""
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_MONTHLY_QUOTA: 100}
    )
    coordinator = TransitAppDataUpdateCoordinator(hass, config_entry)
    await coordinator._async_load_quota_state()

    now = dt_util.now()
    assert coordinator._calls_remaining(now) == 100 - QUOTA_SAFETY_RESERVE


async def test_configurable_monthly_quota_default(
    hass: HomeAssistant, config_entry
) -> None:
    """monthly_quota falls back to the free-tier default when unset."""
    coordinator = TransitAppDataUpdateCoordinator(hass, config_entry)
    assert coordinator.monthly_quota == DEFAULT_MONTHLY_QUOTA
    assert coordinator.rate_limit_per_minute == DEFAULT_RATE_LIMIT_PER_MINUTE


async def test_adaptive_interval_speeds_up_with_quiet_hours_configured(
    hass: HomeAssistant, config_entry, freezer
) -> None:
    """Quiet-hours savings reinvest into a *shorter* interval, not a flat average.

    With half of each day excluded as quiet hours, the same remaining quota
    has to be spent over roughly half the wall-clock time, so the adaptive
    interval should be roughly half as long as with no quiet hours at all.
    """
    freezer.move_to(datetime(2026, 8, 3, 12, 0, 0))
    now = dt_util.now()

    coordinator = TransitAppDataUpdateCoordinator(hass, config_entry)
    baseline_interval = coordinator._compute_adaptive_interval(now, calls_per_poll=1)

    hass.config_entries.async_update_entry(
        config_entry,
        options={
            CONF_QUIET_HOURS_START: "18:00:00",
            CONF_QUIET_HOURS_END: "06:00:00",  # exactly half the day
        },
    )
    quiet_hours_interval = coordinator._compute_adaptive_interval(now, calls_per_poll=1)

    assert quiet_hours_interval < baseline_interval
    assert quiet_hours_interval == pytest.approx(baseline_interval / 2, rel=0.01)


async def test_adaptive_interval_bounds(hass: HomeAssistant, config_entry) -> None:
    """The computed interval is clamped between MIN_ and MAX_ADAPTIVE_INTERVAL."""
    now = dt_util.now()
    coordinator = TransitAppDataUpdateCoordinator(hass, config_entry)

    # An enormous quota relative to remaining time would compute a tiny
    # interval, but it's still floored at MIN_ADAPTIVE_INTERVAL.
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_MONTHLY_QUOTA: 10_000_000}
    )
    assert coordinator._compute_adaptive_interval(now, calls_per_poll=1) == MIN_ADAPTIVE_INTERVAL

    # A tiny quota relative to remaining time would compute a huge interval,
    # but it's still capped at MAX_ADAPTIVE_INTERVAL.
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_MONTHLY_QUOTA: QUOTA_SAFETY_RESERVE + 1}
    )
    assert coordinator._compute_adaptive_interval(now, calls_per_poll=1) == MAX_ADAPTIVE_INTERVAL


async def test_successful_poll_updates_the_adaptive_interval(
    hass: HomeAssistant, config_entry
) -> None:
    """A real poll recomputes update_interval rather than leaving it fixed."""
    coordinator = TransitAppDataUpdateCoordinator(hass, config_entry)
    coordinator.client.async_get_stop_departures = AsyncMock(return_value=[])

    await coordinator._async_update_data()

    assert MIN_ADAPTIVE_INTERVAL <= coordinator.update_interval.total_seconds() <= MAX_ADAPTIVE_INTERVAL


async def test_soft_skip_resets_interval_to_default_for_prompt_rechecks(
    hass: HomeAssistant, config_entry, freezer
) -> None:
    """After a soft skip, update_interval goes back to the short baseline.

    Otherwise a long adaptive interval left over from an earlier active
    period would delay noticing that quiet hours ended or someone got home.
    """
    coordinator = TransitAppDataUpdateCoordinator(hass, config_entry)
    coordinator.client.async_get_stop_departures = AsyncMock(return_value=[])
    coordinator.update_interval = timedelta(seconds=MAX_ADAPTIVE_INTERVAL)

    hass.states.async_set("person.someone", "not_home")
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_PRESENCE_ENTITIES: ["person.someone"]}
    )

    await coordinator._async_update_data()

    assert coordinator.update_interval == timedelta(seconds=DEFAULT_SCAN_INTERVAL)
