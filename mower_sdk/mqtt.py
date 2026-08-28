"""MQTT-klientar for Navimow-SDK-en."""

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Literal, Optional, Tuple, cast
from urllib.parse import urlparse

from paho.mqtt import client as mqtt_client

from mower_sdk.errors import ERROR_MESSAGES, MowerMQTTError
from mower_sdk.models import Device, DeviceStatus
from mower_sdk.utils import parse_json

_LOGGER = logging.getLogger(__name__)


def _build_web_client_id(username: Optional[str]) -> str:
    base = username or "unknown"
    rand = uuid.uuid4().hex[:10]
    return f"web_{base}_{rand}"


def _mask_secret(value: Optional[str]) -> str:
    if not value:
        return "<empty>"
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}***{value[-2:]}"


def _format_auth_headers(headers: Optional[dict[str, str]]) -> str:
    if not headers:
        return "<none>"
    safe = {}
    for key, val in headers.items():
        if key.lower() == "authorization":
            safe[key] = _mask_secret(val)
        else:
            safe[key] = val
    return str(safe)


def _get_running_loop_if_available() -> Optional[asyncio.AbstractEventLoop]:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _validate_event_loop(
    loop: Optional[asyncio.AbstractEventLoop], owner: str
) -> Optional[asyncio.AbstractEventLoop]:
    if loop is not None and loop.is_closed():
        raise RuntimeError(f"{owner} loop is closed")
    return loop


def _bind_event_loop(
    bound_loop: Optional[asyncio.AbstractEventLoop],
    requested_loop: Optional[asyncio.AbstractEventLoop] = None,
    owner: str = "SDK object",
    missing_message: Optional[str] = None,
) -> asyncio.AbstractEventLoop:
    if bound_loop is not None and requested_loop is not None:
        if bound_loop is not requested_loop:
            raise RuntimeError(f"{owner} is bound to a different event loop")

    loop = _validate_event_loop(requested_loop or bound_loop, owner)
    running_loop = _get_running_loop_if_available()

    if loop is None:
        if running_loop is None:
            raise RuntimeError(
                missing_message
                or f"{owner} requires a running event loop or an explicit loop= argument"
            )
        loop = running_loop
    elif running_loop is not None and running_loop is not loop:
        raise RuntimeError(f"{owner} is bound to a different event loop")

    return loop


def _reason_code_is_failure(reason_code: Any) -> bool:
    return bool(getattr(reason_code, "is_failure", reason_code != 0))


def _call_soon_threadsafe(
    loop: asyncio.AbstractEventLoop,
    callback: Callable[..., Any],
    *args: Any,
) -> bool:
    if loop.is_closed():
        _LOGGER.warning("Event loop is closed; dropping late MQTT callback")
        return False
    try:
        loop.call_soon_threadsafe(callback, *args)
    except RuntimeError:
        _LOGGER.warning("Event loop rejected a late MQTT callback", exc_info=True)
        return False
    return True


def _close_awaitable(awaitable: Awaitable[Any]) -> None:
    close = getattr(awaitable, "close", None)
    if callable(close):
        close()


