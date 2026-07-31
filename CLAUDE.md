# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Home Assistant custom integration (`custom_components/transit_app`) for the
[Transit App](https://transitapp.com) public API (`https://external.transitapp.com/v3/public`).
It replaces a hand-written `rest:` sensor + Jinja template approach (see
`conallob/Home-Assistant-Config`'s `rest/transit_app_next_bus.yaml`) with a proper config flow,
API-key-backed config entry, and dynamically created native sensors.

## Commands

Tests require **Python 3.12+** (matching the `homeassistant` version the test harness pins).

```console
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements_test.txt

pytest tests                                          # full suite
pytest tests --cov=custom_components.transit_app       # with coverage (what CI runs)
pytest tests/test_coordinator.py                       # one file
pytest tests/test_coordinator.py::test_quiet_day_skips_polling  # one test
```

`requirements_test.txt` deliberately does **not** pin an exact
`pytest-homeassistant-custom-component` version: each release of that package hard-pins one exact
`homeassistant` version, which in turn supports only a narrow Python range. Pinning here would
make it unresolvable on whichever Python version(s) that release doesn't support - let pip pick
whatever's compatible with the interpreter actually running.

No linter/formatter is configured in this repo.

## CI (`.github/workflows/`)

- **`test.yml`**: runs `pytest` across a Python 3.12 / 3.14 matrix (`fail-fast: false`), uploads
  coverage as a build artifact and to Codecov (needs a `CODECOV_TOKEN` repo secret), then runs
  `hassfest` (validates `manifest.json`/`strings.json`/translations structure) and HACS repository
  validation (`hacs/action`, with `brands`/`topics` checks ignored - those require a merged
  `home-assistant/brands` submission and GitHub repo topics, i.e. default-store-listing
  prerequisites this repo doesn't need yet since it's installed as a HACS custom repository).
  - The Python 3.14 matrix leg is marked `experimental`/`continue-on-error`: as of writing,
    `pytest-homeassistant-custom-component`'s only 3.14-compatible release pulls in a transitive
    dependency (`mashumaro`) that references `typing.ByteString`, which Python 3.14 removed - the
    test harness fails to even import, unconditionally, before any test runs. This is an upstream
    issue, not something fixable in this repo. Remove the `experimental` flag once upstream fixes
    it (see the comment in `test.yml` for exact details).
  - `hacs/action`'s `ignore` input is **space-separated**, not comma-separated (e.g.
    `"brands topics"`) - a comma-separated value is silently treated as one unrecognized token and
    does nothing.
- **`release.yml`**: on a published GitHub release, bumps `custom_components/transit_app/manifest.json`'s
  `version` to match the release tag (HACS requires these to match), commits that to the branch
  the release was cut from, and moves the release's git tag onto the resulting commit (so the tag
  doesn't keep pointing at a manifest with the wrong version). No manual version bump needed before
  cutting a release.

## Architecture

Everything under `custom_components/transit_app/` follows the standard HA custom-integration
shape, with one design decision worth understanding: **sensors are discovered dynamically at
runtime**, not fixed at config time - there's no way to know which routes/directions a stop serves
until the API is actually queried, and that can change over time (e.g. a route that only runs at
certain times of day).

- **`api.py`** - thin `TransitAppClient` wrapper around `nearby_stops` and `stop_departures`.
  Raises `TransitAppAuthError` (401/403) vs the generic `TransitAppApiError`; the config flow and
  coordinator branch on this distinction to show different user-facing errors.
- **`config_flow.py`** - `TransitAppConfigFlow` (initial setup: API key -> location/radius search
  -> pick stops from `nearby_stops` results -> optional quota-saving filters) and
  `TransitAppOptionsFlow` (a menu: re-search/add stops, or edit filters). Both share
  `TransitAppFlowMixin._async_search_nearby_stops`.
  - **Stop accumulation, not replacement**: `_merge_previously_tracked_stops` folds
    already-configured stops into a fresh search's choices even if the new search doesn't
    re-find them (different location/radius). Without this, re-searching from a second area
    would silently drop stops found in an earlier search. This is what lets one config entry (one
    API key, one coordinator, one batched request) cover multiple physical areas instead of
    needing a separate integration instance - and separate polling - per area.
  - The quiet-hours time fields must not get a `default=""` in the voluptuous schema - a
    `TimeSelector` rejects `""` as invalid, so `_time_field()` only attaches a default when a real
    value already exists. (This exact mistake shipped once and broke submitting the filters form
    with blank quiet hours; caught by `test_config_flow.py`.)
- **`coordinator.py`** - `TransitAppDataUpdateCoordinator` polls `stop_departures` for every
  tracked `global_stop_id`, batched into as few requests as possible
  (`MAX_GLOBAL_STOP_IDS_PER_REQUEST` chunks). Two tiers of gating before a poll:
  - **Soft, user-configured gates** (`_skip_reason()`, all off by default, bypassable by a forced
    refresh): quiet days, quiet hours (handles overnight wrap, e.g. 23:00-06:00), and a presence
    filter (skip unless a configured `person`/`device_tracker` entity is `home`).
  - **Hard limits from Transit App itself** (`_async_update_data()`, never bypassable, checked
    before the soft gates): a calls/minute rate limit (`min_seconds_between_calls`, tracked via
    `_last_call_at`) and the monthly call quota (`_calls_remaining()`, tracked via a
    `homeassistant.helpers.storage.Store`-backed counter keyed by `"YYYY-MM"` so it survives
    restarts - see `_async_load_quota_state`/`_async_register_calls`).

  Whenever a poll is skipped (either tier), it returns the previous `self.data` unchanged rather
  than hitting the network.

  `update_interval` is **adaptive**, not fixed: after a successful poll, `_compute_adaptive_interval()`
  spends the *rest* of the monthly quota over the *rest* of the estimated-active time in the
  billing month (`_active_fraction()` - excludes configured quiet days/hours, but deliberately
  *not* presence, since future absence can't be predicted; an unpredicted skip just leaves that
  call unspent, which shows up as extra remaining quota next poll and speeds up the interval to
  compensate). Clamped between `MIN_ADAPTIVE_INTERVAL` (the old fixed default) and
  `MAX_ADAPTIVE_INTERVAL`. After a *soft* skip, `update_interval` is deliberately reset back down to
  `DEFAULT_SCAN_INTERVAL` (not left at whatever long interval an earlier active period computed) so
  a condition becoming active again - quiet hours ending, someone arriving home - is noticed
  promptly rather than after a stretched-out interval.
- **`sensor.py`** - `async_setup_entry` keeps a `known_keys` set of `(stop_id, route_key,
  direction)` tuples and calls `async_add_entities` only for newly-discovered combinations,
  re-checking on every coordinator update via `coordinator.async_add_listener`. Each
  `TransitAppDepartureSensor` is `device_class: timestamp` (state = next non-cancelled departure
  time), looks its own data up live from `coordinator.data` on every property access (not cached at
  construction), and reports `unavailable` if its route/direction stops appearing in the API
  response rather than being torn down.
- **`__init__.py`** - standard `async_setup_entry`/`async_unload_entry`, plus an options-update
  listener that triggers `coordinator.async_request_refresh()`.

## Tests (`tests/`)

Built on `pytest-homeassistant-custom-component`. `tests/conftest.py` has two fixtures worth
knowing about before adding new tests:

- `_warm_up_pycares_shutdown_thread` (session-scoped, autouse) - works around a false positive in
  the test harness's leaked-thread check: `aiohttp`'s `AsyncResolver` (via `aiodns`/`pycares`,
  both Home Assistant dependencies) lazily starts a background daemon thread the first time a real
  `ClientSession`/connector is torn down. If that happens to be during the *first* test that calls
  `async_get_clientsession()`, the thread looks "new" to that test's before/after snapshot and
  fails it. Starting the thread once, up front, keeps it out of every test's diff.
- `config_entry` - a `MockConfigEntry` tracking one stop (`DUBIE:72440`), already added to `hass`.

Other conventions:
- `test_api.py` mocks the aiohttp session directly with hand-rolled fakes (`_session_returning`,
  `_session_raising`) rather than a library like `aioresponses` - that library's last release
  (0.7.9) doesn't track aiohttp's `ClientResponse` constructor and breaks on newer aiohttp/Python
  (confirmed by actually running the suite under Python 3.14).
- Time-dependent coordinator tests (`test_coordinator.py`) use the `freezer` fixture
  (`pytest_freezer`) and explicitly call `await hass.config.async_set_time_zone("UTC")` before
  freezing - the test `hass` fixture defaults to `US/Pacific`, so skipping this makes frozen
  wall-clock times land in the wrong side of a quiet-hours window.
- Config entry `data`/`options` cannot be mutated directly in tests (`entry.data = ...` raises);
  use `hass.config_entries.async_update_entry(entry, data=..., options=...)`.
- `OptionsFlow` subclasses must not assign `self.config_entry` in `__init__` - recent Home
  Assistant versions provide it automatically as a property, and manual assignment conflicts with
  that.
