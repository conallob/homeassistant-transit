"""Constants for the Transit App integration."""

DOMAIN = "transit_app"

CONF_API_KEY = "api_key"
CONF_STOPS = "stops"
CONF_GLOBAL_STOP_ID = "global_stop_id"
CONF_STOP_NAME = "stop_name"
CONF_RADIUS = "radius"
# Ephemeral, form-only field (not persisted to the config entry): narrows a
# nearby_stops search's results client-side by stop name/code/route before
# they're offered as choices. Re-entered on every search.
CONF_SEARCH_FILTER = "search_filter"
CONF_PRESENCE_ENTITIES = "presence_entities"
CONF_QUIET_HOURS_START = "quiet_hours_start"
CONF_QUIET_HOURS_END = "quiet_hours_end"
CONF_QUIET_DAYS = "quiet_days"
CONF_MONTHLY_QUOTA = "monthly_quota"
CONF_RATE_LIMIT_PER_MINUTE = "rate_limit_per_minute"

DEFAULT_RADIUS = 500  # metres, passed to nearby_stops as max_distance
DEFAULT_SCAN_INTERVAL = 60  # seconds

# Transit App's free-tier defaults as of 2026 (per Transit App staff) - a
# paid/different plan may have a different quota or rate limit, hence these
# being user-configurable rather than hardcoded.
DEFAULT_MONTHLY_QUOTA = 1500
DEFAULT_RATE_LIMIT_PER_MINUTE = 5

# Reserve a small slice of the monthly quota that adaptive pacing never
# plans to spend, so a burst of manual transit_app.refresh calls near the
# end of a billing period can't fully zero out the budget.
QUOTA_SAFETY_RESERVE = 10

# Bounds on the adaptive polling interval computed from remaining quota /
# remaining active time: never faster than the classic fixed interval (so
# behavior doesn't get *more* aggressive than the old default), and never
# so slow the integration feels dead if quota is genuinely scarce.
MIN_ADAPTIVE_INTERVAL = DEFAULT_SCAN_INTERVAL
MAX_ADAPTIVE_INTERVAL = 1800  # 30 minutes

# How long to wait between polls once the monthly quota is exhausted -
# there's no point checking every minute when every check will just skip.
QUOTA_EXHAUSTED_RECHECK_INTERVAL = 3600  # 1 hour

QUOTA_STORAGE_VERSION = 1

# Home Assistant's "home" presence state for person/device_tracker entities.
STATE_HOME = "home"

# Order matches datetime.weekday() (Monday=0 .. Sunday=6).
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

# Transit App's documented limits don't specify a hard cap on the number of
# global_stop_ids per stop_departures request; this keeps individual
# requests (and their URLs) to a reasonable size, splitting into multiple
# requests only when a single config entry tracks more stops than this.
MAX_GLOBAL_STOP_IDS_PER_REQUEST = 25

API_BASE_URL = "https://external.transitapp.com/v3/public"
NEARBY_STOPS_ENDPOINT = f"{API_BASE_URL}/nearby_stops"
STOP_DEPARTURES_ENDPOINT = f"{API_BASE_URL}/stop_departures"

ATTR_ROUTE_SHORT_NAME = "route_short_name"
ATTR_ROUTE_LONG_NAME = "route_long_name"
ATTR_DIRECTION_HEADSIGN = "direction_headsign"
ATTR_UPCOMING_DEPARTURES = "upcoming_departures"
ATTR_GLOBAL_STOP_ID = "global_stop_id"
ATTR_STOP_NAME = "stop_name"

MAX_UPCOMING_DEPARTURES = 5

SERVICE_REFRESH = "refresh"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"
