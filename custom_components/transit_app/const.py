"""Constants for the Transit App integration."""

DOMAIN = "transit_app"

CONF_API_KEY = "api_key"
CONF_STOPS = "stops"
CONF_GLOBAL_STOP_ID = "global_stop_id"
CONF_STOP_NAME = "stop_name"
CONF_ROUTE_TYPE_FILTER = "route_type_filter"
CONF_RADIUS = "radius"

DEFAULT_RADIUS = 500  # metres, passed to nearby_stops as max_distance
DEFAULT_SCAN_INTERVAL = 60  # seconds

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