class MowerMQTT:
    """MQTT-klient.

    Gjev funksjonar for MQTT-tilkopling, abonnement og oppdatering av einingsstatus,
    med støtte for både synkrone og asynkrone grensesnitt.

    Eigenskapar:
        broker: MQTT-meglaradresse
        port: MQTT-port
        username: MQTT-brukarnamn (valfritt)
        password: MQTT-passord (valfritt)
        status_cache: Mellombels lager for einingsstatus
        _async_client: Asynkron MQTT-klient
        _sync_client: Synkron MQTT-klient
        _callbacks: Ordbok med tilbakekall
    """

    def __init__(
        self,
        broker: str,
        port: int = 1883,
        username: Optional[str] = None,
        password: Optional[str] = None,
        ws_path: Optional[str] = None,
        auth_headers: Optional[dict[str, str]] = None,
        keepalive_seconds: int = 2400,
        reconnect_min_delay: int = 1,
        reconnect_max_delay: int = 60,
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ) -> None:
        """Initialiser MQTT-klienten.

        Parametrar:
            broker: Adresse til MQTT-meglaren
            port: MQTT-porten
            username: MQTT-brukarnamn (valfritt)
            password: MQTT-passord (valfritt)
        """
        self.broker = broker
        self.port = port
        self.username = username
        self.password = password
        self.ws_path = ws_path
        self.auth_headers = auth_headers
        # KeepAlive er MQTT-lagsnivået si livhaldssignalering og kjem før app-nivå hjarteslag.
        # Standardverdien på 40 minutt held oppe trafikk før broker eller lastbalanserar kuttar ei stille økt.
        self.keepalive_seconds = max(30, int(keepalive_seconds))
        self.reconnect_min_delay = max(0, int(reconnect_min_delay))
        self.reconnect_max_delay = max(self.reconnect_min_delay, int(reconnect_max_delay))
        self._use_tls = bool(ws_path)
        self._client_id = _build_web_client_id(self.username)
        self._loop = _validate_event_loop(loop, "MowerMQTT")
        self.status_cache: dict[str, DeviceStatus] = {}
        self._async_client: Optional[mqtt_client.Client] = None
        self._sync_client: Optional[mqtt_client.Client] = None
        self._async_stop_event: Optional[asyncio.Event] = None
        self._callbacks: dict[str, dict[str, Optional[Callable[..., Any]]]] = {}
        self._connected = False

    @property
    def loop(self) -> Optional[asyncio.AbstractEventLoop]:
        return self._loop

    def configure_wss(
        self,
        mqtt_host: str,
        mqtt_url: str,
        username: Optional[str],
        password: Optional[str],
        auth_headers: Optional[dict[str, str]],
        port: int = 443,
    ) -> None:
        """Set opp WSS-tilkopling."""
        parsed = urlparse(mqtt_host)
        host = parsed.hostname or mqtt_host
        self.broker = host
        self.port = parsed.port or port
        self.ws_path = mqtt_url
        self.username = username
        self.password = password
        self.auth_headers = auth_headers
        self._use_tls = True

    def _build_client(self) -> mqtt_client.Client:
        transport: Literal["websockets", "tcp"] = "websockets" if self.ws_path else "tcp"
        client = mqtt_client.Client(
            callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2,
            client_id=self._client_id,
            transport=transport,
        )
        if self.username and self.password:
            client.username_pw_set(self.username, self.password)
        if self.ws_path:
            client.ws_set_options(path=self.ws_path, headers=self.auth_headers or {})
        if self._use_tls:
            client.tls_set()
        # Automatisk attkopling brukar denne tilbakefallsstrategien når paho køyrer i bakgrunnssløyfa.
        client.reconnect_delay_set(
            min_delay=self.reconnect_min_delay, max_delay=self.reconnect_max_delay
        )
        _LOGGER.debug(
            "MQTT client built: transport=%s broker=%s port=%s ws_path=%s tls=%s client_id=%s",
            transport,
            self.broker,
            self.port,
            self.ws_path,
            self._use_tls,
            self._client_id,
        )
        return client

    def _get_status_topic(self, device_id: str) -> str:
        """Hent topic for einingstilstand."""
        # TODO: Tilpass etter faktisk MQTT-emneformat.
        return f"device/{device_id}/status"

    def _get_event_topic(self, device_id: str) -> str:
        """Hent topic for einingshendingar."""
        # TODO: Tilpass etter faktisk MQTT-emneformat.
        return f"device/{device_id}/event"

    async def async_connect(self) -> None:
        """Klargjer MQTT-klienten for asynkron bruk."""
        self._loop = _bind_event_loop(
            self._loop,
            owner="MowerMQTT",
            missing_message="MowerMQTT.async_connect() requires a running event loop or an explicit loop= argument",
        )
        try:
            # Sjølve tilkoplinga skjer ved abonnement; her stadfestar vi berre at løkka er gyldig.
            self._connected = True
        except Exception as e:
            raise MowerMQTTError(f"{ERROR_MESSAGES['MQTT_CONNECTION_FAILED']}: {str(e)}") from e

    def connect(self) -> None:
        """Kople til MQTT-broker synkront."""
        try:
            self._sync_client = self._build_client()
            _LOGGER.info(
                "MQTT connect details (sync): transport=%s broker=%s port=%s ws_path=%s tls=%s username=%s auth_headers=%s",
                "websockets" if self.ws_path else "tcp",
                self.broker,
                self.port,
                self.ws_path,
                self._use_tls,
                _mask_secret(self.username),
                _format_auth_headers(self.auth_headers),
            )

            def on_connect(client, userdata, flags, reason_code, properties=None):
                if not _reason_code_is_failure(reason_code):
                    self._connected = True
                    _LOGGER.info(
                        "MQTT connected (sync): broker=%s port=%s",
                        self.broker,
                        self.port,
                    )
                else:
                    raise MowerMQTTError(
                        f"{ERROR_MESSAGES['MQTT_CONNECTION_FAILED']}: Return code {reason_code}"
                    )

            self._sync_client.on_connect = on_connect
            _LOGGER.info(
                "MQTT connecting (sync): broker=%s port=%s ws_path=%s",
                self.broker,
                self.port,
                self.ws_path,
            )
            self._sync_client.connect(self.broker, self.port, self.keepalive_seconds)
            self._sync_client.loop_start()
        except Exception as e:
            raise MowerMQTTError(f"{ERROR_MESSAGES['MQTT_CONNECTION_FAILED']}: {str(e)}") from e

    async def async_subscribe_device(
        self,
        device_id: str,
        on_status_update: Optional[Callable[[DeviceStatus], None]] = None,
        on_event: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> None:
        """Abonner asynkront på status og hendingar for ei eining."""
        status_topic = self._get_status_topic(device_id)
        event_topic = self._get_event_topic(device_id)

        # Ta vare på tilbakekall for denne eininga.
        self._callbacks[device_id] = {
            "status": on_status_update,
            "event": on_event,
        }

        self._loop = _bind_event_loop(
            self._loop,
            owner="MowerMQTT",
            missing_message="MowerMQTT.async_subscribe_device() requires a running event loop or an explicit loop= argument",
        )
        loop = self._loop
        self._async_stop_event = asyncio.Event()
        try:
            self._async_client = self._build_client()
            _LOGGER.info(
                "MQTT connect details (async): transport=%s broker=%s port=%s ws_path=%s tls=%s username=%s auth_headers=%s device=%s",
                "websockets" if self.ws_path else "tcp",
                self.broker,
                self.port,
                self.ws_path,
                self._use_tls,
                _mask_secret(self.username),
                _format_auth_headers(self.auth_headers),
                device_id,
            )

            def on_connect(_client, _userdata, _flags, reason_code, _properties=None) -> None:
                if _reason_code_is_failure(reason_code):
                    _LOGGER.error("MQTT connection failed: rc=%s", reason_code)
                    return
                self._connected = True
                _LOGGER.info(
                    "MQTT connected (async): broker=%s port=%s device=%s",
                    self.broker,
                    self.port,
                    device_id,
                )
                _LOGGER.info(
                    "MQTT subscribing (async): %s, %s",
                    status_topic,
                    event_topic,
                )
                _client.subscribe(status_topic)
                _client.subscribe(event_topic)

            def on_message(_client, _userdata, msg) -> None:
                try:
                    payload_text = (msg.payload or b"").decode("utf-8", errors="replace")
                    _LOGGER.debug(
                        "MQTT payload (async): topic=%s payload=%s",
                        msg.topic,
                        payload_text,
                    )
                    payload = cast(dict[str, Any], parse_json(msg.payload))
                    topic = msg.topic
                    _LOGGER.debug(
                        "MQTT message (async): topic=%s bytes=%d device=%s",
                        topic,
                        len(msg.payload or b""),
                        device_id,
                    )
                    if topic == status_topic:
                        status = DeviceStatus.from_dict(payload)
                        self.status_cache[device_id] = status
                        callback = self._callbacks.get(device_id, {}).get("status")
                        if callback:
                            _call_soon_threadsafe(loop, callback, status)
                    elif topic == event_topic:
                        callback = self._callbacks.get(device_id, {}).get("event")
                        if callback:
                            _call_soon_threadsafe(loop, callback, payload)
                except Exception as e:
                    _LOGGER.exception("Error processing MQTT message: %s", e)

            def on_disconnect(_client, _userdata, *disconnect_args) -> None:
                _LOGGER.debug(
                    "MQTT disconnected (async): broker=%s port=%s device=%s",
                    self.broker,
                    self.port,
                    device_id,
                )
                if self._async_stop_event:
                    _call_soon_threadsafe(loop, self._async_stop_event.set)

            self._async_client.on_connect = on_connect
            self._async_client.on_message = on_message
            self._async_client.on_disconnect = on_disconnect
            _LOGGER.info(
                "MQTT connecting (async): broker=%s port=%s ws_path=%s device=%s",
                self.broker,
                self.port,
                self.ws_path,
                device_id,
            )
            self._async_client.connect(self.broker, self.port, self.keepalive_seconds)
            self._async_client.loop_start()

            await self._async_stop_event.wait()
        except Exception as e:
            raise MowerMQTTError(f"{ERROR_MESSAGES['MQTT_SUBSCRIBE_FAILED']}: {str(e)}") from e

    def subscribe_device(
        self,
        device_id: str,
        on_status_update: Optional[Callable[[DeviceStatus], None]] = None,
        on_event: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> None:
        """Abonner synkront på status og hendingar for ei eining."""
        if not self._sync_client:
            self.connect()
        assert self._sync_client is not None

        status_topic = self._get_status_topic(device_id)
        event_topic = self._get_event_topic(device_id)

        def on_message(client, userdata, msg):
            try:
                payload_text = (msg.payload or b"").decode("utf-8", errors="replace")
                _LOGGER.debug(
                    "MQTT payload (sync): topic=%s payload=%s",
                    msg.topic,
                    payload_text,
                )
                payload = parse_json(msg.payload)
                topic = msg.topic
                _LOGGER.debug(
                    "MQTT message (sync): topic=%s bytes=%d device=%s",
                    topic,
                    len(msg.payload or b""),
                    device_id,
                )

                if topic == status_topic:
                    # Handsam statusoppdatering.
                    status = DeviceStatus.from_dict(payload)
                    self.status_cache[device_id] = status

                    if on_status_update:
                        on_status_update(status)

                elif topic == event_topic:
                    # Handsam hending.
                    if on_event:
                        on_event(payload)

            except Exception as e:
                # Logg feilen, men hald fram med handsaminga
                print(f"Error processing MQTT message: {e}")

        try:
            self._sync_client.on_message = on_message
            _LOGGER.info(
                "MQTT subscribing (sync): %s, %s",
                status_topic,
                event_topic,
            )
            self._sync_client.subscribe(status_topic)
            self._sync_client.subscribe(event_topic)

            # Ta vare på tilbakekall for seinare meldingar.
            self._callbacks[device_id] = {
                "status": on_status_update,
                "event": on_event,
            }
        except Exception as e:
            raise MowerMQTTError(f"{ERROR_MESSAGES['MQTT_SUBSCRIBE_FAILED']}: {str(e)}") from e

    def get_cached_status(self, device_id: str) -> Optional[DeviceStatus]:
        """Hent bufra einingstilstand."""
        return self.status_cache.get(device_id)

    async def async_disconnect(self) -> None:
        """Bryt MQTT-tilkopling asynkront."""
        self._loop = _bind_event_loop(
            self._loop,
            owner="MowerMQTT",
            missing_message="MowerMQTT.async_disconnect() requires a running event loop or an explicit loop= argument",
        )
        if self._async_client:
            self._async_client.loop_stop()
            self._async_client.disconnect()
        if self._async_stop_event:
            self._async_stop_event.set()
        self._connected = False
        self._async_client = None

    def disconnect(self) -> None:
        """Bryt MQTT-tilkopling synkront."""
        if self._sync_client:
            self._sync_client.loop_stop()
            self._sync_client.disconnect()
            self._connected = False


class NavimowMQTT:
    """Navimow-MQTT-klient for skyemne."""

    def __init__(
        self,
        broker: str,
        port: int,
        username: Optional[str],
        password: Optional[str],
        records: list[Device],
        ws_path: Optional[str] = None,
        auth_headers: Optional[dict[str, str]] = None,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        keepalive_seconds: int = 2400,
        reconnect_min_delay: int = 1,
        reconnect_max_delay: int = 60,
    ) -> None:
        parsed = urlparse(broker)
        self.broker = parsed.hostname or broker
        self.port = parsed.port or port
        self.username = username
        self.password = password
        self.records = records
        self.loop = _validate_event_loop(loop, "NavimowMQTT")
        self.ws_path = ws_path
        self.auth_headers = auth_headers
        self._use_tls = bool(ws_path) or parsed.scheme == "wss"
        self._client_id = _build_web_client_id(self.username)
        self.keepalive_seconds = max(30, int(keepalive_seconds))
        self.reconnect_min_delay = max(0, int(reconnect_min_delay))
        self.reconnect_max_delay = max(self.reconnect_min_delay, int(reconnect_max_delay))

        self.on_connected: Optional[Callable[[], Awaitable[None]]] = None
        self.on_ready: Optional[Callable[[], Awaitable[None]]] = None
        self.on_message: Optional[Callable[[str, bytes, str], Awaitable[None]]] = None
        self.on_disconnected: Optional[Callable[[], Awaitable[None]]] = None

        transport: Literal["websockets", "tcp"] = "websockets" if self.ws_path else "tcp"
        self.client = mqtt_client.Client(
            callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2,
            client_id=self._client_id,
            transport=transport,
        )
        if self.username and self.password:
            self.client.username_pw_set(self.username, self.password)
        if self.ws_path:
            self.client.ws_set_options(path=self.ws_path, headers=self.auth_headers or {})
        if self._use_tls:
            self.client.tls_set()
        self.client.reconnect_delay_set(
            min_delay=self.reconnect_min_delay, max_delay=self.reconnect_max_delay
        )

        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        _LOGGER.info(
            "NavimowMQTT init: broker=%s port=%s ws_path=%s tls=%s client_id=%s",
            self.broker,
            self.port,
            self.ws_path,
            self._use_tls,
            self._client_id,
        )

    @property
    def is_connected(self) -> bool:
        return self.client.is_connected()

    def _bind_loop(
        self,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        missing_message: Optional[str] = None,
    ) -> asyncio.AbstractEventLoop:
        self.loop = _bind_event_loop(
            self.loop,
            loop,
            owner="NavimowMQTT",
            missing_message=missing_message
            or "NavimowMQTT.connect_async() requires a running event loop or an explicit loop= argument",
        )
        return self.loop

    def _build_new_client(self) -> mqtt_client.Client:
        """Bygg paho-klienten på nytt med oppdaterte legitimasjonar og val."""
        transport: Literal["websockets", "tcp"] = "websockets" if self.ws_path else "tcp"
        client = mqtt_client.Client(
            callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2,
            client_id=self._client_id,
            transport=transport,
        )
        if self.username and self.password:
            client.username_pw_set(self.username, self.password)
        if self.ws_path:
            client.ws_set_options(path=self.ws_path, headers=self.auth_headers or {})
        if self._use_tls:
            client.tls_set()
        client.reconnect_delay_set(
            min_delay=self.reconnect_min_delay, max_delay=self.reconnect_max_delay
        )
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        return client

    def update_credentials(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        auth_headers: Optional[dict[str, str]] = None,
    ) -> None:
        """Oppdater MQTT-legitimasjonar og bygg klienten opp att ved behov."""
        changed = False
        if username is not None and username != self.username:
            self.username = username
            changed = True
        if password is not None and password != self.password:
            self.password = password
            changed = True
        if auth_headers is not None and auth_headers != self.auth_headers:
            self.auth_headers = auth_headers
            changed = True

        if not changed:
            return

        if self.client.is_connected():
            # Ei aktiv tilkopling held fram; nye legitimasjonar blir brukte ved neste attkopling.
            # Vi koplar ikkje frå ved tokenfornying; det hindrar attkopling og «eining utilgjengeleg».
            _LOGGER.info(
                "NavimowMQTT credentials updated while connected (will apply on next reconnect): broker=%s port=%s",
                self.broker,
                self.port,
            )
            return

        # Bygg klienten opp att med ein gong, men vent med tilkoplinga til løkka er bunden.
        reconnect = self.loop is not None
        if reconnect:
            _LOGGER.info(
                "NavimowMQTT credentials updated while disconnected, rebuilding and reconnecting: broker=%s port=%s",
                self.broker,
                self.port,
            )
        else:
            _LOGGER.info(
                "NavimowMQTT credentials updated before loop binding, rebuilding without reconnecting: broker=%s port=%s",
                self.broker,
                self.port,
            )
        try:
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass

        self.client = self._build_new_client()
        if reconnect:
            self.connect_async()

    def connect_async(self) -> None:
        self._bind_loop()
        if not self.is_connected:
            _LOGGER.info(
                "NavimowMQTT connect details: transport=%s broker=%s port=%s ws_path=%s tls=%s username=%s auth_headers=%s",
                "websockets" if self.ws_path else "tcp",
                self.broker,
                self.port,
                self.ws_path,
                self._use_tls,
                _mask_secret(self.username),
                _format_auth_headers(self.auth_headers),
            )
            _LOGGER.info(
                "NavimowMQTT connecting: broker=%s port=%s ws_path=%s",
                self.broker,
                self.port,
                self.ws_path,
            )
            self.client.connect_async(self.broker, self.port, self.keepalive_seconds)
            self.client.loop_start()

    def disconnect(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()
        _LOGGER.info(
            "NavimowMQTT disconnect requested: broker=%s port=%s",
            self.broker,
            self.port,
        )

    def _get_device_ids(self) -> list[str]:
        device_ids: list[str] = []
        for device in self.records:
            device_id = getattr(device, "id", None)
            if device_id:
                device_ids.append(device_id)
        return device_ids

    def subscribe_all(self, product_key: str, device_name: str) -> None:
        device_ids = self._get_device_ids()
        if not device_ids:
            _LOGGER.warning(
                "NavimowMQTT subscribing cloud topics with wildcard: no device ids available"
            )
            self.client.subscribe("/downlink/vehicle/+/realtimeDate/state")
            self.client.subscribe("/downlink/vehicle/+/realtimeDate/event")
            self.client.subscribe("/downlink/vehicle/+/realtimeDate/attributes")
            return

        _LOGGER.info("NavimowMQTT subscribing cloud topics for %d device(s)", len(device_ids))
        for device_id in device_ids:
            self.client.subscribe(f"/downlink/vehicle/{device_id}/realtimeDate/state")
            self.client.subscribe(f"/downlink/vehicle/{device_id}/realtimeDate/event")
            self.client.subscribe(f"/downlink/vehicle/{device_id}/realtimeDate/attributes")

    def unsubscribe_all(self, product_key: str, device_name: str) -> None:
        device_ids = self._get_device_ids()
        if not device_ids:
            _LOGGER.info("NavimowMQTT unsubscribing cloud topics (wildcard)")
            self.client.unsubscribe("/downlink/vehicle/+/realtimeDate/state")
            self.client.unsubscribe("/downlink/vehicle/+/realtimeDate/event")
            self.client.unsubscribe("/downlink/vehicle/+/realtimeDate/attributes")
            return

        _LOGGER.info("NavimowMQTT unsubscribing cloud topics for %d device(s)", len(device_ids))
        for device_id in device_ids:
            self.client.unsubscribe(f"/downlink/vehicle/{device_id}/realtimeDate/state")
            self.client.unsubscribe(f"/downlink/vehicle/{device_id}/realtimeDate/event")
            self.client.unsubscribe(f"/downlink/vehicle/{device_id}/realtimeDate/attributes")

    def _schedule(self, coro: Awaitable[None]) -> None:
        if self.loop is None:
            _close_awaitable(coro)
            _LOGGER.debug("Event loop not running, skip scheduling MQTT callback")
            return
        if not _call_soon_threadsafe(self.loop, asyncio.create_task, coro):
            _close_awaitable(coro)

    def _on_connect(self, _client, _userdata, _flags, reason_code, _properties=None) -> None:
        if _reason_code_is_failure(reason_code):
            _LOGGER.error("MQTT connection failed: rc=%s", reason_code)
            return
        _LOGGER.info(
            "NavimowMQTT connected: broker=%s port=%s",
            self.broker,
            self.port,
        )
        self.subscribe_all("", "")

        if self.on_connected is not None:
            self._schedule(self.on_connected())
        if self.on_ready is not None:
            self._schedule(self.on_ready())

    def _on_disconnect(
        self, _client, _userdata, _flags, reason_code=None, _properties=None
    ) -> None:
        _LOGGER.debug(
            "NavimowMQTT disconnected: broker=%s port=%s rc=%s",
            self.broker,
            self.port,
            reason_code,
        )
        if self.on_disconnected is not None:
            self._schedule(self.on_disconnected())

    def _parse_topic(self, topic: str) -> Tuple[Optional[str], Optional[str]]:
        parts = topic.split("/")
        if parts and parts[0] == "":
            parts = parts[1:]
        if len(parts) != 5:
            return None, None
        if parts[0] != "downlink" or parts[1] != "vehicle":
            return None, None
        if parts[3] != "realtimeDate":
            return None, None
        return parts[2], parts[4]

    def _on_message(self, _client, _userdata, msg) -> None:
        topic = msg.topic
        device_id, _channel = self._parse_topic(topic)

        payload_bytes = msg.payload
        _LOGGER.debug(
            "NavimowMQTT payload: topic=%s payload=%s",
            topic,
            (payload_bytes or b"").decode("utf-8", errors="replace"),
        )
        _LOGGER.debug(
            "NavimowMQTT message: topic=%s bytes=%d device=%s",
            topic,
            len(payload_bytes or b""),
            device_id,
        )
        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            payload = None

        if isinstance(payload, dict) and device_id:
            payload.setdefault("device_id", device_id)
            payload_bytes = json.dumps(payload).encode("utf-8")

        if self.on_message is not None and device_id:
            self._schedule(self.on_message(topic, payload_bytes, device_id))

    def publish_command(self, device_id: str, payload: dict[str, Any]) -> None:
        topic = f"navimow/{device_id}/command"
        self.client.publish(topic, json.dumps(payload))
