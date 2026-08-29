"""Navimow-SDK med MQTT-basert integrasjon."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Callable
from typing import Any, Optional

from mower_sdk.models import (
    DeviceAttributesMessage,
    DeviceCommandMessage,
    DeviceEventMessage,
    DeviceLocationMessage,
    DeviceStateMessage,
    LocationFilter,
    extract_battery_value_or_none,
    is_recognised_state,
    parse_location_payload,
    state_source,
)
from mower_sdk.mqtt import NavimowMQTT

_LOGGER = logging.getLogger(__name__)


class NavimowSDK:
    """SDK-fasade for MQTT-styrt integrasjon.

    Merknader:
        - on_state/on_event/on_attributes/on_location/on_raw-tilbakekall er synkrone.
        - Tilbakekalla blir alltid køyrde på den bundne asyncio-løkka (aldri på
          MQTT-tråden), så Home Assistant kan røre entitetar direkte i dei.
    """

    def __init__(
        self,
        broker: str,
        port: int,
        username: Optional[str] = None,
        password: Optional[str] = None,
        ws_path: Optional[str] = None,
        auth_headers: Optional[dict[str, str]] = None,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        records: Optional[list[Any]] = None,
        keepalive_seconds: int = 2400,
        reconnect_min_delay: int = 1,
        reconnect_max_delay: int = 60,
        subscribe_location: bool = False,
        extra_topics: Optional[list[str]] = None,
    ) -> None:
        self._loop = loop
        self._mqtt = NavimowMQTT(
            broker=broker,
            port=port,
            username=username,
            password=password,
            records=records or [],
            ws_path=ws_path,
            auth_headers=auth_headers,
            loop=self._loop,
            keepalive_seconds=keepalive_seconds,
            reconnect_min_delay=reconnect_min_delay,
            reconnect_max_delay=reconnect_max_delay,
            subscribe_location=subscribe_location,
            extra_topics=extra_topics,
        )
        self._loop = self._mqtt.loop
        self._mqtt.on_message = self._on_mqtt_message

        self._state_callbacks: list[Callable[[DeviceStateMessage], None]] = []
        self._event_callbacks: list[Callable[[DeviceEventMessage], None]] = []
        self._attributes_callbacks: list[Callable[[DeviceAttributesMessage], None]] = []
        self._location_callbacks: list[Callable[[DeviceLocationMessage], None]] = []
        self._raw_callbacks: list[Callable[[str, bytes], None]] = []

        self._state_cache: dict[str, DeviceStateMessage] = {}
        self._attributes_cache: dict[str, DeviceAttributesMessage] = {}
        self._location_cache: dict[str, DeviceLocationMessage] = {}
        self._location_filter = LocationFilter()

    def connect(self) -> None:
        """Kople til MQTT-meglaren og start mottak."""
        try:
            self._mqtt.connect_async()
        except RuntimeError as exc:
            if self._loop is None and self._mqtt.loop is None:
                raise RuntimeError(
                    "NavimowSDK.connect() requires a running event loop or an explicit loop= argument"
                ) from exc
            raise
        self._loop = self._mqtt.loop

    def disconnect(self) -> None:
        """Bryt tilkoplinga til MQTT-meglaren."""
        self._mqtt.disconnect()

    def update_mqtt_credentials(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        auth_headers: Optional[dict[str, str]] = None,
    ) -> None:
        """Oppdater MQTT-legitimasjonen og bygg klienten opp att ved behov.

        Vent med attkoplinga dersom inga hendingsløkke er bunden enno.
        """
        self._mqtt.update_credentials(
            username=username,
            password=password,
            auth_headers=auth_headers,
        )

    def on_state(self, callback: Callable[[DeviceStateMessage], None]) -> None:
        self._state_callbacks.append(callback)

    def on_event(self, callback: Callable[[DeviceEventMessage], None]) -> None:
        self._event_callbacks.append(callback)

    def on_attributes(self, callback: Callable[[DeviceAttributesMessage], None]) -> None:
        self._attributes_callbacks.append(callback)

    def on_location(self, callback: Callable[[DeviceLocationMessage], None]) -> None:
        """Registrer tilbakekall for kvar godteken posisjonslesing."""
        self._location_callbacks.append(callback)

    def on_raw(self, callback: Callable[[str, bytes], None]) -> None:
        """Registrer tilbakekall for alle råe MQTT-meldingar."""
        self._raw_callbacks.append(callback)
        # Kopla til først når nokon lyttar, så vanleg drift ikkje lagar ei ekstra oppgåve per melding.
        self._mqtt.on_raw = self._on_mqtt_raw

    def get_cached_state(self, device_id: str) -> Optional[DeviceStateMessage]:
        return self._state_cache.get(device_id)

    def get_cached_attributes(self, device_id: str) -> Optional[DeviceAttributesMessage]:
        return self._attributes_cache.get(device_id)

    def get_cached_location(self, device_id: str) -> Optional[DeviceLocationMessage]:
        return self._location_cache.get(device_id)

    @staticmethod
    def _dispatch(callbacks: list[Callable[[Any], None]], message: Any, label: str) -> None:
        """Kall kvart tilbakekall; eitt som feilar skal ikkje stoppe dei andre."""
        for callback in list(callbacks):
            try:
                callback(message)
            except Exception:
                _LOGGER.exception(
                    "%s callback failed for %s", label, getattr(message, "device_id", "?")
                )

    async def _on_mqtt_raw(self, topic: str, payload: bytes) -> None:
        for raw_callback in list(self._raw_callbacks):
            try:
                raw_callback(topic, payload)
            except Exception:  # eitt feilande tilbakekall skal ikkje stoppe resten
                _LOGGER.exception("Raw callback failed for topic %s", topic)

    async def _on_mqtt_message(self, topic: str, payload: bytes, device_id: str) -> None:
        try:
            payload_dict = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        parts = topic.split("/")
        if parts and parts[0] == "":
            parts = parts[1:]
        if len(parts) != 5:
            return
        if parts[0] != "downlink" or parts[1] != "vehicle":
            return
        if parts[3] != "realtimeDate":
            return
        channel = parts[4]

        if channel == "location":
            for location_message in self._location_filter.filter(
                parse_location_payload(payload_dict, device_id)
            ):
                if location_message.x is not None and location_message.y is not None:
                    # Framdriftspunkt utan koordinatar skal ikkje overskrive siste posisjon.
                    self._location_cache[device_id] = location_message
                self._dispatch(self._location_callbacks, location_message, "Location")
            return

        if not isinstance(payload_dict, dict):
            return

        payload_dict.setdefault("device_id", device_id)

        if channel == "state":
            state_message = DeviceStateMessage.from_dict(payload_dict)
            cached = self._state_cache.get(device_id)
            if cached is not None:
                # Tilstandskanalen sender delvise meldingar; hald på siste kjende verdiar.
                if extract_battery_value_or_none(payload_dict) is None:
                    state_message.battery = cached.battery
                if state_message.state == "unknown" and not is_recognised_state(
                    state_source(payload_dict)
                ):
                    state_message.state = cached.state
            self._state_cache[device_id] = state_message
            self._dispatch(self._state_callbacks, state_message, "State")
            return
        if channel == "event":
            event_message = DeviceEventMessage.from_dict(payload_dict)
            self._dispatch(self._event_callbacks, event_message, "Event")
            return
        if channel == "attributes":
            attributes_message = DeviceAttributesMessage.from_dict(payload_dict)
            self._attributes_cache[device_id] = attributes_message
            self._dispatch(self._attributes_callbacks, attributes_message, "Attributes")

    def _publish_command(self, message: DeviceCommandMessage) -> None:
        if not self._mqtt.is_connected:
            connection_error = None
            try:
                self._mqtt.connect_async()
            except RuntimeError as exc:
                connection_error = exc
            _LOGGER.error(
                "MQTT not connected, command not sent: %s for device %s",
                message.command,
                message.device_id,
            )
            raise RuntimeError("MQTT not connected") from connection_error
        self._mqtt.publish_command(message.device_id, message.to_dict())
        _LOGGER.debug(
            "Published command %s for device %s",
            message.command,
            message.device_id,
        )

    @property
    def is_connected(self) -> bool:
        return self._mqtt.is_connected

    def start_mowing(self, device_id: str) -> None:
        self._publish_command(
            DeviceCommandMessage(
                id=f"cmd-{uuid.uuid4()}",
                device_id=device_id,
                command="start_mowing",
                params={},
            )
        )

    def pause(self, device_id: str) -> None:
        self._publish_command(
            DeviceCommandMessage(
                id=f"cmd-{uuid.uuid4()}",
                device_id=device_id,
                command="pause",
                params={},
            )
        )

    def return_to_base(self, device_id: str) -> None:
        self._publish_command(
            DeviceCommandMessage(
                id=f"cmd-{uuid.uuid4()}",
                device_id=device_id,
                command="return_to_base",
                params={},
            )
        )

    def set_blade_height(self, device_id: str, height: int) -> None:
        self._publish_command(
            DeviceCommandMessage(
                id=f"cmd-{uuid.uuid4()}",
                device_id=device_id,
                command="set_blade_height",
                params={"height": height},
            )
        )
