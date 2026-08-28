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
    DeviceStateMessage,
)
from mower_sdk.mqtt import NavimowMQTT

_LOGGER = logging.getLogger(__name__)


class NavimowSDK:
    """SDK-fasade for MQTT-styrt integrasjon.

    Merknader:
        - on_state/on_event/on_attributes-tilbakekall er synkrone.
        - Tilbakekalla blir køyrde frå MQTT-tråden eller event loop-konteksten.
          Home Assistant må byte til hass-løkka via call_soon_threadsafe eller
          run_coroutine_threadsafe.
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
        )
        self._loop = self._mqtt.loop
        self._mqtt.on_message = self._on_mqtt_message

        self._state_callbacks: list[Callable[[DeviceStateMessage], None]] = []
        self._event_callbacks: list[Callable[[DeviceEventMessage], None]] = []
        self._attributes_callbacks: list[Callable[[DeviceAttributesMessage], None]] = []

        self._state_cache: dict[str, DeviceStateMessage] = {}
        self._attributes_cache: dict[str, DeviceAttributesMessage] = {}

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

    def get_cached_state(self, device_id: str) -> Optional[DeviceStateMessage]:
        return self._state_cache.get(device_id)

    def get_cached_attributes(self, device_id: str) -> Optional[DeviceAttributesMessage]:
        return self._attributes_cache.get(device_id)

    async def _on_mqtt_message(self, topic: str, payload: bytes, device_id: str) -> None:
        try:
            payload_dict = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(payload_dict, dict):
            return

        payload_dict.setdefault("device_id", device_id)
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

        if channel == "state":
            state_message = DeviceStateMessage.from_dict(payload_dict)
            self._state_cache[state_message.device_id] = state_message
            for state_callback in list(self._state_callbacks):
                state_callback(state_message)
            return
        if channel == "event":
            event_message = DeviceEventMessage.from_dict(payload_dict)
            for event_callback in list(self._event_callbacks):
                event_callback(event_message)
            return
        if channel == "attributes":
            attributes_message = DeviceAttributesMessage.from_dict(payload_dict)
            self._attributes_cache[attributes_message.device_id] = attributes_message
            for attributes_callback in list(self._attributes_callbacks):
                attributes_callback(attributes_message)

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
