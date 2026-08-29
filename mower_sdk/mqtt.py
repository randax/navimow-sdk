"""MQTT-klientar for Navimow-SDK-en."""

import asyncio
import json
import logging
import threading
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Literal, Optional, Tuple
from urllib.parse import urlparse

from paho.mqtt import client as mqtt_client

from mower_sdk.errors import ERROR_MESSAGES, MowerMQTTError
from mower_sdk.models import (
    Device,
    DeviceLocationMessage,
    DeviceStateMessage,
    DeviceStatus,
    LocationFilter,
    parse_location_payload,
)
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
    return str({key: "<redacted>" for key in headers})


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


def _log_teardown_result(future: "asyncio.Future[Any]") -> None:
    if not future.cancelled() and future.exception() is not None:
        _LOGGER.debug("Background MQTT client teardown raised: %s", future.exception())


class _SessionEvent(asyncio.Event):
    """Stopp-hending for éi asynkron økt; `replaced` seier at økta vart bytt ut, ikkje broten."""

    replaced: bool = False


def _stop_paho_client(client: Any) -> None:
    """Stopp ein paho-klient: disconnect() først så nettverkstråden avsluttar sjølv og
    lukkar socketen, deretter loop_stop() som ventar på tråden."""
    try:
        client.disconnect()
    finally:
        client.loop_stop()


def _validate_topics(topics: Optional[list[str]]) -> list[str]:
    """Kontroller ekstra emne tidleg, så feil ikkje først dukkar opp på MQTT-tråden."""
    result: list[str] = []
    for topic in topics or []:
        if (
            not isinstance(topic, str)
            or not topic
            or "\x00" in topic
            or len(topic.encode("utf-8")) > 65535
        ):
            raise ValueError(f"Invalid MQTT topic in extra_topics: {topic!r}")
        result.append(topic)
    return result


