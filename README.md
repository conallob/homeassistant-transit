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
3. Enter your API key and confirm (or override) the search location and radius. A wide radius in a
   dense area can turn up a lot of stops - optionally narrow the list with the filter field, which
   matches against stop name, stop code, or route number/name.
4. Select the stops you want to track from the list of nearby stops. The same physical stop can be
   listed more than once if multiple transit agency feeds serve it - each `[bracketed]` tag is a
   different feed's `global_stop_id`, and both are legitimate, independently selectable options
   (not duplicates).
5. Optionally set up the API-call-saving filters described below (skippable - all off by default).

Sensors are created automatically for every route and direction Transit App reports departures
for at each selected stop, and new ones appear as new routes/directions show up (e.g. a route that
only runs certain times of day). Use the integration's **Configure** option any time afterwards to:

- **Add or change tracked stops** - search again from a new location/radius. Stops you already
  track stay selected even if a new search doesn't turn them up again, so a single config entry
  (and its single Transit App API key) can accumulate stops from multiple areas - e.g. home and
  work - and poll all of them together in as few batched `stop_departures` requests as possible,
  instead of needing a separate integration entry (and separate polling) per area.
- **Save API calls** - three independent, optional filters to help stay within your key's call
  quota:
  - *Presence filter*: only poll while at least one of the chosen `person`/`device_tracker`
    entities is `home`.
  - *Quiet hours*: a daily time window (e.g. overnight) during which polling is skipped.
  - *Quiet days*: whole weekdays (e.g. Saturday/Sunday) during which polling is skipped entirely.

  When a poll is skipped for any of these reasons, sensors simply keep their last known data
  rather than making a request.

## Development background

See [`rest/transit_app_next_bus.yaml`](https://github.com/conallob/Home-Assistant-Config/blob/main/rest/transit_app_next_bus.yaml)
in `conallob/Home-Assistant-Config` for the original REST-sensor proof of concept this
integration replaces, and the [Transit App API docs](https://api-doc.transitapp.com/v3.html) for
endpoint details.

## Testing

Unit tests use [`pytest-homeassistant-custom-component`](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component),
the standard test harness for Home Assistant custom integrations. They require Python 3.12+
(matching the `homeassistant` core version this pins):

```console
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements_test.txt
pytest tests --cov=custom_components.transit_app
```

`.github/workflows/test.yml` runs this same suite on every push/PR - against both Python 3.12
and 3.14, since which Python versions the test harness supports moves as new
`pytest-homeassistant-custom-component`/`homeassistant` releases land - and uploads coverage to
[Codecov](https://about.codecov.io/) (requires a `CODECOV_TOKEN` repository secret from
codecov.io). Alongside that, it runs the two checks every published HA custom integration is
expected to pass:
[`hassfest`](https://developers.home-assistant.io/docs/creating_integration_manifest/#hassfest)
(validates `manifest.json`/`strings.json`/translations structure) and
[HACS repository validation](https://hacs.xyz/docs/publish/action/) (validates `hacs.json` and
repository layout for HACS distribution).

## Releasing

Cutting a [GitHub release](https://github.com/conallob/homeassistant-transit/releases) triggers
`.github/workflows/release.yml`, which bumps `custom_components/transit_app/manifest.json`'s
`version` field to match the release tag (HACS requires these to match) and moves the release's
git tag to the commit containing that bump. Just create the release from the UI (or `gh release
create`) with the tag you want to ship - no manual version bump needed beforehand.
