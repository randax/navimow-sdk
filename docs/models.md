# Data models

All models are dataclasses in `mower_sdk.models` with `from_dict()` /
`to_dict()`; unknown fields are preserved in `extra` where present.

## Enums

```python
class MowerStatus(Enum):  IDLE, MOWING, PAUSED, DOCKED, CHARGING, ERROR, RETURNING, UNKNOWN
class MowerCommand(Enum): START, PAUSE, DOCK, RESUME, STOP
class MowerError(Enum):   NONE, STUCK, LIFTED, RAIN, BATTERY_LOW, SENSOR_ERROR, MOTOR_ERROR, BLADE_ERROR, UNKNOWN
```

Use `.value` for the string (`MowerStatus.MOWING.value == "mowing"`).

## `Device` (from `authList`)

| Field | Type | Notes |
|---|---|---|
| `id` | `str` | Device id used by every other call |
| `name` | `str` | User-visible name |
| `model` | `str` | |
| `firmware_version` | `str` | |
| `serial_number` | `str` | |
| `mac_address` | `str \| None` | |
| `online` | `bool` | |
| `product_key`, `device_name`, `iot_id` | `str \| None` | IoT platform identifiers |
| `extra` | `dict \| None` | Unmodelled fields |

## `DeviceStatus` (REST snapshot)

| Field | Type |
|---|---|
| `device_id` | `str` |
| `status` | `MowerStatus` |
| `battery` | `int` (0–100) |
| `position` | `{"lat": float, "lng": float} \| None` |
| `error_code` | `MowerError` |
| `error_message` | `str \| None` |
| `mowing_time`, `total_mowing_time` | seconds, `int \| None` |
| `signal_strength` | `int \| None` |
| `timestamp` | `int \| None` |
| `extra` | `dict \| None` |

## MQTT messages

### `DeviceStateMessage` — topic `…/realtimeDate/state`

| Field | Type | Notes |
|---|---|---|
| `device_id` | `str` | Injected from the topic |
| `timestamp` | `int \| None` | |
| `state` | `str` | Normalised; the raw value is kept in `metrics["raw_state"]` when it differs |

Raw cloud states are normalised to `MowerStatus` values (`DeviceStatus` uses
the same table):

| Raw value | Canonical |
|---|---|
| `isDocked` | `docked` |
| `isIdle`, `isIdel`, `Self-Checking` | `idle` |
| `isRunning`, `isMapping` | `mowing` |
| `isPaused`, `inSoftwareUpdate` | `paused` |
| `isDocking` | `returning` |
| `error`, `Error`, `isLifted` | `error` |
| `offline`, `Offline` | `unknown` |

Unlisted values pass through unchanged (e.g. `charging`).
| `battery` | `int \| None` | Extracted from several possible payload shapes |
| `signal_strength` | `int \| None` | |
| `position` | `dict \| None` | |
| `error` | `dict \| None` | |
| `metrics` | `dict \| None` | |

### `DeviceEventMessage` — topic `…/realtimeDate/event`

`device_id`, `timestamp`, `type` (default `"system"`), `event`, `level`,
`message`, `params`.

### `DeviceAttributesMessage` — topic `…/realtimeDate/attributes`

`device_id`, `attributes: dict`.

### `DeviceCommandMessage` — outgoing

`id`, `device_id`, `command`, `params`. Built for you by
`NavimowSDK.start_mowing()` and friends.

### `Thing*Message`

`ThingStatusMessage`, `ThingPropertiesMessage`, `ThingEventMessage` wrap the
IoT-platform "thing model" envelope (`method`, `id`, `params: ThingParams`,
`version`) for consumers that work with raw thing-model payloads.

## Example: turning a status into a Home Assistant-style state

```python
from mower_sdk import DeviceStatus, MowerError, MowerStatus

HA_ACTIVITY = {
    MowerStatus.MOWING: "mowing",
    MowerStatus.PAUSED: "paused",
    MowerStatus.RETURNING: "returning",
    MowerStatus.DOCKED: "docked",
    MowerStatus.CHARGING: "docked",
    MowerStatus.IDLE: "docked",
    MowerStatus.ERROR: "error",
}

def to_ha(status: DeviceStatus) -> dict:
    return {
        "activity": HA_ACTIVITY.get(status.status, "unknown"),
        "battery": status.battery,
        "problem": None if status.error_code is MowerError.NONE else status.error_code.value,
    }
```
