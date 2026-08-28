"""Analyse og sending av sky-MQTT-meldingar."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from mower_sdk.event import DataEvent
from mower_sdk.models import (
    DeviceAttributesMessage,
    DeviceEventMessage,
    DeviceStateMessage,
)
from mower_sdk.mqtt import NavimowMQTT

_LOGGER = logging.getLogger(__name__)


class NavimowCloud:
    """MQTT-sky per konto."""

    def __init__(
        self,
        mqtt_client: NavimowMQTT,
        cloud_client: Any,
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ) -> None:
        self.cloud_client = cloud_client
        self._mqtt_client = mqtt_client
        if loop is not None and loop.is_closed():
            raise RuntimeError("The supplied event loop is closed; pass an open loop")
        if loop is not None and mqtt_client.loop is not None:
            if loop is not mqtt_client.loop:
                raise RuntimeError("Cloud and MQTT clients use different event loops")
        self.loop = loop or mqtt_client.loop
        if self.loop is not None and self.loop.is_closed():
            raise RuntimeError("The supplied event loop is closed; pass an open loop")
        if loop is not None:
            self._mqtt_client.loop = loop
        self._mqtt_client.on_message = self._on_mqtt_message
        self._mqtt_client.on_connected = self.on_connected
        self._mqtt_client.on_disconnected = self.on_disconnected
        self._mqtt_client.on_ready = self.on_ready

        self.mqtt_event_message_event = DataEvent()
        self.mqtt_attributes_event = DataEvent()
        self.mqtt_state_event = DataEvent()
        self.on_ready_event = DataEvent()
        self.on_connected_event = DataEvent()
        self.on_disconnected_event = DataEvent()

    def connect_async(self) -> None:
        self._mqtt_client.connect_async()
        self.loop = self._mqtt_client.loop

    def disconnect(self) -> None:
        self._mqtt_client.disconnect()

    async def on_ready(self) -> None:
        self._require_running_loop()
        await self.on_ready_event.data_event(None)

    async def on_connected(self) -> None:
        self._require_running_loop()
        await self.on_connected_event.data_event(None)

    async def on_disconnected(self) -> None:
        self._require_running_loop()
        await self.on_disconnected_event.data_event(None)

    async def _on_mqtt_message(self, topic: str, payload: bytes, device_id: str) -> None:
        self._require_running_loop()
        try:
            json_str = payload.decode("utf-8")
            payload_dict = json.loads(json_str)
        except (UnicodeDecodeError, json.JSONDecodeError):
            _LOGGER.debug("MQTT payload not json: topic=%s", topic)
            return

        if isinstance(payload_dict, dict):
            payload_dict.setdefault("device_id", device_id)
        await self._parse_mqtt_response(topic, payload_dict)

    def _require_running_loop(self) -> asyncio.AbstractEventLoop:
        running_loop = asyncio.get_running_loop()
        if self.loop is None:
            self.loop = running_loop
        elif self.loop is not running_loop:
            raise RuntimeError("This SDK object is being used from a different event loop")
        if self.loop.is_closed():
            raise RuntimeError("The SDK event loop is closed")
        self._mqtt_client._bind_loop(self.loop)
        return self.loop

    async def _parse_mqtt_response(self, topic: str, payload: dict[str, Any]) -> None:
        """Tolk MQTT-svar etter emnekanal og send datahendingar."""
        parts = topic.split("/")
        if len(parts) != 3 or parts[0] != "navimow":
            return
        channel = parts[2]
        if channel == "state":
            state = DeviceStateMessage.from_dict(payload)
            await self.mqtt_state_event.data_event(state)
            return
        if channel == "event":
            event = DeviceEventMessage.from_dict(payload)
            await self.mqtt_event_message_event.data_event(event)
            return
        if channel == "attributes":
            attrs = DeviceAttributesMessage.from_dict(payload)
            await self.mqtt_attributes_event.data_event(attrs)
