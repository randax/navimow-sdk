# Datamodellar

Alle modellane er dataklassar i `mower_sdk.models` med `from_dict()` /
`to_dict()`; ukjende felt blir tekne vare på i `extra` der det finst.

## Opprekningar

```python
class MowerStatus(Enum):  IDLE, MOWING, PAUSED, DOCKED, CHARGING, ERROR, RETURNING, UNKNOWN
class MowerCommand(Enum): START, PAUSE, DOCK, RESUME, STOP
class MowerError(Enum):   NONE, STUCK, LIFTED, RAIN, BATTERY_LOW, SENSOR_ERROR, MOTOR_ERROR, BLADE_ERROR, UNKNOWN
```

Bruk `.value` for strengen (`MowerStatus.MOWING.value == "mowing"`).

## `Device` (frå `authList`)

| Felt | Type | Merknad |
|---|---|---|
| `id` | `str` | Einings-ID brukt av alle andre kall |
| `name` | `str` | Namn synleg for brukaren |
| `model` | `str` | |
| `firmware_version` | `str` | |
| `serial_number` | `str` | |
| `mac_address` | `str \| None` | |
| `online` | `bool` | |
| `product_key`, `device_name`, `iot_id` | `str \| None` | Identifikatorar frå IoT-plattforma |
| `extra` | `dict \| None` | Umodellerte felt |

## `DeviceStatus` (REST-augneblinksbilete)

| Felt | Type |
|---|---|
| `device_id` | `str` |
| `status` | `MowerStatus` |
| `battery` | `int` (0–100) |
| `position` | `{"lat": float, "lng": float} \| None` |
| `error_code` | `MowerError` |
| `error_message` | `str \| None` |
| `mowing_time`, `total_mowing_time` | sekund, `int \| None` |
| `signal_strength` | `int \| None` |
| `timestamp` | `int \| None` |
| `extra` | `dict \| None` |

## MQTT-meldingar

### `DeviceStateMessage` — emne `…/realtimeDate/state`

| Felt | Type | Merknad |
|---|---|---|
| `device_id` | `str` | Lagd inn frå emnet |
| `timestamp` | `int \| None` | |
| `state` | `str` | Normalisert; råverdien blir teken vare på i `metrics["raw_state"]` når han skil seg |
| `battery` | `int \| None` | Henta ut frå fleire moglege lastformer |
| `signal_strength` | `int \| None` | |
| `position` | `dict \| None` | |
| `error` | `dict \| None` | |
| `metrics` | `dict \| None` | |

Råe skytilstandar blir normaliserte til `MowerStatus`-verdiar (`DeviceStatus`
brukar same tabell):

| Råverdi | Kanonisk |
|---|---|
| `isDocked` | `docked` |
| `isIdle`, `isIdel`, `Self-Checking` | `idle` |
| `isRunning`, `isMapping` | `mowing` |
| `isPaused`, `inSoftwareUpdate` | `paused` |
| `isDocking` | `returning` |
| `error`, `Error`, `isLifted` | `error` |
| `offline`, `Offline` | `unknown` |

Verdiar som ikkje står i lista går uendra gjennom (t.d. `charging`).

### `DeviceEventMessage` — emne `…/realtimeDate/event`

`device_id`, `timestamp`, `type` (standard `"system"`), `event`, `level`,
`message`, `params`.

### `DeviceAttributesMessage` — emne `…/realtimeDate/attributes`

`device_id`, `attributes: dict`.

### `DeviceLocationMessage` — emne `…/realtimeDate/location`

| Felt | Type | Merknad |
|---|---|---|
| `device_id` | `str` | Lagt inn frå MQTT-emnet |
| `x`, `y` | `float \| None` | Meter relativt til ladestasjonen |
| `theta` | `float \| None` | Retning i radianar |
| `timestamp` | `int \| None` | `time` på leidninga (`timestamp` blir òg godteken) |
| `type` | `str \| None` | Råverdien gjort om med `str()` |
| `vehicle_state` | `int \| None` | Ugjennomsiktig, separat vokabular frå tilstandskanalen |
| `mowing_percentage`, `subtotal_area` | `float \| None` | Framdrift og delareal når dei finst |
| `mow_start_type` | `Any \| None` | Rå starttype |
| `raw` | `dict` | Uendra punkt frå leidninga |

Felta er observerte gjennom `ioBroker.navimow`, ikkje dokumenterte i ei
offisiell protokollspesifikasjon.

### `DeviceCommandMessage` — utgåande

`id`, `device_id`, `command`, `params`. Blir bygd for deg av
`NavimowSDK.start_mowing()` og dei andre kommandometodane.

### `Thing*Message`

`ThingStatusMessage`, `ThingPropertiesMessage`, `ThingEventMessage` pakkar inn
«thing model»-konvolutten frå IoT-plattforma (`method`, `id`,
`params: ThingParams`, `version`) for konsumentar som arbeider med råe
thing-model-lastar.

## Døme: gjer ein status om til ein tilstand i Home Assistant-stil

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
