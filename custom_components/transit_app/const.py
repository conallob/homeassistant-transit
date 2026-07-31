"""Constants for the Transit App integration."""

DOMAIN = "transit_app"

CONF_API_KEY = "api_key"
CONF_STOPS = "stops"
CONF_GLOBAL_STOP_ID = "global_stop_id"
CONF_STOP_NAME = "stop_name"
CONF_ROUTE_TYPE_FILTER = "route_type_filter"
CONF_RADIUS = "radius"
CONF_PRESENCE_ENTITIES = "presence_entities"
CONF_QUIET_HOURS_START = "quiet_hours_start"
CONF_QUIET_HOURS_END = "quiet_hours_end"
CONF_QUIET_DAYS = "quiet_days"

DEFAULT_RADIUS = 500  # metres, passed to nearby_stops as max_distance
DEFAULT_SCAN_INTERVAL = 60  # seconds

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