def _parse_realtime_topic(topic: str) -> Tuple[Optional[str], Optional[str]]:
    """Tolk `/downlink/vehicle/{id}/realtimeDate/{kanal}` til (einings-ID, kanal)."""
    parts = topic.split("/")
    if parts and parts[0] == "":
        parts = parts[1:]
    if len(parts) != 5:
        return None, None
    if parts[0] != "downlink" or parts[1] != "vehicle" or parts[3] != "realtimeDate":
        return None, None
    return parts[2], parts[4]


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
        location_cache: Siste posisjonslesing med koordinatar per eining
        subscribe_location: Om posisjonskanalen skal abonnerast (frivillig)
        extra_topics: Ekstra emne som blir abonnerte ordrett
        on_raw: Synkront tilbakekall for alle råe meldingar
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
        subscribe_location: bool = False,
        extra_topics: Optional[list[str]] = None,
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
        self.location_cache: dict[str, DeviceLocationMessage] = {}
        self.subscribe_location = subscribe_location
        self.extra_topics = _validate_topics(extra_topics)
        self._location_filter = LocationFilter()
        # Både den synkrone og den asynkrone paho-tråden kan handsame meldingar samstundes.
        self._state_lock = threading.Lock()
        self._location_pass_done = False
        self._sync_signature: Optional[tuple[Any, ...]] = None
        self.on_raw: Optional[Callable[[str, bytes], None]] = None
        self._async_client: Optional[mqtt_client.Client] = None
        self._sync_client: Optional[mqtt_client.Client] = None
        self._async_stop_event: Optional[_SessionEvent] = None
        self._async_connected = False
        self._async_session_was_connected = False
        self._async_location_pass_done = False
        self._async_waiters = 0
        self._async_session_lock: Optional[asyncio.Lock] = None
        self._async_owned_devices: set[str] = set()
        self._sync_owned_devices: set[str] = set()
        self._async_signature: Optional[tuple[Any, ...]] = None
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
        return f"/downlink/vehicle/{device_id}/realtimeDate/state"

    def _get_event_topic(self, device_id: str) -> str:
        """Hent topic for einingshendingar."""
        return f"/downlink/vehicle/{device_id}/realtimeDate/event"

    def _get_location_topic(self, device_id: str) -> str:
        """Hent topic for einingsposisjon."""
        return f"/downlink/vehicle/{device_id}/realtimeDate/location"

    def _device_topics(self, device_id: str) -> list[str]:
        """Emna som skal abonnerast for ei eining, etter gjeldande innstillingar."""
        topics = [self._get_status_topic(device_id), self._get_event_topic(device_id)]
        if self.subscribe_location:
            topics.append(self._get_location_topic(device_id))
        return topics

    def _subscribe_topics(self, client: mqtt_client.Client, device_ids: list[str]) -> None:
        """Abonner på alle emne for dei oppgjevne einingane pluss ekstra emne."""
        topics: list[str] = []
        for device_id in device_ids:
            topics.extend(self._device_topics(device_id))
        topics.extend(self.extra_topics)
        _LOGGER.info("MQTT subscribing: %s", ", ".join(topics))
        for topic in topics:
            client.subscribe(topic)

    def _handle_message(
        self,
        topic: str,
        raw_payload: bytes,
        loop: Optional[asyncio.AbstractEventLoop],
    ) -> None:
        """Handsam éi melding; planlegg tilbakekall på løkka, eller kall direkte utan løkke.

        Einings-ID og kanal blir lesne frå emnet, så same handsamar tener alle einingar.
        """

        def dispatch(callback: Callable[..., Any], *args: Any) -> None:
            if loop is None:
                try:
                    callback(*args)
                except Exception:  # eitt feilande tilbakekall skal ikkje stoppe resten
                    _LOGGER.exception("MQTT callback failed for topic %s", topic)
            else:
                _call_soon_threadsafe(loop, callback, *args)

        on_raw = self.on_raw  # augneblinksbilete: kan bli nullstilt frå ein annan tråd
        if on_raw:
            dispatch(on_raw, topic, raw_payload)

        device_id, channel = _parse_realtime_topic(topic)
        if device_id is None or channel is None:
            return
        with self._state_lock:
            callbacks = self._callbacks.get(device_id)
        if callbacks is None:
            _LOGGER.debug("MQTT message for unregistered device: topic=%s", topic)
            return
        try:
            payload = parse_json(raw_payload)
        except (TypeError, ValueError, UnicodeDecodeError):
            _LOGGER.warning("Could not parse MQTT message: topic=%s", topic)
            return

        if channel == "state":
            if not isinstance(payload, dict):
                return
            state_payload = dict(payload)
            state_payload.setdefault("device_id", device_id)
            message = DeviceStateMessage.from_dict(state_payload)
            with self._state_lock:
                cached = self.status_cache.get(device_id)
                status = DeviceStatus.from_state_message(
                    message,
                    fallback_status=cached.status if cached else None,
                    fallback_battery=cached.battery if cached else None,
                )
                self.status_cache[device_id] = status
            callback = callbacks.get("status")
            if callback:
                dispatch(callback, status)
            return
        if channel == "event":
            callback = callbacks.get("event")
            if callback and isinstance(payload, dict):
                dispatch(callback, payload)
            return
        if channel == "location":
            callback = callbacks.get("location")
            with self._state_lock:
                # Filter og mellomlager under lås: vassmerket må ikkje gå bakover når to
                # paho-trådar leverer samstundes.
                points = self._location_filter.filter(parse_location_payload(payload, device_id))
                for point in points:
                    # Mellomlageret held siste lesing MED koordinatar, uavhengig av `type`:
                    # vokabularet for `type` er ikkje kjent, så koordinatane er det einaste
                    # trygge kriteriet.
                    if point.x is not None and point.y is not None:
                        self.location_cache[device_id] = point
            if callback:
                for point in points:
                    dispatch(callback, point)

    def _make_on_message(
        self,
        loop_getter: Callable[[], Optional[asyncio.AbstractEventLoop]],
        label: str,
    ) -> Callable[..., None]:
        """Lag paho-handsamaren. Løkka blir slått opp per melding, så ei løkke som blir
        bunden etter at klienten vart bygd, òg blir brukt."""

        def on_message(_client: Any, _userdata: Any, msg: Any) -> None:
            try:
                loop = loop_getter()
                raw_payload = msg.payload or b""
                _LOGGER.debug(
                    "MQTT message (%s): topic=%s bytes=%d payload=%s",
                    label,
                    msg.topic,
                    len(raw_payload),
                    raw_payload.decode("utf-8", errors="replace"),
                )
                self._handle_message(msg.topic, raw_payload, loop)
            except Exception as e:
                _LOGGER.exception("Error processing MQTT message: %s", e)

        return on_message

    async def async_connect(self) -> None:
        """Klargjer MQTT-klienten for asynkron bruk."""
        self._loop = _bind_event_loop(
            self._loop,
            owner="MowerMQTT",
            missing_message="MowerMQTT.async_connect() requires a running event loop or an explicit loop= argument",
        )
        # Sjølve tilkoplinga skjer ved abonnement; her stadfestar vi berre at løkka er gyldig.

    def _connection_signature(self) -> tuple[Any, ...]:
        """Alt som avgjer korleis paho-klienten blir bygd; endring krev ny klient."""
        headers = tuple(sorted((self.auth_headers or {}).items()))
        return (self.broker, self.port, self.ws_path, self.username, self.password, headers)

    def connect(self) -> None:
        """Kople til MQTT-broker synkront.

        Idempotent: ein alt bygd klient blir attbrukt så lenge tilkoplingsoppsettet er
        uendra. Er legitimasjon/vert oppdaterte (t.d. via `configure_wss`), blir den gamle
        klienten stoppa og ein ny bygd; `on_connect` teiknar opp att alle abonnement.
        """
        if self._sync_client is not None:
            if self._sync_signature == self._connection_signature():
                return
            _LOGGER.info("MQTT connection settings changed; rebuilding sync client")
            self.disconnect()
        try:
            self._sync_signature = self._connection_signature()
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
                if _reason_code_is_failure(reason_code):
                    # Kasting her ville lande på paho-tråden der ingen kan fange det.
                    _LOGGER.error("MQTT connection failed (sync): rc=%s", reason_code)
                    return
                self._connected = True
                _LOGGER.info(
                    "MQTT connected (sync): broker=%s port=%s",
                    self.broker,
                    self.port,
                )
                # Abonnementa overlever ikkje ei ny økt; teikn dei opp att ved kvar (att)kopling.
                try:
                    self._subscribe_topics(client, self._registered_devices())
                    self._location_pass_done = self.subscribe_location
                except Exception:  # må ikkje drepe paho sin nettverkstråd
                    _LOGGER.exception("MQTT subscribe failed after connect (sync)")

            self._sync_client.on_connect = on_connect
            self._sync_loop()  # bind ei køyrande løkke no, om ei finst
            self._sync_client.on_message = self._make_on_message(self._bound_loop, "sync")
            _LOGGER.info(
                "MQTT connecting (sync): broker=%s port=%s ws_path=%s",
                self.broker,
                self.port,
                self.ws_path,
            )
            self._sync_client.connect(self.broker, self.port, self.keepalive_seconds)
            self._sync_client.loop_start()
        except Exception as e:
            # Ikkje lat ein halvbygd klient lure idempotens-vernet ved neste forsøk.
            failed_client, self._sync_client = self._sync_client, None
            if failed_client is not None:
                try:
                    _stop_paho_client(failed_client)
                except Exception:
                    _LOGGER.debug("Cleanup of failed sync MQTT client raised", exc_info=True)
            self._sync_signature = None
            self._connected = False
            raise MowerMQTTError(f"{ERROR_MESSAGES['MQTT_CONNECTION_FAILED']}: {str(e)}") from e

    async def async_subscribe_device(
        self,
        device_id: str,
        on_status_update: Optional[Callable[[DeviceStatus], None]] = None,
        on_event: Optional[Callable[[dict[str, Any]], None]] = None,
        on_location: Optional[Callable[[DeviceLocationMessage], None]] = None,
    ) -> None:
        """Abonner asynkront på status og hendingar for ei eining.

        Første kall byggjer MQTT-klienten; seinare kall (t.d. via `asyncio.gather` for
        fleire einingar) deler same klient og same økt. Kvart kall ventar til tilkoplinga
        blir broten (eller `async_disconnect()` blir kalla). Blir tilkoplingsoppsettet
        endra i mellomtida (t.d. nye legitimasjonar via `configure_wss`), blir økta bytt ut
        med ei ny og kallet held fram på henne utan å returnere. Blir eitt kall avbrote,
        held økta fram for dei andre; først når det siste kallet forlèt økta blir klienten
        stoppa. Eitt aktivt kall per eining; eit samtidig kall for same eining blir avvist.
        Å slå `subscribe_location` av att fjernar ikkje alt teikna posisjonsemne før neste
        attkopling.
        """
        self._loop = _bind_event_loop(
            self._loop,
            owner="MowerMQTT",
            missing_message="MowerMQTT.async_subscribe_device() requires a running event loop or an explicit loop= argument",
        )
        loop = self._loop
        if device_id in self._async_owned_devices or device_id in self._sync_owned_devices:
            # To samtidige kall for same eining (eller eitt synkront og eitt asynkront) ville
            # overskrive kvarandre sine tilbakekall. Avvis tydeleg i staden.
            raise MowerMQTTError(
                f"{ERROR_MESSAGES['MQTT_SUBSCRIBE_FAILED']}: device {device_id} already has an "
                "active async subscription"
            )
        # Ta vare på tilbakekall for denne eininga (etter løkkebinding, så ei feilbinding
        # ikkje etterlet ei registrering; før abonnementet, så inga melding går tapt).
        my_callbacks: dict[str, Any] = {
            "status": on_status_update,
            "event": on_event,
            "location": on_location,
        }
        try:
            self._async_owned_devices.add(device_id)
            self._register_callbacks(device_id, my_callbacks)
            while True:
                if not self._async_session_is_live():
                    async with self._session_lock():
                        # Ein annan deltakar kan ha starta ei ny økt medan vi venta på låsen.
                        if self._async_client is not None and not self._async_session_is_live():
                            await self._end_async_session_async(loop, replaced=True)
                        if self._async_client is None:
                            self._start_async_session(device_id, my_callbacks, loop)
                        elif self._async_connected:
                            self._subscribe_new_device(device_id)
                elif self._async_connected:
                    self._subscribe_new_device(device_id)
                # Elles tek on_connect seg av abonnementet når tilkoplinga er oppe.

                stop_event = self._async_stop_event
                if stop_event is None:
                    raise MowerMQTTError(ERROR_MESSAGES["MQTT_CONNECTION_FAILED"])

                self._async_waiters += 1
                try:
                    await stop_event.wait()
                finally:
                    self._async_waiters -= 1
                    if self._async_waiters == 0 and self._async_stop_event is stop_event:
                        # Skjerma: eit avbrot medan vi ventar på låsen skal ikkje hoppe over
                        # nedrivinga og etterlate ein levande paho-tråd utan deltakarar.
                        teardown = asyncio.ensure_future(
                            self._end_session_if_last(loop, stop_event)
                        )
                        try:
                            await asyncio.shield(teardown)
                        except asyncio.CancelledError:
                            teardown.add_done_callback(_log_teardown_result)
                            raise
                if not stop_event.replaced:
                    return  # tilkoplinga vart broten (eller fråkopla): kontrakten er å returnere
                # Økta vart bytt ut (t.d. nye legitimasjonar): bli med i den nye i staden for
                # å returnere, så eininga ikkje mistar abonnementet sitt i det stille.
        finally:
            # Kallet er over (retur, feil eller avbrot): ikkje etterlat tilbakekalla, elles
            # teiknar neste økt eininga opp att og kallar ein død lyttar.
            self._forget_callbacks(device_id, my_callbacks)
            self._async_owned_devices.discard(device_id)

    async def _end_session_if_last(
        self, loop: asyncio.AbstractEventLoop, stop_event: "_SessionEvent"
    ) -> None:
        async with self._session_lock():
            if self._async_waiters == 0 and self._async_stop_event is stop_event:
                await self._end_async_session_async(loop)

    def _subscribe_new_device(self, device_id: str) -> None:
        """Abonner ei ny eining på den levande asynkrone klienten (med posisjons-bokføring)."""
        client = self._async_client
        if client is None:
            raise MowerMQTTError(ERROR_MESSAGES["MQTT_CONNECTION_FAILED"])
        try:
            if self.subscribe_location and not self._async_location_pass_done:
                # Nyleg påslått subscribe_location: éin full pass over alle einingar.
                self._subscribe_topics(client, self._registered_devices())
            else:
                self._subscribe_topics(client, [device_id])
            self._async_location_pass_done = self.subscribe_location
        except Exception as e:
            raise MowerMQTTError(f"{ERROR_MESSAGES['MQTT_SUBSCRIBE_FAILED']}: {str(e)}") from e

    def _async_session_is_live(self) -> bool:
        """Sei om den asynkrone økta kan delast: har klient, er ikkje stoppa, er ikkje
        broten etter å ha vore oppe, og er bygd med gjeldande tilkoplingsoppsett."""
        return (
            self._async_client is not None
            and self._async_stop_event is not None
            and not self._async_stop_event.is_set()
            and (self._async_connected or not self._async_session_was_connected)
            and self._async_signature == self._connection_signature()
        )

    def _session_lock(self) -> asyncio.Lock:
        """Lås som gjer start/stopp av den asynkrone økta gjensidig utelukkande."""
        if self._async_session_lock is None:
            self._async_session_lock = asyncio.Lock()
        return self._async_session_lock

    def _register_callbacks(self, device_id: str, owned: dict[str, Any]) -> None:
        with self._state_lock:
            self._callbacks[device_id] = owned

    def _registered_devices(self) -> list[str]:
        """Augneblinksbilete av registrerte einingar, trygt frå paho-tråden."""
        with self._state_lock:
            return list(self._callbacks)

    def _forget_callbacks(self, device_id: str, owned: dict[str, Any]) -> None:
        """Fjern tilbakekalla for eininga, men berre om dei framleis er våre eigne."""
        with self._state_lock:
            if self._callbacks.get(device_id) is owned:
                self._callbacks.pop(device_id, None)

    async def _end_async_session_async(
        self, loop: asyncio.AbstractEventLoop, replaced: bool = False
    ) -> None:
        """Stopp økta utan å blokkere løkka (paho sin loop_stop() ventar på tråden sin).

        `replaced=True` fortel deltakarane at ei ny økt tek over, så dei blir med i henne
        i staden for å returnere.
        """
        client = self._async_client
        old_event = self._async_stop_event
        # Berre ei levande økt kan «bytast ut»; ei broten økt skal returnere. Liveness er
        # avgjort av flagga paho-tråden set med ein gong (on_disconnect), ikkje av
        # stopp-hendinga, som først blir sett seinare på løkka.
        was_live = self._async_connected or not self._async_session_was_connected
        if old_event is not None and replaced and was_live and not old_event.is_set():
            old_event.replaced = True
        self._async_client = None
        self._async_stop_event = None
        self._async_connected = False
        self._async_session_was_connected = False
        self._async_location_pass_done = False
        self._async_signature = None
        if old_event is not None:
            # Vekk deltakarane i den gamle økta FØR vi kan bli avbrotne i utføraren.
            old_event.set()
        if client is not None:
            # Skjerma: eit avbrot medan stoppinga ventar i utføraren skal ikkje etterlate
            # ein levande paho-tråd som ingen lenger kan nå.
            stopping = loop.run_in_executor(None, _stop_paho_client, client)
            try:
                await asyncio.shield(stopping)
            except asyncio.CancelledError:
                stopping.add_done_callback(_log_teardown_result)
                raise
            except Exception:
                _LOGGER.debug("Cleanup of async MQTT client raised", exc_info=True)

    def _start_async_session(
        self, device_id: str, owned: dict[str, Any], loop: asyncio.AbstractEventLoop
    ) -> None:
        """Bygg og kople til ein ny asynkron klient; rydd opp fullstendig ved feil."""
        stop_event = _SessionEvent()
        self._async_stop_event = stop_event
        self._async_connected = False
        self._async_session_was_connected = False
        self._async_location_pass_done = False
        self._async_signature = self._connection_signature()
        started = False
        connected = False
        try:
            client = self._build_client()
            self._async_client = client
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
                if self._async_stop_event is not stop_event:
                    return  # tilbakekall frå ei gammal økt
                self._async_connected = True
                self._async_session_was_connected = True
                _LOGGER.info("MQTT connected (async): broker=%s port=%s", self.broker, self.port)
                try:
                    self._subscribe_topics(_client, self._registered_devices())
                    self._async_location_pass_done = self.subscribe_location
                except Exception:  # må ikkje drepe paho sin nettverkstråd
                    _LOGGER.exception("MQTT subscribe failed after connect (async)")

            def on_disconnect(_client, _userdata, *disconnect_args) -> None:
                _LOGGER.debug(
                    "MQTT disconnected (async): broker=%s port=%s", self.broker, self.port
                )
                if self._async_stop_event is not stop_event:
                    return  # tilbakekall frå ei gammal, alt stoppa økt
                self._async_connected = False
                _call_soon_threadsafe(loop, stop_event.set)

            client.on_connect = on_connect
            client.on_message = self._make_on_message(lambda: loop, "async")
            client.on_disconnect = on_disconnect
            _LOGGER.info(
                "MQTT connecting (async): broker=%s port=%s ws_path=%s device=%s",
                self.broker,
                self.port,
                self.ws_path,
                device_id,
            )
            client.connect(self.broker, self.port, self.keepalive_seconds)
            connected = True
            client.loop_start()
            started = True
        except Exception as e:
            # Ikkje lat ein halvbygd klient blokkere seinare kall; vekk eventuelle ventarar.
            self._forget_callbacks(device_id, owned)
            failed_client = self._async_client
            self._async_client = None
            self._async_stop_event = None
            self._async_connected = False
            self._async_session_was_connected = False
            if failed_client is not None and started:
                # Nettverkstråden er i gang: stopp henne i utføraren, ikkje på løkka.
                loop.run_in_executor(None, _stop_paho_client, failed_client).add_done_callback(
                    _log_teardown_result
                )
            elif failed_client is not None and connected:
                # Tilkopla, men ingen tråd starta: lukk økta direkte (ingen join å vente på).
                try:
                    failed_client.disconnect()
                except Exception:
                    _LOGGER.debug("Cleanup of connected-but-unstarted client raised", exc_info=True)
            stop_event.set()
            raise MowerMQTTError(f"{ERROR_MESSAGES['MQTT_SUBSCRIBE_FAILED']}: {str(e)}") from e

    def subscribe_device(
        self,
        device_id: str,
        on_status_update: Optional[Callable[[DeviceStatus], None]] = None,
        on_event: Optional[Callable[[dict[str, Any]], None]] = None,
        on_location: Optional[Callable[[DeviceLocationMessage], None]] = None,
    ) -> None:
        """Abonner synkront på status og hendingar for ei eining.

        Fleire einingar kan registrerast på same klient; meldingar blir ruta etter emnet.
        Abonnementa blir teikna opp att automatisk ved attkopling.
        """
        if not self._sync_client:
            self.connect()
        assert self._sync_client is not None

        # Registrer tilbakekalla FØR abonnementet, så ei melding som kjem med ein gong
        # (t.d. ei retained tilstandsmelding) ikkje blir kasta.
        if device_id in self._async_owned_devices:
            raise MowerMQTTError(
                f"{ERROR_MESSAGES['MQTT_SUBSCRIBE_FAILED']}: device {device_id} already has an "
                "active async subscription"
            )
        owned: dict[str, Any] = {
            "status": on_status_update,
            "event": on_event,
            "location": on_location,
        }
        self._register_callbacks(device_id, owned)
        self._sync_owned_devices.add(device_id)
        self._sync_loop()  # bind ei køyrande løkke no, om ei finst
        self._sync_client.on_message = self._make_on_message(self._bound_loop, "sync")
        try:
            if self._connected:
                if self.subscribe_location and not self._location_pass_done:
                    # Nyleg påslått subscribe_location: éin full pass så òg dei som alt var
                    # abonnerte får posisjonsemnet.
                    self._subscribe_topics(self._sync_client, self._registered_devices())
                else:
                    self._subscribe_topics(self._sync_client, [device_id])
                self._location_pass_done = self.subscribe_location
            # Elles tek on_connect seg av abonnementet når tilkoplinga er oppe.
        except Exception as e:
            self._forget_callbacks(device_id, owned)
            self._sync_owned_devices.discard(device_id)
            raise MowerMQTTError(f"{ERROR_MESSAGES['MQTT_SUBSCRIBE_FAILED']}: {str(e)}") from e

    def _bound_loop(self) -> Optional[asyncio.AbstractEventLoop]:
        """Den bundne løkka, om ei finst. Trygg å kalle frå paho-tråden."""
        return self._loop

    def _sync_loop(self) -> Optional[asyncio.AbstractEventLoop]:
        """Løkka for den synkrone stien: bunden eller køyrande om ho finst, elles inga."""
        if self._loop is None:
            running = _get_running_loop_if_available()
            if running is not None:
                self._loop = running
        return self._loop

    def get_cached_status(self, device_id: str) -> Optional[DeviceStatus]:
        """Hent bufra einingstilstand."""
        return self.status_cache.get(device_id)

    def get_cached_location(self, device_id: str) -> Optional[DeviceLocationMessage]:
        """Hent sist godtekne posisjonslesing."""
        return self.location_cache.get(device_id)

    async def async_disconnect(self) -> None:
        """Bryt MQTT-tilkopling asynkront."""
        self._loop = _bind_event_loop(
            self._loop,
            owner="MowerMQTT",
            missing_message="MowerMQTT.async_disconnect() requires a running event loop or an explicit loop= argument",
        )
        async with self._session_lock():
            await self._end_async_session_async(self._loop)

    def disconnect(self) -> None:
        """Bryt MQTT-tilkopling synkront."""
        if self._sync_client:
            _stop_paho_client(self._sync_client)
            # Ein stoppa paho-klient er død; fjern han så neste connect() byggjer ein ny.
            self._sync_client = None
        self._connected = False
        self._location_pass_done = False
        # Synkrone registreringar lever vidare (connect() teiknar dei opp att), men dei
        # sperrar ikkje lenger for eit asynkront kall for same eining.
        self._sync_owned_devices.clear()


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
        subscribe_location: bool = False,
        extra_topics: Optional[list[str]] = None,
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
        self.subscribe_location = subscribe_location
        self.extra_topics = _validate_topics(extra_topics)
        self._connection_started = False
        self._network_loop_started = False

        self.on_connected: Optional[Callable[[], Awaitable[None]]] = None
        self.on_ready: Optional[Callable[[], Awaitable[None]]] = None
        self.on_message: Optional[Callable[[str, bytes, str], Awaitable[None]]] = None
        self.on_raw: Optional[Callable[[str, bytes], Awaitable[None]]] = None
        self.on_disconnected: Optional[Callable[[], Awaitable[None]]] = None

        transport: Literal["websockets", "tcp"] = "websockets" if self.ws_path else "tcp"
        self.client = mqtt_client.Client(
            callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2,
            client_id=self._client_id,
            transport=transport,
        )
        self._apply_credentials_to_client(self.client)
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
        self._apply_credentials_to_client(client)
        if self._use_tls:
            client.tls_set()
        client.reconnect_delay_set(
            min_delay=self.reconnect_min_delay, max_delay=self.reconnect_max_delay
        )
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        return client

    def _apply_credentials_to_client(self, client: mqtt_client.Client) -> None:
        if self.username is not None:
            client.username_pw_set(self.username, self.password)
        if self.ws_path:
            client.ws_set_options(path=self.ws_path, headers=self.auth_headers or {})

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
            self._apply_credentials_to_client(self.client)
            # Ei aktiv tilkopling held fram; nye legitimasjonar blir brukte ved neste attkopling.
            # Vi koplar ikkje frå ved tokenfornying; det hindrar attkopling og «eining utilgjengeleg».
            _LOGGER.info(
                "NavimowMQTT credentials updated while connected (will apply on next reconnect): broker=%s port=%s",
                self.broker,
                self.port,
            )
            return

        # Bygg klienten opp att med ein gong, men vent med tilkoplinga til løkka er bunden.
        reconnect = self._connection_started
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
        self._network_loop_started = False

        self.client = self._build_new_client()
        if reconnect:
            self.connect_async()

    def connect_async(self) -> None:
        self._bind_loop()
        if not self.is_connected and not self._network_loop_started:
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
            self._connection_started = True
            self._network_loop_started = True

    def disconnect(self) -> None:
        # Rekkjefølgja er medvite den same som før (loop_stop før disconnect): då når paho
        # ikkje on_disconnect, og Home Assistant-integrasjonen får ikkje eit uventa
        # on_disconnected-tilbakekall under avlasting.
        self.client.loop_stop()
        self._network_loop_started = False
        self._connection_started = False
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
            if self.subscribe_location:
                self.client.subscribe("/downlink/vehicle/+/realtimeDate/location")
            for extra_topic in self.extra_topics:
                self.client.subscribe(extra_topic)
            return

        _LOGGER.info("NavimowMQTT subscribing cloud topics for %d device(s)", len(device_ids))
        for device_id in device_ids:
            self.client.subscribe(f"/downlink/vehicle/{device_id}/realtimeDate/state")
            self.client.subscribe(f"/downlink/vehicle/{device_id}/realtimeDate/event")
            self.client.subscribe(f"/downlink/vehicle/{device_id}/realtimeDate/attributes")
            if self.subscribe_location:
                self.client.subscribe(f"/downlink/vehicle/{device_id}/realtimeDate/location")
        for extra_topic in self.extra_topics:
            self.client.subscribe(extra_topic)

    def unsubscribe_all(self, product_key: str, device_name: str) -> None:
        device_ids = self._get_device_ids()
        if not device_ids:
            _LOGGER.info("NavimowMQTT unsubscribing cloud topics (wildcard)")
            self.client.unsubscribe("/downlink/vehicle/+/realtimeDate/state")
            self.client.unsubscribe("/downlink/vehicle/+/realtimeDate/event")
            self.client.unsubscribe("/downlink/vehicle/+/realtimeDate/attributes")
            if self.subscribe_location:
                self.client.unsubscribe("/downlink/vehicle/+/realtimeDate/location")
            for extra_topic in self.extra_topics:
                self.client.unsubscribe(extra_topic)
            return

        _LOGGER.info("NavimowMQTT unsubscribing cloud topics for %d device(s)", len(device_ids))
        for device_id in device_ids:
            self.client.unsubscribe(f"/downlink/vehicle/{device_id}/realtimeDate/state")
            self.client.unsubscribe(f"/downlink/vehicle/{device_id}/realtimeDate/event")
            self.client.unsubscribe(f"/downlink/vehicle/{device_id}/realtimeDate/attributes")
            if self.subscribe_location:
                self.client.unsubscribe(f"/downlink/vehicle/{device_id}/realtimeDate/location")
        for extra_topic in self.extra_topics:
            self.client.unsubscribe(extra_topic)

    def _start_task(self, callback: Callable[..., Awaitable[None]], args: tuple[Any, ...]) -> None:
        """Køyrer på løkka: kall tilbakekallet der og start korutinen det gjev."""
        try:
            asyncio.ensure_future(callback(*args))
        except Exception:
            _LOGGER.exception("MQTT callback failed to start")

    def _schedule_call(self, callback: Callable[..., Awaitable[None]], *args: Any) -> None:
        """Flytt både kallet og korutinen til løkka, så ingenting av brukarkode køyrer på paho-tråden."""
        if self.loop is None:
            _LOGGER.debug("Event loop not running, skip scheduling MQTT callback")
            return
        _call_soon_threadsafe(self.loop, self._start_task, callback, args)

    def _on_connect(self, _client, _userdata, _flags, reason_code, _properties=None) -> None:
        if _reason_code_is_failure(reason_code):
            _LOGGER.error("MQTT connection failed: rc=%s", reason_code)
            return
        _LOGGER.info(
            "NavimowMQTT connected: broker=%s port=%s",
            self.broker,
            self.port,
        )
        try:
            self.subscribe_all("", "")
        except Exception:  # må ikkje drepe paho sin nettverkstråd
            _LOGGER.exception("NavimowMQTT subscribe failed after connect")

        if self.on_connected is not None:
            self._schedule_call(self.on_connected)
        if self.on_ready is not None:
            self._schedule_call(self.on_ready)

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
            self._schedule_call(self.on_disconnected)

    def _parse_topic(self, topic: str) -> Tuple[Optional[str], Optional[str]]:
        return _parse_realtime_topic(topic)

    def _on_message(self, _client, _userdata, msg) -> None:
        topic = msg.topic
        device_id, _channel = self._parse_topic(topic)

        payload_bytes = msg.payload or b""
        _LOGGER.debug(
            "NavimowMQTT payload: topic=%s payload=%s",
            topic,
            payload_bytes.decode("utf-8", errors="replace"),
        )
        _LOGGER.debug(
            "NavimowMQTT message: topic=%s bytes=%d device=%s",
            topic,
            len(payload_bytes),
            device_id,
        )
        if self.on_raw is not None:
            self._schedule_call(self.on_raw, topic, payload_bytes)
        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            payload = None

        if isinstance(payload, dict) and device_id:
            payload.setdefault("device_id", device_id)
            payload_bytes = json.dumps(payload).encode("utf-8")

        if self.on_message is not None and device_id:
            self._schedule_call(self.on_message, topic, payload_bytes, device_id)

    def publish_command(self, device_id: str, payload: dict[str, Any]) -> None:
        topic = f"navimow/{device_id}/command"
        self.client.publish(topic, json.dumps(payload))
