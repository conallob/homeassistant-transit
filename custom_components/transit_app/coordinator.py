"""DataUpdateCoordinator for the Transit App integration."""
from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import TransitAppApiError, TransitAppAuthError, TransitAppClient
from .const import (
    CONF_API_KEY,
    CONF_GLOBAL_STOP_ID,
    CONF_MONTHLY_QUOTA,
    CONF_PRESENCE_ENTITIES,
    CONF_QUIET_DAYS,
    CONF_QUIET_HOURS_END,
    CONF_QUIET_HOURS_START,
    CONF_RATE_LIMIT_PER_MINUTE,
    CONF_STOPS,
    DEFAULT_MONTHLY_QUOTA,
    DEFAULT_RATE_LIMIT_PER_MINUTE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_ADAPTIVE_INTERVAL,
    MAX_GLOBAL_STOP_IDS_PER_REQUEST,
    MIN_ADAPTIVE_INTERVAL,
    QUOTA_EXHAUSTED_RECHECK_INTERVAL,
    QUOTA_SAFETY_RESERVE,
    QUOTA_STORAGE_VERSION,
    STATE_HOME,
    WEEKDAYS,
)

_LOGGER = logging.getLogger(__name__)


def _seconds_until_next_month(now: datetime) -> float:
    if now.month == 12:
        next_month_start = now.replace(
            year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0
        )
    else:
        next_month_start = now.replace(
            month=now.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0
        )
    return (next_month_start - now).total_seconds()


def _quiet_hours_seconds_per_day(start: str | None, end: str | None) -> float:
    """Length of a configured daily quiet-hours window, handling overnight wrap."""
    if not start or not end:
        return 0.0
    start_time = dt_util.parse_time(start)
    end_time = dt_util.parse_time(end)
    if start_time is None or end_time is None or start_time == end_time:
        return 0.0
    start_seconds = start_time.hour * 3600 + start_time.minute * 60 + start_time.second
    end_seconds = end_time.hour * 3600 + end_time.minute * 60 + end_time.second
    if start_time < end_time:
        return end_seconds - start_seconds
    return (86400 - start_seconds) + end_seconds


def _active_fraction(
    quiet_days: list[str], quiet_hours_start: str | None, quiet_hours_end: str | None
) -> float:
    """Estimate the fraction of a typical week polling is actually active.

    Presence isn't modeled here - unlike quiet days/hours it can't be
    predicted in advance. Instead, the adaptive interval simply recomputes
    fresh on every poll: an unpredictable nobody-home skip just leaves that
    call unspent, which shows up as extra remaining quota next time this
    runs and speeds up subsequent active-period polling to compensate.
    """
    quiet_days_fraction = len(set(quiet_days)) / 7
    quiet_hours_fraction = _quiet_hours_seconds_per_day(quiet_hours_start, quiet_hours_end) / 86400
    active_fraction = (1 - quiet_days_fraction) * (1 - quiet_hours_fraction)
    return max(active_fraction, 0.01)  # keep a small floor - never divide by zero


