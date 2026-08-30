# Changelog

## 0.4.0

- `DeviceLocationMessage` får `current_zone`, `zone_progress`, `action`,
  `sub_action`, `week_area`, `partition_ids` og `task_delay`, og ein `status`-
  eigenskap som omset `vehicleState` (2 → `CHARGING`) via `VEHICLE_STATE_TO_STATUS`.
- Dokumenterer `vehicleState`- og `mapWorkPosition`-kodinga i `docs/models.md`.

## 0.3.0

- Legg til frivillig MQTT-posisjon (`location`) med `DeviceLocationMessage`,
  mellomlager og filtrering av plasshaldarar og forelda punkt.
- Legg til `extra_topics` og løkketrygge `on_raw`-tilbakekall for rå meldingar.
- Gjer `MowerMQTT` og `MowerClient.subscribe_device_updates()` klare for dei
  verkelege `realtimeDate`-emna, inkludert posisjon og statuskonvertering.
- Utvid `watch_state.py` med `--location`, `--raw` og `--discover`.

## 0.2.0

- Publish the fork as `randax-navimow-sdk` while retaining the `mower_sdk`
  Python import package.
- Support standard CPython 3.9.2 through 3.14.x, including Raspberry Pi OS on
  ARM32 and ARM64.
- Resolve Python 3.9 annotations through `typing.get_type_hints()` and adapt
  event-loop ownership to Python 3.14 behavior.
- Add caller-provided loop support, permanent loop affinity, cross-loop guards,
  and thread-safe MQTT callback dispatch.
- Move MQTT callbacks to Paho VERSION2 and select the latest compatible aiohttp
  release for Python 3.10+, while Python 3.9 uses the built-in standard-library
  HTTP transport.
- Add an offline compatibility suite and Python/ARM GitHub Actions matrices.
- Translate maintained comments and docstrings to Nynorsk while preserving
  runtime messages and protocol values.
- Align the package version, GPL-3.0-only declaration, and project URLs with the
  maintained repository.
