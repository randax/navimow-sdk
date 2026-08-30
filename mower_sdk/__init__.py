"""Python SDK for ei skybasert plattform for robotklipparar.

Gjev funksjonar for å snakke med plattforma, inkludert REST-API- og MQTT-støtte.
"""

from mower_sdk.api import MowerAPI
from mower_sdk.client import MowerClient
from mower_sdk.cloud import NavimowCloud
from mower_sdk.device import NavimowCloudDevice
from mower_sdk.errors import (
    COMMAND_ERRORS,
    ERROR_MESSAGES,
    MowerAPIError,
    MowerAuthError,
    MowerMQTTError,
)
from mower_sdk.event import DataEvent
from mower_sdk.http import HTTPClientError, UrllibSession
from mower_sdk.models import (
    Device,
    DeviceAttributesMessage,
    DeviceCommandMessage,
    DeviceEventMessage,
    DeviceLocationMessage,
    DeviceStateMessage,
    DeviceStatus,
    MowerCommand,
    MowerError,
    MowerStatus,
    ThingEventMessage,
    ThingPropertiesMessage,
    ThingStatusMessage,
)
from mower_sdk.mqtt import MowerMQTT, NavimowMQTT
from mower_sdk.navimow import Navimow
from mower_sdk.sdk import NavimowSDK
from mower_sdk.state_manager import StateManager

__version__ = "0.4.0"

__all__ = [
    # Hovudklient
    "MowerClient",
    "Navimow",
    "NavimowSDK",
    # Delmodular
    "MowerAPI",
    "MowerMQTT",
    "NavimowMQTT",
    "NavimowCloud",
    "NavimowCloudDevice",
    "StateManager",
    "DataEvent",
    "UrllibSession",
    # Datamodellar
    "Device",
    "DeviceStateMessage",
    "DeviceEventMessage",
    "DeviceLocationMessage",
    "DeviceAttributesMessage",
    "DeviceCommandMessage",
    "DeviceStatus",
    "MowerStatus",
    "MowerCommand",
    "MowerError",
    "ThingStatusMessage",
    "ThingPropertiesMessage",
    "ThingEventMessage",
    # Unntak
    "MowerAPIError",
    "MowerAuthError",
    "MowerMQTTError",
    "HTTPClientError",
    "ERROR_MESSAGES",
    "COMMAND_ERRORS",
]
