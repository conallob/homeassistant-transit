# homeassistant-transit

A Home Assistant custom integration for the [Transit App](https://transitapp.com) public API
(`https://external.transitapp.com/v3/public`), providing "next departure" sensors for the
transit stops you care about.

## Why a custom integration instead of a REST sensor?

A hand-rolled `rest:` sensor with a `value_template` works, but it's a dead end for anyone who
isn't comfortable writing Jinja and hunting for `global_stop_id`s by hand. This integration
replaces that with a proper config flow:

- **Stop discovery** - enter a location (defaults to your Home Assistant home coordinates) and a
  search radius; the integration calls Transit App's `nearby_stops` endpoint and lets you pick the
  stops you want from a list, instead of requiring you to find `global_stop_id`s manually.
- **Credential management** - the API key is stored in the config entry (Home Assistant's
  standard, encrypted-at-rest storage for integration secrets), not in `secrets.yaml` or a
  hand-written REST resource.
- **Simple installation** - install via HACS or by copying `custom_components/transit_app`, then
  configure entirely through the UI (Settings -> Devices & Services -> Add Integration).
- **Native sensor types** - each tracked route/direction at a stop becomes its own `sensor` entity
  with `device_class: timestamp`, so its state is the next scheduled departure time and it plays
  well with Lovelace, templates, and automations out of the box - no naive JSON-blob parsing
  required.

## Installation

### HACS (custom repository)

1. HACS -> Integrations -> the `...` menu -> **Custom repositories**.
2. Add this repository URL with category **Integration**.
3. Install "Transit App", then restart Home Assistant.

### Manual

Copy `custom_components/transit_app` into your Home Assistant `config/custom_components/`
directory and restart Home Assistant.

## Configuration

1. Get an API key from [Transit App](https://transitapp.com/apis).
2. In Home Assistant: **Settings -> Devices & Services -> Add Integration -> Transit App**.
3. Enter your API key and confirm (or override) the search location and radius.
4. Select the stops you want to track from the list of nearby stops.

Sensors are created automatically for every route and direction Transit App reports departures
for at each selected stop, and new ones appear as new routes/directions show up (e.g. a route that
only runs certain times of day). To change which stops are tracked later, use the integration's
**Configure** option, which repeats the location search and stop-selection steps.

## Development background

See [`rest/transit_app_next_bus.yaml`](https://github.com/conallob/Home-Assistant-Config/blob/main/rest/transit_app_next_bus.yaml)
in `conallob/Home-Assistant-Config` for the original REST-sensor proof of concept this
integration replaces, and the [Transit App API docs](https://api-doc.transitapp.com/v3.html) for
endpoint details.