class TransitAppDataUpdateCoordinator(DataUpdateCoordinator[dict[str, list[dict[str, Any]]]]):
    """Poll stop_departures for every configured stop and index by global_stop_id.

    To make effective use of a limited Transit App API quota, this coordinator:
    - batches all of a config entry's stops into as few stop_departures
      requests as possible (splitting only if the stop count exceeds
      MAX_GLOBAL_STOP_IDS_PER_REQUEST)
    - skips polling entirely outside a configured presence filter (no
      tracked person/device_tracker is home) or during configured quiet
      hours/days, reusing the last known data instead
    - adapts its polling interval to spend the *rest* of the monthly quota
      across the *rest* of the estimated-active time in the current billing
      month, so quota saved by quiet hours/days and absences buys tighter
      polling the rest of the time instead of just idling unused
    - always respects two hard limits from Transit App itself - a
      calls-per-minute rate limit and the monthly quota - even when a
      manual transit_app.refresh bypasses the softer quiet-hours/presence
      gates above
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        self.client = TransitAppClient(
            async_get_clientsession(hass), entry.data[CONF_API_KEY]
        )
        self._force_next_update = False
        self._store: Store = Store(
            hass, QUOTA_STORAGE_VERSION, f"{DOMAIN}_{entry.entry_id}_quota"
        )
        self._quota_loaded = False
        self._quota_period: str | None = None
        self._calls_used = 0
        self._last_call_at: datetime | None = None
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )

    async def async_refresh_now(self) -> None:
        """Force an immediate poll, bypassing quiet-hours/presence gates.

        Used by the transit_app.refresh action for on-demand debugging -
        e.g. confirming the API/credentials/stop IDs actually work without
        waiting for the next scheduled poll or temporarily changing quota
        filters. Uses async_refresh() rather than async_request_refresh() -
        the latter is debounced (by design, for coalescing rapid option-
        change triggers), which would make a manual "do it now" action wait
        out that debounce window instead of running immediately.

        This still respects the hard rate-limit/monthly-quota checks in
        _async_update_data - those are facts about the Transit App account,
        not a soft preference this action is meant to override.
        """
        self._force_next_update = True
        await self.async_refresh()

    @property
    def global_stop_ids(self) -> list[str]:
        stops = self.entry.options.get(CONF_STOPS, self.entry.data.get(CONF_STOPS, []))
        return [stop[CONF_GLOBAL_STOP_ID] for stop in stops]

    @property
    def monthly_quota(self) -> int:
        return self.entry.options.get(CONF_MONTHLY_QUOTA, DEFAULT_MONTHLY_QUOTA)

    @property
    def rate_limit_per_minute(self) -> int:
        return self.entry.options.get(CONF_RATE_LIMIT_PER_MINUTE, DEFAULT_RATE_LIMIT_PER_MINUTE)

    @property
    def min_seconds_between_calls(self) -> float:
        return 60.0 / max(self.rate_limit_per_minute, 1)

    def _request_chunks(self, global_stop_ids: list[str]) -> list[list[str]]:
        return [
            global_stop_ids[i : i + MAX_GLOBAL_STOP_IDS_PER_REQUEST]
            for i in range(0, len(global_stop_ids), MAX_GLOBAL_STOP_IDS_PER_REQUEST)
        ]

    @staticmethod
    def _period_key(now: datetime) -> str:
        return f"{now.year:04d}-{now.month:02d}"

    async def _async_load_quota_state(self) -> None:
        if self._quota_loaded:
            return
        stored = await self._store.async_load() or {}
        self._quota_period = stored.get("period")
        self._calls_used = stored.get("calls_used", 0)
        self._quota_loaded = True

    async def _async_register_calls(self, count: int, now: datetime) -> None:
        period = self._period_key(now)
        if period != self._quota_period:
            self._quota_period = period
            self._calls_used = 0
        self._calls_used += count
        self._last_call_at = now
        await self._store.async_save({"period": self._quota_period, "calls_used": self._calls_used})

    def _calls_used_this_period(self, now: datetime) -> int:
        if self._period_key(now) != self._quota_period:
            return 0
        return self._calls_used

    def _calls_remaining(self, now: datetime) -> int:
        return max(self.monthly_quota - QUOTA_SAFETY_RESERVE - self._calls_used_this_period(now), 0)

    def _compute_adaptive_interval(self, now: datetime, calls_per_poll: int) -> float:
        """Spend the rest of this month's quota over the rest of its active time.

        "Active time" excludes configured quiet days/hours (presence is
        deliberately not modeled - see _active_fraction) so quota freed up
        by those gates is reinvested into more frequent polling the rest of
        the time, rather than averaged flatly across time we already know
        will be skipped.
        """
        remaining_calls = self._calls_remaining(now)
        if remaining_calls <= 0:
            return QUOTA_EXHAUSTED_RECHECK_INTERVAL

        remaining_active_seconds = _seconds_until_next_month(now) * _active_fraction(
            self.entry.options.get(CONF_QUIET_DAYS, []),
            self.entry.options.get(CONF_QUIET_HOURS_START),
            self.entry.options.get(CONF_QUIET_HOURS_END),
        )
        ideal_seconds_per_poll = (remaining_active_seconds / remaining_calls) * calls_per_poll
        return min(max(ideal_seconds_per_poll, MIN_ADAPTIVE_INTERVAL), MAX_ADAPTIVE_INTERVAL)

    def _skip_reason(self) -> str | None:
        """Return why polling should be skipped this cycle, or None to poll.

        These are the *soft*, user-configured quota-saving gates - a forced
        refresh (async_refresh_now) may bypass these. Contrast with the hard
        rate-limit/monthly-quota checks in _async_update_data, which apply
        unconditionally.
        """
        quiet_days = self.entry.options.get(CONF_QUIET_DAYS, [])
        now = dt_util.now()
        if WEEKDAYS[now.weekday()] in quiet_days:
            return f"today ({WEEKDAYS[now.weekday()]}) is a configured quiet day"

        start = self.entry.options.get(CONF_QUIET_HOURS_START)
        end = self.entry.options.get(CONF_QUIET_HOURS_END)
        if start and end:
            start_time = dt_util.parse_time(start)
            end_time = dt_util.parse_time(end)
            now_time = now.time()
            if start_time != end_time:
                in_window = (
                    start_time <= now_time < end_time
                    if start_time < end_time
                    else now_time >= start_time or now_time < end_time
                )
                if in_window:
                    return f"current time is within configured quiet hours ({start}-{end})"

        presence_entities = self.entry.options.get(CONF_PRESENCE_ENTITIES, [])
        if presence_entities:
            anyone_home = any(
                (state := self.hass.states.get(entity_id)) is not None
                and state.state == STATE_HOME
                for entity_id in presence_entities
            )
            if not anyone_home:
                return "none of the configured presence entities are home"

        return None

    async def _async_update_data(self) -> dict[str, list[dict[str, Any]]]:
        await self._async_load_quota_state()

        global_stop_ids = self.global_stop_ids
        if not global_stop_ids:
            _LOGGER.debug("Transit App: no stops configured on this entry, nothing to poll")
            return {}

        force = self._force_next_update
        self._force_next_update = False
        now = dt_util.now()

        # Hard limits from Transit App itself - these apply even to a
        # forced/manual refresh, unlike the soft gates below.
        if self._last_call_at is not None:
            seconds_since_last_call = (now - self._last_call_at).total_seconds()
            if seconds_since_last_call < self.min_seconds_between_calls:
                _LOGGER.debug(
                    "Skipping Transit App update: last API call was %.1fs ago, under "
                    "the %.1fs minimum needed to respect the %d calls/minute rate limit",
                    seconds_since_last_call,
                    self.min_seconds_between_calls,
                    self.rate_limit_per_minute,
                )
                return self.data or {stop_id: [] for stop_id in global_stop_ids}

        if self._calls_remaining(now) <= 0:
            _LOGGER.debug(
                "Skipping Transit App update: monthly API quota (%d calls) exhausted "
                "for %s",
                self.monthly_quota,
                self._period_key(now),
            )
            self.update_interval = timedelta(seconds=QUOTA_EXHAUSTED_RECHECK_INTERVAL)
            return self.data or {stop_id: [] for stop_id in global_stop_ids}

        skip_reason = None if force else self._skip_reason()
        if skip_reason is not None:
            _LOGGER.debug("Skipping Transit App update: %s", skip_reason)
            # Re-check soon (rather than at a stretched-out adaptive
            # interval left over from an earlier active period) so
            # conditions becoming active again - someone arriving home,
            # quiet hours ending - are picked up promptly.
            self.update_interval = timedelta(seconds=DEFAULT_SCAN_INTERVAL)
            return self.data or {stop_id: [] for stop_id in global_stop_ids}

        chunks = self._request_chunks(global_stop_ids)
        by_stop: dict[str, list[dict[str, Any]]] = {stop_id: [] for stop_id in global_stop_ids}
        unmatched_stop_ids: set[str] = set()
        try:
            for chunk in chunks:
                route_departures = await self.client.async_get_stop_departures(chunk)
                for route in route_departures:
                    stop_id = route.get("global_stop_id")
                    if stop_id in by_stop:
                        by_stop[stop_id].append(route)
                    else:
                        unmatched_stop_ids.add(stop_id)
        except TransitAppAuthError as err:
            raise UpdateFailed(f"Authentication failed: {err}") from err
        except TransitAppApiError as err:
            raise UpdateFailed(f"Error fetching departures: {err}") from err

        await self._async_register_calls(len(chunks), now)
        self.update_interval = timedelta(seconds=self._compute_adaptive_interval(now, len(chunks)))

        if unmatched_stop_ids:
            _LOGGER.debug(
                "Transit App returned route_departures for global_stop_id(s) %s that "
                "aren't tracked by this entry (tracked: %s) - ignoring them",
                unmatched_stop_ids,
                global_stop_ids,
            )
        _LOGGER.debug(
            "Transit App poll complete: queried %d stop(s) in %d request(s), got %d "
            "route(s) total: %s (%d/%d calls used this period, next poll in ~%ds)",
            len(global_stop_ids),
            len(chunks),
            sum(len(routes) for routes in by_stop.values()),
            {stop_id: len(routes) for stop_id, routes in by_stop.items()},
            self._calls_used,
            self.monthly_quota,
            self.update_interval.total_seconds(),
        )

        return by_stop
