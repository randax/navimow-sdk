"""Modul for datamodellar.

Definerer alle datamodellar som SDK-en bruker, inkludert opprekningar og dataklassar.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

_LOGGER = logging.getLogger(__name__)

_RAW_STATE_TO_CANONICAL: dict[str, str] = {
    "isDocked": "docked",
    "isIdel": "idle",
    "isIdle": "idle",
    "isMapping": "mowing",
    "isRunning": "mowing",
    "isPaused": "paused",
    "isDocking": "returning",
    "Error": "error",
    "error": "error",
    "isLifted": "error",
    "inSoftwareUpdate": "paused",
    "Self-Checking": "idle",
    "Self-checking": "idle",
    "Offline": "unknown",
    "offline": "unknown",
}


def _state_source(data: dict[str, Any]) -> Any:
    """Den eine staden som avgjer kva nøkkel tilstanden blir lesen frå."""
    return data.get("state") or data.get("status") or data.get("vehicleState")


def _is_recognised_state(raw_state: Any) -> bool:
    """Sei om råverdien er ein tilstand vi kjenner (kanonisk eller via oppslagstabellen)."""
    if isinstance(raw_state, MowerStatus):
        return True
    if not isinstance(raw_state, str):
        return False
    canonical = _RAW_STATE_TO_CANONICAL.get(raw_state, raw_state)
    return any(canonical == member.value for member in MowerStatus)


def _normalize_state_value(raw_state: Any) -> str:
    """Normaliser skya eller rå klipparstatus til intern kanonisk status."""
    if isinstance(raw_state, MowerStatus):
        return raw_state.value
    if not isinstance(raw_state, str):
        return "unknown"
    return _RAW_STATE_TO_CANONICAL.get(raw_state, raw_state)


def _extract_battery_value(data: dict[str, Any]) -> int:
    """Hent batteriprosent frå fleire ulike lastformat (0 når det ikkje finst)."""
    value = _extract_battery_value_or_none(data)
    return 0 if value is None else value


def _extract_battery_value_or_none(data: dict[str, Any]) -> Optional[int]:
    """Hent batteriprosent, eller None når lasta ikkje ber ein brukbar verdi."""

    def _to_int_or_none(value: Any) -> Optional[int]:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    # MQTT-statuslaster har ofte eit direkte batterifelt.
    battery = _to_int_or_none(data.get("battery"))
    if battery is not None:
        return battery

    # HTTP-laster frå getVehicleStatus bruker capacityRemaining[].rawValue.
    capacity_remaining = data.get("capacityRemaining")
    if isinstance(capacity_remaining, list):
        for item in capacity_remaining:
            if not isinstance(item, dict):
                continue
            unit = str(item.get("unit", "")).upper()
            if unit == "PERCENTAGE":
                raw_value = _to_int_or_none(item.get("rawValue"))
                if raw_value is not None:
                    return raw_value

        # Kompatibilitetsreserve: dersom PERCENTAGE-eining manglar, prøv første element.
        if capacity_remaining and isinstance(capacity_remaining[0], dict):
            raw_value = _to_int_or_none(capacity_remaining[0].get("rawValue"))
            if raw_value is not None:
                return raw_value

    return None


class MowerStatus(Enum):
    """Opprekning for statusar til robotklipparen."""

    IDLE = "idle"  # I ro
    MOWING = "mowing"  # Klipper
    PAUSED = "paused"  # Sett på pause
    DOCKED = "docked"  # I ladestasjonen
    CHARGING = "charging"  # Ladar
    ERROR = "error"  # Feil
    RETURNING = "returning"  # På veg tilbake
    UNKNOWN = "unknown"  # Ukjend status


class MowerCommand(Enum):
    """Opprekning for kontrollkommandoar til robotklipparen."""

    START = "start"  # Start klipping
    PAUSE = "pause"  # Set på pause
    DOCK = "dock"  # Returner til ladestasjonen
    RESUME = "resume"  # Hald fram med klippinga
    STOP = "stop"  # Stopp


class MowerError(Enum):
    """Opprekning for feiltypar på robotklipparen."""

    NONE = "none"  # Ingen feil
    STUCK = "stuck"  # Sit fast
    LIFTED = "lifted"  # Løfta opp
    RAIN = "rain"  # Regn
    BATTERY_LOW = "battery_low"  # Låg batteristatus
    SENSOR_ERROR = "sensor_error"  # Sensorfeil
    MOTOR_ERROR = "motor_error"  # Motorfeil
    BLADE_ERROR = "blade_error"  # Knivfeil
    UNKNOWN = "unknown"  # Ukjend feil


@dataclass
class Device:
    """Dataklasse for einingsinformasjon.

    Eigenskapar:
        id: Eining-ID
        name: Namn på eininga
        model: Einingstype
        firmware_version: Fastvareversjon
        serial_number: Serienummer
        mac_address: MAC-adresse (valfri)
        online: Om eininga er på nett
        extra: Ekstra informasjon (valfri)
    """

    id: str
    name: str
    model: str
    firmware_version: str
    serial_number: str
    mac_address: Optional[str] = None
    online: bool = False
    extra: Optional[dict[str, Any]] = None
    product_key: Optional[str] = None
    device_name: Optional[str] = None
    iot_id: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Device":
        """Lag ein Device-instans frå ei ordbok.

        Parametrar:
            data: Ordboka som inneheld einingsinformasjon

        Retur:
            Ein Device-instans
        """
        product_key = data.get("productKey") or data.get("product_key")
        device_name = data.get("deviceName") or data.get("device_name") or data.get("name")
        iot_id = data.get("iotId") or data.get("iot_id") or data.get("id")

        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            model=data.get("model", ""),
            firmware_version=data.get("firmware_version", ""),
            serial_number=data.get("serial_number", ""),
            mac_address=data.get("mac_address"),
            online=data.get("online", False),
            extra=data.get("extra"),
            product_key=product_key,
            device_name=device_name,
            iot_id=iot_id,
        )

    def to_dict(self) -> dict[str, Any]:
        """Gjer om til ei ordbok.

        Retur:
            Ei ordbok med einingsinformasjon
        """
        result: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "model": self.model,
            "firmware_version": self.firmware_version,
            "serial_number": self.serial_number,
            "online": self.online,
        }
        if self.mac_address:
            result["mac_address"] = self.mac_address
        if self.extra:
            result["extra"] = self.extra
        if self.product_key:
            result["product_key"] = self.product_key
        if self.device_name:
            result["device_name"] = self.device_name
        if self.iot_id:
            result["iot_id"] = self.iot_id
        return result


@dataclass
class ThingParams:
    """Felles parametrar for Thing-meldingar."""

    iot_id: Optional[str] = None
    product_key: Optional[str] = None
    device_name: Optional[str] = None
    identifier: Optional[str] = None
    value: Optional[Any] = None
    raw: Optional[dict[str, Any]] = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ThingParams":
        return cls(
            iot_id=data.get("iotId") or data.get("iot_id"),
            product_key=data.get("productKey") or data.get("product_key"),
            device_name=data.get("deviceName") or data.get("device_name"),
            identifier=data.get("identifier"),
            value=data.get("value"),
            raw=data,
        )


@dataclass
class ThingStatusMessage:
    """Thing-statusmelding."""

    method: Optional[str]
    id: Optional[str]
    params: ThingParams
    version: Optional[str]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ThingStatusMessage":
        return cls(
            method=payload.get("method"),
            id=payload.get("id"),
            params=ThingParams.from_dict(payload.get("params", {})),
            version=payload.get("version"),
        )


@dataclass
class ThingPropertiesMessage:
    """Thing-eigenskapsmelding."""

    method: Optional[str]
    id: Optional[str]
    params: ThingParams
    version: Optional[str]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ThingPropertiesMessage":
        return cls(
            method=payload.get("method"),
            id=payload.get("id"),
            params=ThingParams.from_dict(payload.get("params", {})),
            version=payload.get("version"),
        )


@dataclass
class ThingEventMessage:
    """Thing-hendingmelding."""

    method: Optional[str]
    id: Optional[str]
    params: ThingParams
    version: Optional[str]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ThingEventMessage":
        return cls(
            method=payload.get("method"),
            id=payload.get("id"),
            params=ThingParams.from_dict(payload.get("params", {})),
            version=payload.get("version"),
        )


@dataclass
class DeviceStatus:
    """Dataklasse for einingsstatus.

    Eigenskapar:
        device_id: Eining-ID
        status: Einingstatus (MowerStatus-verdien)
        battery: Batterinivå (0-100)
        position: Posisjonsinformasjon (valfri, format: {"lat": float, "lng": float})
        error_code: Feilkode (MowerError-verdien)
        error_message: Feilmelding (valfri)
        mowing_time: Denne klippeøkta i sekund (valfri)
        total_mowing_time: Total klippetid i sekund (valfri)
        signal_strength: Signalstyrke (valfri)
        timestamp: Tidspunkt for statusoppdatering (valfri)
        extra: Ekstra informasjon (valfri)
    """

    device_id: str
    status: MowerStatus
    battery: int
    position: Optional[dict[str, float]] = None
    error_code: MowerError = MowerError.NONE
    error_message: Optional[str] = None
    mowing_time: Optional[int] = None
    total_mowing_time: Optional[int] = None
    signal_strength: Optional[int] = None
    timestamp: Optional[int] = None
    extra: Optional[dict[str, Any]] = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DeviceStatus":
        """Lag ein DeviceStatus-instans frå ei ordbok.

        Parametrar:
            data: Ordboka som inneheld einingsstatus

        Retur:
            Ein DeviceStatus-instans
        """
        status_source = _state_source(data)
        normalized_state = _normalize_state_value(status_source)
        try:
            status = MowerStatus(normalized_state)
        except ValueError:
            status = MowerStatus.UNKNOWN

        error_str = data.get("error_code", "none")
        try:
            error_code = MowerError(error_str)
        except ValueError:
            error_code = MowerError.UNKNOWN

        battery = _extract_battery_value(data)

        extra: dict[str, Any] = dict(data.get("extra") or {})
        if "vehicleState" in data:
            extra["vehicleState"] = data.get("vehicleState")
        if "descriptiveCapacityRemaining" in data:
            extra["descriptiveCapacityRemaining"] = data.get("descriptiveCapacityRemaining")
        if "capacityRemaining" in data:
            extra["capacityRemaining"] = data.get("capacityRemaining")

        return cls(
            device_id=data.get("device_id") or data.get("id", ""),
            status=status,
            battery=battery,
            position=data.get("position"),
            error_code=error_code,
            error_message=data.get("error_message"),
            mowing_time=data.get("mowing_time"),
            total_mowing_time=data.get("total_mowing_time"),
            signal_strength=data.get("signal_strength"),
            timestamp=data.get("timestamp"),
            extra=extra or None,
        )

    @classmethod
    def from_state_message(
        cls,
        message: "DeviceStateMessage",
        fallback_status: Optional[MowerStatus] = None,
        fallback_battery: Optional[int] = None,
    ) -> "DeviceStatus":
        """Gjer ei MQTT-tilstandsmelding om til statusmodellen.

        Byggjer på `from_dict` slik at posisjon, feilkode, klippetid og `extra` blir
        handsama på same måte som for REST-svar. Tilstandskanalen sender delvise
        meldingar, så manglande tilstand/batteri fell tilbake til dei bufra verdiane.
        """
        # Ei melding laga direkte (utan `raw`) blir bygd frå dei tolka felta.
        raw = dict(message.raw) if message.raw else message.to_dict()
        raw.setdefault("device_id", message.device_id)
        status = cls.from_dict(raw)

        if status.status is MowerStatus.UNKNOWN and fallback_status is not None:
            # Fall berre tilbake når tilstanden manglar eller er ukjend for oss (t.d.
            # numerisk vehicleState). Ein eksplisitt kjend verdi som «offline» skal
            # sleppe gjennom som UNKNOWN, elles maskerer vi at klipparen forsvann.
            raw_state = _state_source(raw)
            if not _is_recognised_state(raw_state):
                status.status = fallback_status
        if _extract_battery_value_or_none(raw) is None and fallback_battery is not None:
            status.battery = fallback_battery
        return status

    def to_dict(self) -> dict[str, Any]:
        """Gjer om til ei ordbok.

        Retur:
            Ei ordbok med einingsstatus
        """
        result: dict[str, Any] = {
            "device_id": self.device_id,
            "status": self.status.value,
            "battery": self.battery,
            "error_code": self.error_code.value,
        }
        if self.position:
            result["position"] = self.position
        if self.error_message:
            result["error_message"] = self.error_message
        if self.mowing_time is not None:
            result["mowing_time"] = self.mowing_time
        if self.total_mowing_time is not None:
            result["total_mowing_time"] = self.total_mowing_time
        if self.signal_strength is not None:
            result["signal_strength"] = self.signal_strength
        if self.timestamp is not None:
            result["timestamp"] = self.timestamp
        if self.extra:
            result["extra"] = self.extra
        return result


@dataclass
class DeviceStateMessage:
    """Samla statusmelding frå MQTT."""

    device_id: str
    timestamp: Optional[int]
    state: str
    battery: Optional[int] = None
    signal_strength: Optional[int] = None
    position: Optional[dict[str, float]] = None
    error: Optional[dict[str, Any]] = None
    metrics: Optional[dict[str, Any]] = None
    raw: Optional[dict[str, Any]] = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DeviceStateMessage":
        raw_state = _state_source(payload)
        normalized_state = _normalize_state_value(raw_state)
        metrics = dict(payload.get("metrics") or {})  # kopi: ikkje endre lasta til kallaren
        if raw_state is not None and normalized_state != raw_state:
            metrics["raw_state"] = raw_state

        return cls(
            device_id=payload.get("device_id", ""),
            timestamp=payload.get("timestamp"),
            state=normalized_state,
            battery=_extract_battery_value(payload),
            signal_strength=payload.get("signal_strength"),
            position=payload.get("position"),
            error=payload.get("error"),
            metrics=metrics or None,
            raw=dict(payload),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "timestamp": self.timestamp,
            "state": self.state,
            "battery": self.battery,
            "signal_strength": self.signal_strength,
            "position": self.position,
            "error": self.error,
            "metrics": self.metrics,
        }


def _float_or_none(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> Optional[int]:
    """Tolk heiltal, òg frå desimaltal og tal som strengar ("1755000000.5")."""
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None


@dataclass
class DeviceLocationMessage:
    """Ei einskild posisjonslesing frå MQTT."""

    device_id: str
    x: Optional[float] = None
    y: Optional[float] = None
    theta: Optional[float] = None
    timestamp: Optional[int] = None
    type: Optional[str] = None
    vehicle_state: Optional[int] = None
    mowing_percentage: Optional[float] = None
    subtotal_area: Optional[float] = None
    mow_start_type: Optional[Any] = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DeviceLocationMessage":
        """Lag ei posisjonslesing frå den observerte leidningsforma."""
        raw_type = payload.get("type")
        return cls(
            device_id=str(payload.get("device_id", "")),
            x=_float_or_none(payload.get("postureX")),
            y=_float_or_none(payload.get("postureY")),
            theta=_float_or_none(payload.get("postureTheta")),
            timestamp=_int_or_none(
                payload["time"] if payload.get("time") is not None else payload.get("timestamp")
            ),
            type=str(raw_type) if raw_type is not None else None,
            vehicle_state=_int_or_none(payload.get("vehicleState")),
            mowing_percentage=_float_or_none(payload.get("mowingPercentage")),
            subtotal_area=_float_or_none(payload.get("subtotalArea")),
            mow_start_type=payload.get("mowStartType"),
            raw=dict(payload),
        )

    @property
    def is_placeholder(self) -> bool:
        """Sei om lesinga er klipparen sin stilleståande plasshaldar."""
        return self.x == 0.0 and self.y == 0.0 and self.theta == 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "x": self.x,
            "y": self.y,
            "theta": self.theta,
            "timestamp": self.timestamp,
            "type": self.type,
            "vehicle_state": self.vehicle_state,
            "mowing_percentage": self.mowing_percentage,
            "subtotal_area": self.subtotal_area,
            "mow_start_type": self.mow_start_type,
            "raw": self.raw,
        }


def parse_location_payload(payload: Any, device_id: str) -> list[DeviceLocationMessage]:
    """Tolk éi eller fleire posisjonslesingar og set einings-ID frå MQTT-emnet."""
    values = payload if isinstance(payload, list) else [payload]
    points: list[DeviceLocationMessage] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        point = DeviceLocationMessage.from_dict(value)
        point.device_id = device_id
        points.append(point)
    return points


class LocationFilter:
    """Filtrer bort plasshaldarar og seint komne posisjonslesingar."""

    def __init__(self) -> None:
        self._newest_timestamps: dict[tuple[str, str], int] = {}

    def filter(self, points: list[DeviceLocationMessage]) -> list[DeviceLocationMessage]:
        accepted: list[DeviceLocationMessage] = []
        for point in points:
            if point.is_placeholder:
                _LOGGER.debug("Dropping placeholder location for device %s", point.device_id)
                continue
            if point.timestamp is None or point.type is None:
                accepted.append(point)
                continue
            key = (point.device_id, point.type)
            newest = self._newest_timestamps.get(key)
            if newest is not None and point.timestamp < newest:
                _LOGGER.debug(
                    "Dropping stale location for device %s type %s: %s < %s",
                    point.device_id,
                    point.type,
                    point.timestamp,
                    newest,
                )
                continue
            self._newest_timestamps[key] = point.timestamp
            accepted.append(point)
        return accepted


@dataclass
class DeviceEventMessage:
    """Samla hendingmelding frå MQTT."""

    device_id: str
    timestamp: Optional[int]
    type: str
    event: str
    level: Optional[str] = None
    message: Optional[str] = None
    params: Optional[dict[str, Any]] = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DeviceEventMessage":
        return cls(
            device_id=payload.get("device_id", ""),
            timestamp=payload.get("timestamp"),
            type=payload.get("type", "system"),
            event=payload.get("event", ""),
            level=payload.get("level"),
            message=payload.get("message"),
            params=payload.get("params"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "timestamp": self.timestamp,
            "type": self.type,
            "event": self.event,
            "level": self.level,
            "message": self.message,
            "params": self.params,
        }


@dataclass
class DeviceAttributesMessage:
    """Samla eigenskapmelding frå MQTT."""

    device_id: str
    attributes: dict[str, Any]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DeviceAttributesMessage":
        return cls(
            device_id=payload.get("device_id", ""),
            attributes=payload.get("attributes", {}) or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "attributes": self.attributes,
        }


@dataclass
class DeviceCommandMessage:
    """Samla kommandomelding for MQTT-publisering."""

    id: str
    device_id: str
    command: str
    params: Optional[dict[str, Any]] = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DeviceCommandMessage":
        return cls(
            id=payload.get("id", ""),
            device_id=payload.get("device_id", ""),
            command=payload.get("command", ""),
            params=payload.get("params"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "device_id": self.device_id,
            "command": self.command,
            "params": self.params or {},
        }
