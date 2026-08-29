import asyncio
import importlib
import json
import types
import unittest
from unittest import mock

from tests.support import FakePahoClient, install_dependency_stubs, purge_modules

DEVICE_ID = "device-1"
POSITION_POINT = {
    "postureX": -6.586,
    "postureY": 3.21,
    "postureTheta": -2.833,
    "time": 1755000000,
    "type": "position",
    "vehicleState": 3,
}
PROGRESS_POINT = {
    "mowingPercentage": 61.01,
    "subtotalArea": 224.15,
    "time": 1755000100,
    "type": "progress",
    "postureX": 1.0,
    "postureY": 2.0,
    "postureTheta": 0.5,
}
PLACEHOLDER_POINT = {
    "postureX": 0.0,
    "postureY": 0.0,
    "postureTheta": 0.0,
    "time": 1755000200,
    "type": "position",
}


def _load_modules():
    install_dependency_stubs()
    purge_modules("mower_sdk")
    models = importlib.import_module("mower_sdk.models")
    mqtt = importlib.import_module("mower_sdk.mqtt")
    sdk = importlib.import_module("mower_sdk.sdk")
    return models, mqtt, sdk


def _make_message(topic, payload):
    if isinstance(payload, bytes):
        encoded = payload
    else:
        encoded = json.dumps(payload).encode("utf-8")
    return types.SimpleNamespace(topic=topic, payload=encoded)


class _RecordingLoop:
    def __init__(self):
        self.calls = []

    def is_closed(self):
        return False

    def call_soon_threadsafe(self, callback, *args):
        self.calls.append((callback, args))

    def drain(self):
        while self.calls:
            callback, args = self.calls.pop(0)
            callback(*args)


class DeviceLocationMessageTests(unittest.TestCase):
    def setUp(self):
        self.models, _, _ = _load_modules()

    def test_device_location_message_maps_observed_fields(self):
        payload = dict(POSITION_POINT, device_id=DEVICE_ID, mowStartType="schedule")

        message = self.models.DeviceLocationMessage.from_dict(payload)

        self.assertEqual(DEVICE_ID, message.device_id)
        self.assertEqual(POSITION_POINT["postureX"], message.x)
        self.assertEqual(POSITION_POINT["postureY"], message.y)
        self.assertEqual(POSITION_POINT["postureTheta"], message.theta)
        self.assertEqual(POSITION_POINT["time"], message.timestamp)
        self.assertEqual("position", message.type)
        self.assertEqual(3, message.vehicle_state)
        self.assertEqual("schedule", message.mow_start_type)
        self.assertEqual(payload, message.raw)

    def test_device_location_message_sets_missing_fields_to_none(self):
        message = self.models.DeviceLocationMessage.from_dict({"device_id": DEVICE_ID})

        self.assertIsNone(message.x)
        self.assertIsNone(message.y)
        self.assertIsNone(message.theta)
        self.assertIsNone(message.timestamp)
        self.assertIsNone(message.type)
        self.assertIsNone(message.vehicle_state)

    def test_device_location_message_to_dict_preserves_public_field_names(self):
        payload = dict(PROGRESS_POINT, device_id=DEVICE_ID, mowStartType={"mode": "edge"})
        message = self.models.DeviceLocationMessage.from_dict(payload)

        result = message.to_dict()

        self.assertEqual(DEVICE_ID, result["device_id"])
        self.assertEqual(PROGRESS_POINT["postureX"], result["x"])
        self.assertEqual(PROGRESS_POINT["postureY"], result["y"])
        self.assertEqual(PROGRESS_POINT["postureTheta"], result["theta"])
        self.assertEqual(PROGRESS_POINT["time"], result["timestamp"])
        self.assertEqual("progress", result["type"])
        self.assertEqual({"mode": "edge"}, result["mow_start_type"])
        self.assertEqual(payload, result["raw"])

    def test_parse_location_payload_accepts_a_single_dict(self):
        messages = self.models.parse_location_payload(POSITION_POINT, DEVICE_ID)

        self.assertEqual(1, len(messages))
        self.assertEqual(DEVICE_ID, messages[0].device_id)

    def test_parse_location_payload_injects_device_id_and_skips_non_dict_entries(self):
        payload = [POSITION_POINT, "bad", 42, PROGRESS_POINT]

        messages = self.models.parse_location_payload(payload, DEVICE_ID)

        self.assertEqual(2, len(messages))
        self.assertEqual([DEVICE_ID, DEVICE_ID], [message.device_id for message in messages])

    def test_is_placeholder_requires_exact_zero_triple(self):
        placeholder = self.models.DeviceLocationMessage.from_dict(
            dict(PLACEHOLDER_POINT, device_id=DEVICE_ID)
        )
        partial = self.models.DeviceLocationMessage.from_dict(
            dict(PLACEHOLDER_POINT, device_id=DEVICE_ID, postureTheta=0.1)
        )

        self.assertTrue(placeholder.is_placeholder)
        self.assertFalse(partial.is_placeholder)


class LocationFilterTests(unittest.TestCase):
    def setUp(self):
        self.models, _, _ = _load_modules()

    def test_location_filter_drops_placeholder_points(self):
        location_filter = self.models.LocationFilter()
        messages = self.models.parse_location_payload(
            [PLACEHOLDER_POINT, POSITION_POINT],
            DEVICE_ID,
        )

        accepted = location_filter.filter(messages)

        self.assertEqual(1, len(accepted))
        self.assertEqual(POSITION_POINT["postureX"], accepted[0].x)

    def test_location_filter_drops_stale_points_per_device_and_type(self):
        location_filter = self.models.LocationFilter()
        payload = [
            dict(PROGRESS_POINT, time=200),
            dict(PROGRESS_POINT, time=199),
            dict(POSITION_POINT, time=150),
        ]

        accepted = location_filter.filter(self.models.parse_location_payload(payload, DEVICE_ID))

        self.assertEqual(
            [200, 150],
            [message.timestamp for message in accepted],
        )
        self.assertEqual(
            ["progress", "position"],
            [message.type for message in accepted],
        )

    def test_location_filter_keeps_points_without_timestamp(self):
        location_filter = self.models.LocationFilter()
        payload = [
            dict(POSITION_POINT, time=200),
            dict(POSITION_POINT),
        ]
        payload[1].pop("time")

        accepted = location_filter.filter(self.models.parse_location_payload(payload, DEVICE_ID))

        self.assertEqual(2, len(accepted))
        self.assertEqual([200, None], [message.timestamp for message in accepted])

    def test_location_filter_keeps_points_without_type(self):
        location_filter = self.models.LocationFilter()
        payload = [
            dict(POSITION_POINT, time=200, type="position"),
            dict(POSITION_POINT, time=100),
        ]
        payload[1].pop("type")

        accepted = location_filter.filter(self.models.parse_location_payload(payload, DEVICE_ID))

        self.assertEqual(2, len(accepted))
        self.assertEqual(["position", None], [message.type for message in accepted])


class NavimowMQTTTests(unittest.TestCase):
    def setUp(self):
        self.models, self.mqtt_module, _ = _load_modules()
        FakePahoClient.__init__.__defaults__ = (None, None, "tcp")

    def test_subscribe_all_uses_three_topics_per_device_when_location_is_disabled(self):
        device = self.models.Device(
            id=DEVICE_ID,
            name="Mower",
            model="i108e",
            firmware_version="1.0.0",
            serial_number="SERIAL",
        )
        mqtt = self.mqtt_module.NavimowMQTT(
            "broker",
            1883,
            None,
            None,
            [device],
            subscribe_location=False,
        )

        mqtt.subscribe_all("", "")

        self.assertEqual(
            [
                f"/downlink/vehicle/{DEVICE_ID}/realtimeDate/state",
                f"/downlink/vehicle/{DEVICE_ID}/realtimeDate/event",
                f"/downlink/vehicle/{DEVICE_ID}/realtimeDate/attributes",
            ],
            mqtt.client.subscriptions,
        )

    def test_subscribe_all_includes_location_and_extra_topics(self):
        device = self.models.Device(
            id=DEVICE_ID,
            name="Mower",
            model="i108e",
            firmware_version="1.0.0",
            serial_number="SERIAL",
        )
        mqtt = self.mqtt_module.NavimowMQTT(
            "broker",
            1883,
            None,
            None,
            [device],
            subscribe_location=True,
            extra_topics=["/custom/topic", f"/downlink/vehicle/{DEVICE_ID}/#"],
        )

        mqtt.subscribe_all("", "")

        self.assertEqual(
            [
                f"/downlink/vehicle/{DEVICE_ID}/realtimeDate/state",
                f"/downlink/vehicle/{DEVICE_ID}/realtimeDate/event",
                f"/downlink/vehicle/{DEVICE_ID}/realtimeDate/attributes",
                f"/downlink/vehicle/{DEVICE_ID}/realtimeDate/location",
                "/custom/topic",
                f"/downlink/vehicle/{DEVICE_ID}/#",
            ],
            mqtt.client.subscriptions,
        )

    def test_unsubscribe_all_mirrors_location_and_extra_topics(self):
        device = self.models.Device(
            id=DEVICE_ID,
            name="Mower",
            model="i108e",
            firmware_version="1.0.0",
            serial_number="SERIAL",
        )
        mqtt = self.mqtt_module.NavimowMQTT(
            "broker",
            1883,
            None,
            None,
            [device],
            subscribe_location=True,
            extra_topics=["/custom/topic"],
        )

        mqtt.unsubscribe_all("", "")

        self.assertEqual(
            [
                f"/downlink/vehicle/{DEVICE_ID}/realtimeDate/state",
                f"/downlink/vehicle/{DEVICE_ID}/realtimeDate/event",
                f"/downlink/vehicle/{DEVICE_ID}/realtimeDate/attributes",
                f"/downlink/vehicle/{DEVICE_ID}/realtimeDate/location",
                "/custom/topic",
            ],
            mqtt.client.unsubscriptions,
        )


class NavimowMQTTAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_on_raw_is_scheduled_for_known_and_unknown_topics(self):
        models, mqtt_module, _ = _load_modules()
        device = models.Device(
            id=DEVICE_ID,
            name="Mower",
            model="i108e",
            firmware_version="1.0.0",
            serial_number="SERIAL",
        )
        mqtt = mqtt_module.NavimowMQTT(
            "broker",
            1883,
            None,
            None,
            [device],
            loop=asyncio.get_running_loop(),
        )
        raw_calls = []

        async def on_raw(topic, payload):
            raw_calls.append((topic, payload))

        mqtt.on_raw = on_raw

        mqtt._on_message(
            mqtt.client,
            None,
            _make_message(
                f"/downlink/vehicle/{DEVICE_ID}/realtimeDate/state",
                {"state": "docked"},
            ),
        )
        mqtt._on_message(mqtt.client, None, _make_message("/unknown/topic", {"ok": True}))
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        self.assertEqual(
            [
                (
                    f"/downlink/vehicle/{DEVICE_ID}/realtimeDate/state",
                    b'{"state": "docked"}',
                ),
                ("/unknown/topic", b'{"ok": true}'),
            ],
            raw_calls,
        )


class NavimowSDKTests(unittest.IsolatedAsyncioTestCase):
    async def test_location_payload_dispatches_each_filtered_point_and_updates_cache(self):
        _, _, sdk_module = _load_modules()
        sdk = sdk_module.NavimowSDK("broker", 1883)
        received = []
        sdk.on_location(received.append)
        progress_only = {"mowingPercentage": 61.01, "time": 1755000100, "type": "progress"}

        await sdk._on_mqtt_message(
            f"/downlink/vehicle/{DEVICE_ID}/realtimeDate/location",
            json.dumps([POSITION_POINT, PLACEHOLDER_POINT, progress_only]).encode("utf-8"),
            DEVICE_ID,
        )

        self.assertEqual(["position", "progress"], [point.type for point in received])
        # Framdriftspunktet utan koordinatar må ikkje overskrive siste posisjon.
        self.assertEqual(-6.586, sdk.get_cached_location(DEVICE_ID).x)

    async def test_on_raw_callbacks_receive_topic_and_bytes(self):
        _, _, sdk_module = _load_modules()
        sdk = sdk_module.NavimowSDK("broker", 1883)
        received = []
        sdk.on_raw(lambda topic, payload: received.append((topic, payload)))

        self.assertIsNotNone(sdk._mqtt.on_raw)
        await sdk._mqtt.on_raw("/custom/topic", b'{"ok": true}')

        self.assertEqual([("/custom/topic", b'{"ok": true}')], received)


class MowerMQTTTests(unittest.TestCase):
    def setUp(self):
        self.models, self.mqtt_module, _ = _load_modules()

    def test_mower_mqtt_uses_realtime_protocol_topics(self):
        mqtt = self.mqtt_module.MowerMQTT("broker", 1883)

        self.assertEqual(
            f"/downlink/vehicle/{DEVICE_ID}/realtimeDate/state",
            mqtt._get_status_topic(DEVICE_ID),
        )
        self.assertEqual(
            f"/downlink/vehicle/{DEVICE_ID}/realtimeDate/event",
            mqtt._get_event_topic(DEVICE_ID),
        )
        self.assertEqual(
            f"/downlink/vehicle/{DEVICE_ID}/realtimeDate/location",
            mqtt._get_location_topic(DEVICE_ID),
        )

    def test_state_messages_reuse_cached_status_when_state_fields_are_missing(self):
        loop = _RecordingLoop()
        mqtt = self.mqtt_module.MowerMQTT("broker", 1883, loop=loop)
        mqtt.status_cache[DEVICE_ID] = self.models.DeviceStatus(
            device_id=DEVICE_ID,
            status=self.models.MowerStatus.MOWING,
            battery=50,
        )
        callback = mock.Mock()

        mqtt.subscribe_device(DEVICE_ID, on_status_update=callback)
        mqtt._sync_client.on_message(
            mqtt._sync_client,
            None,
            _make_message(
                mqtt._get_status_topic(DEVICE_ID),
                {"device_id": DEVICE_ID, "battery": 88, "timestamp": 123},
            ),
        )
        loop.drain()

        status = callback.call_args.args[0]
        self.assertEqual(self.models.MowerStatus.MOWING, status.status)
        self.assertEqual(88, status.battery)

    def test_sync_subscribe_without_any_loop_calls_callbacks_directly(self):
        # Regresjonsvern: den synkrone stien må verke frå vanleg synkron kode utan loop=.
        mqtt = self.mqtt_module.MowerMQTT("broker", 1883, subscribe_location=True)
        status_callback = mock.Mock()
        location_callback = mock.Mock()
        raw_callback = mock.Mock()
        mqtt.on_raw = raw_callback

        mqtt.subscribe_device(
            DEVICE_ID, on_status_update=status_callback, on_location=location_callback
        )
        client = mqtt._sync_client
        client.on_message(
            client,
            None,
            _make_message(
                mqtt._get_status_topic(DEVICE_ID),
                {"state": "isRunning", "battery": 70},
            ),
        )
        client.on_message(
            client,
            None,
            _make_message(mqtt._get_location_topic(DEVICE_ID), [POSITION_POINT]),
        )

        self.assertEqual(self.models.MowerStatus.MOWING, status_callback.call_args.args[0].status)
        self.assertEqual(-6.586, location_callback.call_args.args[0].x)
        self.assertEqual(2, raw_callback.call_count)
        self.assertIsNotNone(mqtt.get_cached_location(DEVICE_ID))

    def test_state_messages_default_to_unknown_without_state_or_cache(self):
        loop = _RecordingLoop()
        mqtt = self.mqtt_module.MowerMQTT("broker", 1883, loop=loop)
        callback = mock.Mock()

        mqtt.subscribe_device(DEVICE_ID, on_status_update=callback)
        mqtt._sync_client.on_message(
            mqtt._sync_client,
            None,
            _make_message(
                mqtt._get_status_topic(DEVICE_ID),
                {"device_id": DEVICE_ID, "battery": 88},
            ),
        )
        loop.drain()

        status = callback.call_args.args[0]
        self.assertEqual(self.models.MowerStatus.UNKNOWN, status.status)


class MowerMQTTAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_location_messages_update_cache_and_dispatch_callbacks(self):
        models, mqtt_module, _ = _load_modules()
        mqtt = mqtt_module.MowerMQTT("broker", 1883, subscribe_location=True)
        callback = mock.Mock()

        subscribe_task = asyncio.create_task(
            mqtt.async_subscribe_device(DEVICE_ID, on_location=callback)
        )
        await asyncio.sleep(0)

        mqtt._async_client.on_message(
            mqtt._async_client,
            None,
            _make_message(
                mqtt._get_location_topic(DEVICE_ID),
                [PLACEHOLDER_POINT, POSITION_POINT],
            ),
        )
        await asyncio.sleep(0)
        mqtt._async_client.on_disconnect(mqtt._async_client, None, None, None, None)
        await subscribe_task

        callback.assert_called_once()
        location = callback.call_args.args[0]
        self.assertEqual("position", location.type)
        self.assertEqual("position", mqtt.get_cached_location(DEVICE_ID).type)


class ReviewRegressionTests(unittest.TestCase):
    """Regresjonsvern for funn frå den motstridande gjennomgangen."""

    def setUp(self):
        self.models, self.mqtt_module, self.sdk_module = _load_modules()
        self.client_module = importlib.import_module("mower_sdk.client")

    def _filter(self):
        return self.models.LocationFilter()

    def _point(self, **overrides):
        payload = dict(POSITION_POINT)
        payload.update(overrides)
        return self.models.parse_location_payload(payload, overrides.get("device_id", DEVICE_ID))

    def test_missing_timestamp_never_advances_watermark(self):
        flt = self._filter()
        accepted = flt.filter(
            self._point(time=200) + self._point(time=None) + self._point(time=199)
        )
        self.assertEqual([200, None], [p.timestamp for p in accepted])

    def test_watermark_is_isolated_per_device(self):
        flt = self._filter()
        first = self._point(time=200)
        other = self.models.parse_location_payload(dict(POSITION_POINT, time=100), "device-2")
        accepted = flt.filter(first + other)
        self.assertEqual(2, len(accepted))

    def test_string_and_decimal_timestamps_are_parsed(self):
        msg = self.models.DeviceLocationMessage.from_dict({"time": "1755000000.5"})
        self.assertEqual(1755000000, msg.timestamp)
        msg = self.models.DeviceLocationMessage.from_dict({"time": None, "timestamp": 7})
        self.assertEqual(7, msg.timestamp)

    def test_location_message_has_defaults(self):
        msg = self.models.DeviceLocationMessage(device_id=DEVICE_ID)
        self.assertIsNone(msg.x)
        self.assertEqual({}, msg.raw)

    def _state_status(self, payload, cached=None):
        message = self.models.DeviceStateMessage.from_dict(dict(payload, device_id=DEVICE_ID))
        return self.models.DeviceStatus.from_state_message(
            message,
            fallback_status=cached.status if cached else None,
            fallback_battery=cached.battery if cached else None,
        )

    def test_numeric_vehicle_state_keeps_cached_status(self):
        cached = self.models.DeviceStatus(DEVICE_ID, self.models.MowerStatus.MOWING, 50)
        status = self._state_status({"vehicleState": 3, "battery": 88}, cached)
        self.assertEqual(self.models.MowerStatus.MOWING, status.status)
        self.assertEqual(88, status.battery)

    def test_partial_state_payload_keeps_cached_battery(self):
        cached = self.models.DeviceStatus(DEVICE_ID, self.models.MowerStatus.MOWING, 88)
        status = self._state_status({"state": "isDocked"}, cached)
        self.assertEqual(self.models.MowerStatus.DOCKED, status.status)
        self.assertEqual(88, status.battery)

    def test_state_conversion_preserves_legacy_fields(self):
        status = self._state_status(
            {
                "state": "isRunning",
                "battery": 70,
                "position": {"lat": 59.9, "lng": 10.7},
                "error_code": "stuck",
                "mowing_time": 120,
                "capacityRemaining": [{"unit": "PERCENTAGE", "rawValue": 70}],
            }
        )
        self.assertEqual({"lat": 59.9, "lng": 10.7}, status.position)
        self.assertEqual(self.models.MowerError.STUCK, status.error_code)
        self.assertEqual(120, status.mowing_time)
        self.assertIn("capacityRemaining", status.extra)

    def _connected_mqtt(self, **kwargs):
        mqtt = self.mqtt_module.MowerMQTT("broker", 1883, **kwargs)
        mqtt.connect()
        client = mqtt._sync_client
        client.on_connect(client, None, None, 0)
        return mqtt, client

    def test_sync_path_resubscribes_every_device_on_reconnect(self):
        mqtt, client = self._connected_mqtt(subscribe_location=True, extra_topics=["x/#"])
        mqtt.subscribe_device(DEVICE_ID, on_status_update=mock.Mock())
        mqtt.subscribe_device("device-2", on_status_update=mock.Mock())
        before = len(client.subscriptions)

        client.on_connect(client, None, None, 0)  # attkopling

        added = client.subscriptions[before:]
        self.assertIn(f"/downlink/vehicle/{DEVICE_ID}/realtimeDate/location", added)
        self.assertIn("/downlink/vehicle/device-2/realtimeDate/state", added)
        self.assertIn("x/#", added)

    def test_two_devices_share_one_sync_client(self):
        mqtt, client = self._connected_mqtt()
        cb_a, cb_b = mock.Mock(), mock.Mock()
        mqtt.subscribe_device("A", on_status_update=cb_a)
        mqtt.subscribe_device("B", on_status_update=cb_b)

        client.on_message(
            client, None, _make_message(mqtt._get_status_topic("A"), {"state": "isRunning"})
        )

        self.assertEqual(1, cb_a.call_count)
        self.assertEqual(0, cb_b.call_count)
        self.assertEqual(["A"], list(mqtt.status_cache))

    def test_sync_on_connect_failure_does_not_raise(self):
        mqtt = self.mqtt_module.MowerMQTT("broker", 1883)
        mqtt.connect()
        client = mqtt._sync_client
        client.on_connect(client, None, None, 5)  # feilkode
        self.assertFalse(mqtt._connected)

    def test_client_flag_is_never_cleared_by_default_argument(self):
        from mower_sdk.client import MowerClient

        client = MowerClient(session=mock.Mock(), token="t", mqtt_broker="broker")
        client.mqtt.subscribe_location = True
        with (
            mock.patch.object(client, "async_refresh_mqtt_info", new=mock.AsyncMock()),
            mock.patch.object(client.mqtt, "async_subscribe_device", new=mock.AsyncMock()),
        ):
            asyncio.run(client.async_subscribe_device_updates(DEVICE_ID, subscribe_location=False))
        self.assertTrue(client.mqtt.subscribe_location)

    def test_public_signature_prefixes_are_frozen(self):
        import inspect

        expected = {
            (self.sdk_module.NavimowSDK, "__init__"): [
                "self",
                "broker",
                "port",
                "username",
                "password",
                "ws_path",
                "auth_headers",
                "loop",
                "records",
                "keepalive_seconds",
                "reconnect_min_delay",
                "reconnect_max_delay",
            ],
            (self.mqtt_module.NavimowMQTT, "__init__"): [
                "self",
                "broker",
                "port",
                "username",
                "password",
                "records",
                "ws_path",
                "auth_headers",
                "loop",
                "keepalive_seconds",
                "reconnect_min_delay",
                "reconnect_max_delay",
            ],
            (self.mqtt_module.MowerMQTT, "subscribe_device"): [
                "self",
                "device_id",
                "on_status_update",
                "on_event",
            ],
            (self.mqtt_module.MowerMQTT, "async_subscribe_device"): [
                "self",
                "device_id",
                "on_status_update",
                "on_event",
            ],
            (self.mqtt_module.MowerMQTT, "__init__"): [
                "self",
                "broker",
                "port",
                "username",
                "password",
                "ws_path",
                "auth_headers",
                "keepalive_seconds",
                "reconnect_min_delay",
                "reconnect_max_delay",
                "loop",
            ],
            (self.client_module.MowerClient, "__init__"): [
                "self",
                "session",
                "token",
                "api_base_url",
                "mqtt_broker",
                "mqtt_port",
                "mqtt_username",
                "mqtt_password",
                "loop",
            ],
            (self.client_module.MowerClient, "subscribe_device_updates"): [
                "self",
                "device_id",
                "callback",
            ],
            (self.client_module.MowerClient, "async_subscribe_device_updates"): [
                "self",
                "device_id",
                "callback",
            ],
        }
        for (cls, name), prefix in expected.items():
            params = list(inspect.signature(getattr(cls, name)).parameters.values())
            self.assertEqual(
                prefix, [p.name for p in params[: len(prefix)]], f"{cls.__name__}.{name}"
            )
            for param in params[: len(prefix)]:
                self.assertEqual(
                    inspect.Parameter.POSITIONAL_OR_KEYWORD, param.kind, f"{cls.__name__}.{name}"
                )
            # Alt som er lagt til etter det gamle prefikset må ha standardverdi.
            for param in params[len(prefix) :]:
                self.assertIsNot(
                    inspect.Parameter.empty, param.default, f"{cls.__name__}.{name}.{param.name}"
                )

    def test_explicit_offline_state_is_not_masked_by_cache(self):
        cached = self.models.DeviceStatus(DEVICE_ID, self.models.MowerStatus.MOWING, 80)
        for raw in ("offline", "Offline"):
            status = self._state_status({"state": raw}, cached)
            self.assertEqual(self.models.MowerStatus.UNKNOWN, status.status, raw)

    def test_unusable_battery_value_keeps_cached_battery(self):
        cached = self.models.DeviceStatus(DEVICE_ID, self.models.MowerStatus.MOWING, 80)
        for payload in ({"battery": None}, {"capacityRemaining": []}):
            status = self._state_status(dict(payload, state="isRunning"), cached)
            self.assertEqual(80, status.battery, payload)

    def test_infinite_numbers_do_not_raise(self):
        msg = self.models.DeviceLocationMessage.from_dict({"time": float("inf")})
        self.assertIsNone(msg.timestamp)

    def test_from_dict_does_not_mutate_caller_extra(self):
        extra = {}
        self.models.DeviceStatus.from_dict({"vehicleState": "isDocked", "extra": extra})
        self.assertEqual({}, extra)

    def test_mower_client_reuses_one_sync_client_for_two_devices(self):
        from mower_sdk.client import MowerClient

        client = MowerClient(session=mock.Mock(), token="t", mqtt_broker="broker")
        with (
            mock.patch.object(client, "refresh_mqtt_info", new=mock.Mock()),
            mock.patch.object(
                client.mqtt, "_build_client", wraps=client.mqtt._build_client
            ) as build,
        ):
            client.subscribe_device_updates("A", callback=mock.Mock())
            client.subscribe_device_updates("B", callback=mock.Mock(), subscribe_location=True)
        self.assertEqual(1, build.call_count)

    def test_enabling_location_later_subscribes_already_registered_devices(self):
        mqtt, client = self._connected_mqtt()
        mqtt.subscribe_device("A", on_status_update=mock.Mock())
        mqtt.subscribe_location = True
        mqtt.subscribe_device("B", on_status_update=mock.Mock())
        self.assertIn("/downlink/vehicle/A/realtimeDate/location", client.subscriptions)

    def test_failing_location_callback_does_not_stop_others(self):
        import logging

        sdk = self.sdk_module.NavimowSDK("broker", 1883)
        received = []
        logging.getLogger("mower_sdk.sdk").disabled = True
        self.addCleanup(setattr, logging.getLogger("mower_sdk.sdk"), "disabled", False)
        sdk.on_location(lambda _p: 1 / 0)
        sdk.on_location(received.append)
        asyncio.run(
            sdk._on_mqtt_message(
                f"/downlink/vehicle/{DEVICE_ID}/realtimeDate/location",
                json.dumps([POSITION_POINT, dict(POSITION_POINT, time=1755000001)]).encode(),
                DEVICE_ID,
            )
        )
        self.assertEqual(2, len(received))

    def test_state_message_without_raw_converts_from_fields(self):
        message = self.models.DeviceStateMessage(
            device_id=DEVICE_ID, timestamp=5, state="mowing", battery=77
        )
        status = self.models.DeviceStatus.from_state_message(message)
        self.assertEqual(self.models.MowerStatus.MOWING, status.status)
        self.assertEqual(77, status.battery)
        self.assertEqual(5, status.timestamp)

    def test_non_dict_payloads_on_typed_channels_are_ignored(self):
        sdk = self.sdk_module.NavimowSDK("broker", 1883)
        calls = []
        sdk.on_state(calls.append)
        sdk.on_event(calls.append)
        sdk.on_attributes(calls.append)
        for channel in ("state", "event", "attributes"):
            for payload in (b"[1, 2]", b"42", b"not json"):
                asyncio.run(
                    sdk._on_mqtt_message(
                        f"/downlink/vehicle/{DEVICE_ID}/realtimeDate/{channel}", payload, DEVICE_ID
                    )
                )
        self.assertEqual([], calls)
        self.assertIsNone(sdk.get_cached_state(DEVICE_ID))

    def test_bound_loop_callbacks_are_queued_not_run_on_mqtt_thread(self):
        import threading

        loop = _RecordingLoop()
        mqtt = self.mqtt_module.MowerMQTT("broker", 1883, loop=loop, subscribe_location=True)
        seen_threads = []
        record = lambda *_a: seen_threads.append(threading.current_thread().name)  # noqa: E731
        mqtt.on_raw = record
        mqtt.subscribe_device(
            DEVICE_ID, on_status_update=record, on_event=record, on_location=record
        )
        client = mqtt._sync_client

        def paho_thread():
            client.on_message(
                client,
                None,
                _make_message(mqtt._get_status_topic(DEVICE_ID), {"state": "isRunning"}),
            )
            client.on_message(
                client, None, _make_message(mqtt._get_event_topic(DEVICE_ID), {"event": "x"})
            )
            client.on_message(
                client, None, _make_message(mqtt._get_location_topic(DEVICE_ID), [POSITION_POINT])
            )

        t = threading.Thread(target=paho_thread, name="paho-net")
        t.start()
        t.join()

        self.assertEqual([], seen_threads)  # ingenting køyrde inline på paho-tråden
        self.assertEqual(6, len(loop.calls))  # 3 on_raw + status + event + location, alle i kø
        loop.drain()
        self.assertEqual(6, len(seen_threads))

    def test_disconnect_then_connect_rebuilds_client_and_resubscribes(self):
        mqtt, client = self._connected_mqtt()
        mqtt.subscribe_device("A", on_status_update=mock.Mock())
        mqtt.disconnect()
        mqtt.connect()
        new_client = mqtt._sync_client
        self.assertIsNot(client, new_client)
        new_client.on_connect(new_client, None, None, 0)
        self.assertIn("/downlink/vehicle/A/realtimeDate/state", new_client.subscriptions)

    def test_failing_callbacks_on_no_loop_path_do_not_swallow_the_message(self):
        import logging

        logging.getLogger("mower_sdk.mqtt").disabled = True
        self.addCleanup(setattr, logging.getLogger("mower_sdk.mqtt"), "disabled", False)
        mqtt = self.mqtt_module.MowerMQTT("broker", 1883, subscribe_location=True)
        mqtt.on_raw = lambda *_a: 1 / 0
        status, location = mock.Mock(), mock.Mock()
        location.side_effect = [ZeroDivisionError(), None]
        mqtt.subscribe_device("A", on_status_update=status, on_location=location)
        client = mqtt._sync_client
        client.on_message(
            client, None, _make_message(mqtt._get_status_topic("A"), {"state": "isRunning"})
        )
        client.on_message(
            client,
            None,
            _make_message(
                mqtt._get_location_topic("A"),
                [POSITION_POINT, dict(POSITION_POINT, time=1755000001)],
            ),
        )
        self.assertEqual(1, status.call_count)
        self.assertEqual(2, location.call_count)

    def test_raw_callback_isolation_in_navimow_sdk(self):
        import logging

        logging.getLogger("mower_sdk.sdk").disabled = True
        self.addCleanup(setattr, logging.getLogger("mower_sdk.sdk"), "disabled", False)
        sdk = self.sdk_module.NavimowSDK("broker", 1883)
        seen = []
        sdk.on_raw(lambda *_a: 1 / 0)
        sdk.on_raw(lambda t, p: seen.append(t))
        asyncio.run(sdk._mqtt.on_raw("t", b"x"))
        self.assertEqual(["t"], seen)

    def test_invalid_extra_topics_are_rejected_early(self):
        for bad in ([""], ["a\x00b"], [None]):
            with self.assertRaises(ValueError):
                self.mqtt_module.NavimowMQTT("broker", 1883, None, None, [], extra_topics=bad)
            with self.assertRaises(ValueError):
                self.mqtt_module.MowerMQTT("broker", 1883, extra_topics=bad)

    def test_subscribe_failure_in_on_connect_is_logged_not_raised(self):
        import logging

        logging.getLogger("mower_sdk.mqtt").disabled = True
        self.addCleanup(setattr, logging.getLogger("mower_sdk.mqtt"), "disabled", False)
        mqtt = self.mqtt_module.MowerMQTT("broker", 1883)
        mqtt.connect()
        client = mqtt._sync_client
        client.subscribe = mock.Mock(side_effect=ValueError("bad topic"))
        mqtt.subscribe_device("A", on_status_update=mock.Mock())
        client.on_connect(client, None, None, 0)  # må ikkje kaste
        self.assertTrue(mqtt._connected)

    def test_subscribe_device_only_subscribes_the_new_device(self):
        mqtt, client = self._connected_mqtt()
        for name in ("A", "B", "C"):
            mqtt.subscribe_device(name, on_status_update=mock.Mock())
        self.assertEqual(6, len(client.subscriptions))  # 2 emne × 3 einingar, ingen dublettar

    def test_state_from_dict_does_not_mutate_caller_metrics(self):
        metrics = {"a": 1}
        self.models.DeviceStateMessage.from_dict({"state": "isRunning", "metrics": metrics})
        self.assertEqual({"a": 1}, metrics)

    def test_navimow_sdk_partial_state_keeps_cached_battery_and_state(self):
        sdk = self.sdk_module.NavimowSDK("broker", 1883)
        topic = f"/downlink/vehicle/{DEVICE_ID}/realtimeDate/state"
        asyncio.run(
            sdk._on_mqtt_message(topic, b'{"state": "isRunning", "battery": 66}', DEVICE_ID)
        )
        asyncio.run(sdk._on_mqtt_message(topic, b'{"signal_strength": 4}', DEVICE_ID))
        cached = sdk.get_cached_state(DEVICE_ID)
        self.assertEqual(66, cached.battery)
        self.assertEqual("mowing", cached.state)
        asyncio.run(sdk._on_mqtt_message(topic, b'{"state": "offline"}', DEVICE_ID))
        self.assertEqual("unknown", sdk.get_cached_state(DEVICE_ID).state)

    def test_concurrent_async_subscriptions_share_one_client(self):
        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)
        mqtt = self.mqtt_module.MowerMQTT("broker", 1883, loop=loop)
        cb_a, cb_b = mock.Mock(), mock.Mock()

        async def scenario():
            await mqtt.async_connect()
            t1 = asyncio.ensure_future(mqtt.async_subscribe_device("A", on_status_update=cb_a))
            await asyncio.sleep(0)
            client = mqtt._async_client
            client.on_connect(client, None, None, 0)
            t2 = asyncio.ensure_future(mqtt.async_subscribe_device("B", on_status_update=cb_b))
            await asyncio.sleep(0)
            self.assertIs(client, mqtt._async_client)
            client.on_message(
                client, None, _make_message(mqtt._get_status_topic("A"), {"state": "isRunning"})
            )
            await asyncio.sleep(0)
            self.assertEqual(1, cb_a.call_count)
            self.assertEqual(0, cb_b.call_count)
            self.assertIn("/downlink/vehicle/B/realtimeDate/state", client.subscriptions)
            mqtt._async_stop_event.set()
            await asyncio.gather(t1, t2)

        loop.run_until_complete(scenario())

    def test_connect_rebuilds_client_when_credentials_change(self):
        mqtt = self.mqtt_module.MowerMQTT("broker", 1883, username="u1", password="p1")
        mqtt.connect()
        first = mqtt._sync_client
        mqtt.connect()
        self.assertIs(first, mqtt._sync_client)  # uendra oppsett: attbruk
        mqtt.configure_wss("broker", "/mqtt", "u2", "p2", {"Authorization": "Bearer T2"})
        mqtt.connect()
        self.assertIsNot(first, mqtt._sync_client)
        self.assertEqual(("u2", "p2"), mqtt._sync_client.username_password)

    def test_state_event_attribute_callbacks_are_isolated(self):
        import logging

        logging.getLogger("mower_sdk.sdk").disabled = True
        self.addCleanup(setattr, logging.getLogger("mower_sdk.sdk"), "disabled", False)
        sdk = self.sdk_module.NavimowSDK("broker", 1883)
        seen = []
        for register in (sdk.on_state, sdk.on_event, sdk.on_attributes):
            register(lambda _m: 1 / 0)
            register(seen.append)
        for channel, body in (
            ("state", b'{"state": "isRunning"}'),
            ("event", b'{"event": "x"}'),
            ("attributes", b'{"attributes": {}}'),
        ):
            asyncio.run(
                sdk._on_mqtt_message(
                    f"/downlink/vehicle/{DEVICE_ID}/realtimeDate/{channel}", body, DEVICE_ID
                )
            )
        self.assertEqual(3, len(seen))

    def test_device_status_from_dict_keeps_status_first_precedence(self):
        status = self.models.DeviceStatus.from_dict({"status": "docked", "state": "mowing"})
        self.assertEqual(self.models.MowerStatus.DOCKED, status.status)
        message = self.models.DeviceStateMessage.from_dict({"status": "docked", "state": "mowing"})
        self.assertEqual("mowing", message.state)

    def test_navimow_mqtt_never_invokes_callables_on_paho_thread(self):
        import threading

        loop = _RecordingLoop()
        mqtt = self.mqtt_module.NavimowMQTT("broker", 1883, None, None, [], loop=loop)
        threads = []

        class Hook:
            def __call__(self, *args):
                threads.append(threading.current_thread().name)

                async def coro():
                    return None

                return coro()

        mqtt.on_raw = Hook()
        mqtt.on_message = Hook()
        mqtt.on_ready = Hook()

        def paho_thread():
            mqtt._on_connect(mqtt.client, None, None, 0)
            mqtt._on_message(
                mqtt.client,
                None,
                _make_message(f"/downlink/vehicle/{DEVICE_ID}/realtimeDate/state", {"state": "x"}),
            )

        t = threading.Thread(target=paho_thread, name="paho-net")
        t.start()
        t.join()
        self.assertEqual([], threads)  # ingen brukarkode køyrde på paho-tråden
        self.assertEqual(3, len(loop.calls))  # ready + raw + message, alle i kø

    def test_failed_connect_can_be_retried(self):
        mqtt = self.mqtt_module.MowerMQTT("broker", 1883)
        with mock.patch.object(FakePahoClient, "connect", side_effect=OSError("down")):
            with self.assertRaises(self.mqtt_module.MowerMQTTError):
                mqtt.connect()
        self.assertIsNone(mqtt._sync_client)
        mqtt.connect()  # ny klient, ekte connect
        self.assertEqual(1, len(mqtt._sync_client.connect_calls))

    def test_failed_async_subscribe_can_be_retried(self):
        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)
        mqtt = self.mqtt_module.MowerMQTT("broker", 1883, loop=loop)

        async def scenario():
            await mqtt.async_connect()
            with mock.patch.object(FakePahoClient, "connect", side_effect=OSError("down")):
                with self.assertRaises(self.mqtt_module.MowerMQTTError):
                    await mqtt.async_subscribe_device("A", on_status_update=mock.Mock())
            self.assertIsNone(mqtt._async_client)
            task = asyncio.ensure_future(mqtt.async_subscribe_device("A"))
            await asyncio.sleep(0)
            self.assertIsNotNone(mqtt._async_client)  # bygd på nytt, ikkje hengande
            self.assertEqual(1, len(mqtt._async_client.connect_calls))
            mqtt._async_stop_event.set()
            await task

        loop.run_until_complete(scenario())

    def test_async_disconnect_ends_session_and_next_subscribe_rebuilds(self):
        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)
        mqtt = self.mqtt_module.MowerMQTT("broker", 1883, loop=loop)

        async def scenario():
            await mqtt.async_connect()
            first = asyncio.ensure_future(mqtt.async_subscribe_device("A"))
            await asyncio.sleep(0)
            client = mqtt._async_client
            client.on_connect(client, None, None, 0)
            self.assertTrue(mqtt._async_connected)
            client.on_disconnect(client, None, None, 0)
            await asyncio.sleep(0)
            await first  # kontrakt: kallet returnerer når tilkoplinga blir broten
            self.assertFalse(mqtt._async_connected)
            second = asyncio.ensure_future(mqtt.async_subscribe_device("B"))
            await asyncio.sleep(0)
            self.assertFalse(second.done())  # ikkje umiddelbar retur frå gamal hending
            self.assertIsNot(client, mqtt._async_client)  # ny klient for ny økt
            mqtt._async_stop_event.set()
            await second

        loop.run_until_complete(scenario())

    def test_cancelling_owner_keeps_session_alive_for_other_subscribers(self):
        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)
        mqtt = self.mqtt_module.MowerMQTT("broker", 1883, loop=loop)
        cb_b = mock.Mock()

        async def scenario():
            owner = asyncio.ensure_future(
                mqtt.async_subscribe_device("A", on_status_update=mock.Mock())
            )
            await asyncio.sleep(0)
            client = mqtt._async_client
            client.on_connect(client, None, None, 0)
            waiter = asyncio.ensure_future(mqtt.async_subscribe_device("B", on_status_update=cb_b))
            await asyncio.sleep(0)

            owner.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await owner

            self.assertIs(client, mqtt._async_client)  # økta lever vidare
            self.assertEqual(0, client.disconnect_calls)
            self.assertNotIn("A", mqtt._callbacks)  # A sine tilbakekall er fjerna
            client.on_message(
                client, None, _make_message(mqtt._get_status_topic("B"), {"state": "isRunning"})
            )
            await asyncio.sleep(0)
            self.assertEqual(1, cb_b.call_count)
            self.assertFalse(waiter.done())

            waiter.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiter
            self.assertIsNone(mqtt._async_client)  # siste deltakar ute: økta stoppa
            self.assertEqual(1, client.disconnect_calls)

        loop.run_until_complete(scenario())

    def test_sync_and_async_connected_flags_are_independent(self):
        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)
        mqtt = self.mqtt_module.MowerMQTT("broker", 1883, loop=loop)
        mqtt.connect()
        sync_client = mqtt._sync_client
        sync_client.on_connect(sync_client, None, None, 0)
        self.assertTrue(mqtt._connected)
        loop.run_until_complete(mqtt.async_disconnect())
        self.assertTrue(mqtt._connected)  # den synkrone klienten er framleis oppe
        mqtt.subscribe_device("B", on_status_update=mock.Mock())
        self.assertIn("/downlink/vehicle/B/realtimeDate/state", sync_client.subscriptions)

    def test_bool_posture_values_are_rejected(self):
        msg = self.models.DeviceLocationMessage.from_dict({"postureX": True, "vehicleState": True})
        self.assertIsNone(msg.x)
        self.assertIsNone(msg.vehicle_state)

    def test_mqtt_state_message_prefers_state_over_status_in_conversion(self):
        status = self._state_status({"state": "isRunning", "status": "docked"})
        self.assertEqual(self.models.MowerStatus.MOWING, status.status)

    def test_untyped_reading_does_not_disturb_typed_watermark(self):
        flt = self._filter()
        untyped = dict(POSITION_POINT, time=150)
        untyped.pop("type")
        accepted = flt.filter(
            self._point(time=200)
            + self.models.parse_location_payload(untyped, DEVICE_ID)
            + self._point(time=199)
        )
        self.assertEqual([200, 150], [p.timestamp for p in accepted])

    def test_loop_bound_after_sync_subscribe_is_used_for_dispatch(self):
        mqtt = self.mqtt_module.MowerMQTT("broker", 1883)
        callback = mock.Mock()
        mqtt.subscribe_device("A", on_status_update=callback)  # inga løkke enno
        loop = _RecordingLoop()
        mqtt._loop = loop  # løkka blir bunden seinare (t.d. via async_connect)
        client = mqtt._sync_client
        client.on_message(
            client, None, _make_message(mqtt._get_status_topic("A"), {"state": "isRunning"})
        )
        self.assertEqual(0, callback.call_count)  # ikkje kalla på paho-tråden
        self.assertEqual(1, len(loop.calls))
        loop.drain()
        self.assertEqual(1, callback.call_count)

    def test_normal_return_removes_device_callbacks(self):
        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)
        mqtt = self.mqtt_module.MowerMQTT("broker", 1883, loop=loop)
        cb_a = mock.Mock()

        async def scenario():
            task = asyncio.ensure_future(mqtt.async_subscribe_device("A", on_status_update=cb_a))
            await asyncio.sleep(0)
            client = mqtt._async_client
            client.on_connect(client, None, None, 0)
            client.on_disconnect(client, None, None, 0)
            await task
            self.assertNotIn("A", mqtt._callbacks)
            self.assertEqual(0, mqtt._async_waiters)
            # Ny økt for B må ikkje teikne opp A att
            second = asyncio.ensure_future(mqtt.async_subscribe_device("B"))
            await asyncio.sleep(0)
            c2 = mqtt._async_client
            c2.on_connect(c2, None, None, 0)
            self.assertNotIn("/downlink/vehicle/A/realtimeDate/state", c2.subscriptions)
            mqtt._async_stop_event.set()
            await second

        loop.run_until_complete(scenario())

    def test_stale_on_disconnect_does_not_poison_new_session(self):
        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)
        mqtt = self.mqtt_module.MowerMQTT("broker", 1883, loop=loop)

        async def scenario():
            first = asyncio.ensure_future(mqtt.async_subscribe_device("A"))
            await asyncio.sleep(0)
            c1 = mqtt._async_client
            c1.on_connect(c1, None, None, 0)
            c1.on_disconnect(c1, None, None, 0)
            await first
            second = asyncio.ensure_future(mqtt.async_subscribe_device("B"))
            await asyncio.sleep(0)
            c2 = mqtt._async_client
            c2.on_connect(c2, None, None, 0)
            c1.on_disconnect(c1, None, None, 0)  # seint tilbakekall frå gammal økt
            self.assertTrue(mqtt._async_connected)
            self.assertFalse(mqtt._async_stop_event.is_set())
            third = asyncio.ensure_future(mqtt.async_subscribe_device("C"))
            await asyncio.sleep(0)
            self.assertIn("/downlink/vehicle/C/realtimeDate/state", c2.subscriptions)
            mqtt._async_stop_event.set()
            await asyncio.gather(second, third)

        loop.run_until_complete(scenario())

    def test_async_disconnect_wakes_all_waiters_and_stops_once(self):
        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)
        mqtt = self.mqtt_module.MowerMQTT("broker", 1883, loop=loop)

        async def scenario():
            tasks = [asyncio.ensure_future(mqtt.async_subscribe_device(d)) for d in "ABC"]
            await asyncio.sleep(0)
            client = mqtt._async_client
            client.on_connect(client, None, None, 0)
            await mqtt.async_disconnect()
            await asyncio.gather(*tasks)
            self.assertEqual(0, mqtt._async_waiters)
            self.assertEqual(1, client.disconnect_calls)
            self.assertEqual(1, client.loop_stop_calls)
            self.assertIsNone(mqtt._async_client)

        loop.run_until_complete(scenario())

    def test_subscribe_during_drop_window_starts_a_fresh_session(self):
        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)
        mqtt = self.mqtt_module.MowerMQTT("broker", 1883, loop=loop)

        async def scenario():
            first = asyncio.ensure_future(mqtt.async_subscribe_device("A"))
            await asyncio.sleep(0)
            c1 = mqtt._async_client
            c1.on_connect(c1, None, None, 0)
            # paho-tråden har sett _async_connected=False, men stopp-hendinga er enno i kø
            mqtt._async_connected = False
            second = asyncio.ensure_future(mqtt.async_subscribe_device("B"))
            for _ in range(5):
                await asyncio.sleep(0)
            self.assertIsNot(c1, mqtt._async_client)  # ny økt, ikkje den døyande
            c2 = mqtt._async_client
            c2.on_connect(c2, None, None, 0)
            self.assertIn("/downlink/vehicle/B/realtimeDate/state", c2.subscriptions)
            first.cancel()
            mqtt._async_stop_event.set()
            await asyncio.gather(first, second, return_exceptions=True)

        loop.run_until_complete(scenario())

    def test_sync_subscribe_inside_running_loop_binds_it_without_loop_argument(self):
        callback = mock.Mock()

        async def scenario():
            mqtt = self.mqtt_module.MowerMQTT("broker", 1883)  # inga loop=
            mqtt.subscribe_device("A", on_status_update=callback)
            self.assertIs(asyncio.get_running_loop(), mqtt.loop)
            client = mqtt._sync_client
            client.on_message(
                client, None, _make_message(mqtt._get_status_topic("A"), {"state": "isRunning"})
            )
            self.assertEqual(0, callback.call_count)  # i kø på løkka, ikkje kalla direkte
            await asyncio.sleep(0)
            self.assertEqual(1, callback.call_count)

        asyncio.run(scenario())

    def test_async_late_location_enable_covers_earlier_devices(self):
        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)
        mqtt = self.mqtt_module.MowerMQTT("broker", 1883, loop=loop)

        async def scenario():
            a = asyncio.ensure_future(mqtt.async_subscribe_device("A"))
            await asyncio.sleep(0)
            client = mqtt._async_client
            client.on_connect(client, None, None, 0)
            mqtt.subscribe_location = True
            b = asyncio.ensure_future(mqtt.async_subscribe_device("B"))
            await asyncio.sleep(0)
            self.assertIn("/downlink/vehicle/A/realtimeDate/location", client.subscriptions)
            self.assertIn("/downlink/vehicle/B/realtimeDate/location", client.subscriptions)
            mqtt._async_stop_event.set()
            await asyncio.gather(a, b)

        loop.run_until_complete(scenario())

    def test_non_finite_floats_are_rejected_everywhere(self):
        msg = self.models.DeviceLocationMessage.from_dict(
            {"postureX": float("nan"), "postureY": float("inf"), "mowingPercentage": "-inf"}
        )
        self.assertIsNone(msg.x)
        self.assertIsNone(msg.y)
        self.assertIsNone(msg.mowing_percentage)

    def test_concurrent_subscribers_in_drop_window_share_one_new_session(self):
        import threading
        import time

        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)
        mqtt = self.mqtt_module.MowerMQTT("broker", 1883, loop=loop)
        built = []
        real_build = mqtt._build_client

        def tracking_build():
            client = real_build()
            built.append(client)
            return client

        mqtt._build_client = tracking_build
        slow = threading.Lock()

        def slow_stop(client):
            time.sleep(0.05)  # som paho sin loop_stop().join()
            with slow:
                client.disconnect()
                client.loop_stop()

        async def scenario():
            with mock.patch.object(self.mqtt_module, "_stop_paho_client", slow_stop):
                first = asyncio.ensure_future(mqtt.async_subscribe_device("A"))
                await asyncio.sleep(0)
                c1 = mqtt._async_client
                c1.on_connect(c1, None, None, 0)
                c1.on_disconnect(c1, None, None, 0)  # frå «paho-tråden»
                await first
                # To deltakarar samtidig medan den gamle økta blir riven ned
                b = asyncio.ensure_future(mqtt.async_subscribe_device("B"))
                c = asyncio.ensure_future(mqtt.async_subscribe_device("C"))
                await asyncio.sleep(0.2)
                live = mqtt._async_client
                live.on_connect(live, None, None, 0)
                await asyncio.sleep(0)
                self.assertEqual(2, len(built))  # éi ny økt, ikkje to
                await mqtt.async_disconnect()
                done, _ = await asyncio.wait({b, c}, timeout=1)
                self.assertEqual({b, c}, done)  # ingen heng
                for client in built:
                    self.assertEqual(1, client.disconnect_calls)
                    self.assertEqual(1, client.loop_stop_calls)

        loop.run_until_complete(scenario())

    def test_cancel_during_pending_teardown_still_stops_client(self):
        import concurrent.futures
        import time

        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self.addCleanup(executor.shutdown)
        loop.set_default_executor(executor)
        mqtt = self.mqtt_module.MowerMQTT("broker", 1883, loop=loop)

        async def scenario():
            task = asyncio.ensure_future(mqtt.async_subscribe_device("A"))
            await asyncio.sleep(0)
            client = mqtt._async_client
            client.on_connect(client, None, None, 0)
            # Opptek den einaste utførartråden så nedrivinga må stå i kø
            blocker = loop.run_in_executor(None, time.sleep, 0.15)
            client.on_disconnect(client, None, None, 0)
            await asyncio.sleep(0)  # stopp-hendinga køyrer; finally ventar i kø i utføraren
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await blocker
            await asyncio.sleep(0.05)
            self.assertEqual(1, client.disconnect_calls)
            self.assertEqual(1, client.loop_stop_calls)

        loop.run_until_complete(scenario())

    def test_navimow_mqtt_disconnect_keeps_original_order_without_disconnected_callback(self):
        loop = _RecordingLoop()
        mqtt = self.mqtt_module.NavimowMQTT("broker", 1883, None, None, [], loop=loop)
        mqtt.on_disconnected = mock.Mock()
        order = []
        mqtt.client.loop_stop = lambda: order.append("loop_stop")
        mqtt.client.disconnect = lambda: order.append("disconnect")
        mqtt.disconnect()
        self.assertEqual(["loop_stop", "disconnect"], order)
        self.assertEqual([], loop.calls)  # ingen on_disconnected planlagd

    def test_mower_client_forwards_location_arguments_to_both_wrappers(self):
        from mower_sdk.client import MowerClient

        client = MowerClient(session=mock.Mock(), token="t", mqtt_broker="broker")
        on_location = mock.Mock()
        with (
            mock.patch.object(client, "refresh_mqtt_info"),
            mock.patch.object(client.mqtt, "connect"),
            mock.patch.object(client.mqtt, "subscribe_device") as sub,
        ):
            client.subscribe_device_updates("A", on_location=on_location, subscribe_location=True)
        sub.assert_called_once_with(device_id="A", on_status_update=None, on_location=on_location)
        self.assertTrue(client.mqtt.subscribe_location)

        client2 = MowerClient(session=mock.Mock(), token="t", mqtt_broker="broker")
        with (
            mock.patch.object(client2, "async_refresh_mqtt_info", new=mock.AsyncMock()),
            mock.patch.object(client2.mqtt, "async_connect", new=mock.AsyncMock()),
            mock.patch.object(client2.mqtt, "async_subscribe_device", new=mock.AsyncMock()) as asub,
        ):
            asyncio.run(
                client2.async_subscribe_device_updates(
                    "B", on_location=on_location, subscribe_location=True
                )
            )
        asub.assert_awaited_once_with(device_id="B", on_status_update=None, on_location=on_location)
        self.assertTrue(client2.mqtt.subscribe_location)

    def test_async_session_is_rebuilt_when_credentials_change(self):
        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)
        mqtt = self.mqtt_module.MowerMQTT("broker", 1883, username="u1", password="p1", loop=loop)

        async def scenario():
            a = asyncio.ensure_future(mqtt.async_subscribe_device("A"))
            await asyncio.sleep(0)
            c1 = mqtt._async_client
            c1.on_connect(c1, None, None, 0)
            mqtt.configure_wss("broker", "/mqtt", "u2", "p2", {"Authorization": "Bearer T2"})
            b = asyncio.ensure_future(mqtt.async_subscribe_device("B"))
            await asyncio.sleep(0.05)
            c2 = mqtt._async_client
            self.assertIsNot(c1, c2)
            self.assertEqual(("u2", "p2"), c2.username_password)
            self.assertEqual(1, c1.disconnect_calls)
            done, _ = await asyncio.wait({a}, timeout=0.5)
            self.assertEqual({a}, done)  # A vart vekt: økta hans er over
            c2.on_connect(c2, None, None, 0)
            self.assertIn("/downlink/vehicle/B/realtimeDate/state", c2.subscriptions)
            mqtt._async_stop_event.set()
            await b

        loop.run_until_complete(scenario())

    def test_cancelling_rebuilder_inside_lock_wakes_old_session_and_leaks_nothing(self):
        import concurrent.futures
        import time

        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self.addCleanup(executor.shutdown)
        loop.set_default_executor(executor)
        mqtt = self.mqtt_module.MowerMQTT("broker", 1883, username="u1", password="p1", loop=loop)

        async def scenario():
            a = asyncio.ensure_future(mqtt.async_subscribe_device("A"))
            await asyncio.sleep(0)
            c1 = mqtt._async_client
            c1.on_connect(c1, None, None, 0)
            blocker = loop.run_in_executor(None, time.sleep, 0.15)  # utføraren er oppteken
            mqtt.configure_wss("broker", "/mqtt", "u2", "p2", None)  # tvingar ombygging
            b = asyncio.ensure_future(mqtt.async_subscribe_device("B"))
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            b.cancel()  # avbrote medan nedrivinga står i kø i utføraren, inne i låsen
            with self.assertRaises(asyncio.CancelledError):
                await b
            done, _ = await asyncio.wait({a}, timeout=0.5)
            self.assertEqual({a}, done)  # A vart vekt, ikkje etterlaten
            await blocker
            await asyncio.sleep(0.05)
            self.assertEqual(0, mqtt._async_waiters)
            self.assertEqual({}, mqtt._callbacks)
            self.assertEqual(1, c1.disconnect_calls)
            # og ei ny økt kan framleis startast og rivast ned korrekt
            c = asyncio.ensure_future(mqtt.async_subscribe_device("C"))
            await asyncio.sleep(0)
            c3 = mqtt._async_client
            c3.on_connect(c3, None, None, 0)
            await mqtt.async_disconnect()
            await c
            self.assertEqual(1, c3.loop_stop_calls)

        loop.run_until_complete(scenario())

    def test_in_lock_subscribe_failure_is_wrapped_and_forgets_callbacks(self):
        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)
        mqtt = self.mqtt_module.MowerMQTT("broker", 1883, loop=loop)

        async def scenario():
            a = asyncio.ensure_future(mqtt.async_subscribe_device("A"))
            await asyncio.sleep(0)
            client = mqtt._async_client
            client.on_connect(client, None, None, 0)
            client.subscribe = mock.Mock(side_effect=ValueError("bad"))
            with self.assertRaises(self.mqtt_module.MowerMQTTError):
                await mqtt.async_subscribe_device("B")
            self.assertNotIn("B", mqtt._callbacks)
            self.assertIn("A", mqtt._callbacks)
            mqtt._async_stop_event.set()
            await a

        loop.run_until_complete(scenario())

    def test_async_disconnect_serialised_with_subscribe(self):
        import concurrent.futures
        import time

        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self.addCleanup(executor.shutdown)
        loop.set_default_executor(executor)
        mqtt = self.mqtt_module.MowerMQTT("broker", 1883, loop=loop)

        async def scenario():
            a = asyncio.ensure_future(mqtt.async_subscribe_device("A"))
            await asyncio.sleep(0)
            c1 = mqtt._async_client
            c1.on_connect(c1, None, None, 0)
            blocker = loop.run_in_executor(None, time.sleep, 0.1)
            disconnecting = asyncio.ensure_future(mqtt.async_disconnect())
            await asyncio.sleep(0)
            b = asyncio.ensure_future(mqtt.async_subscribe_device("B"))  # ventar på låsen
            await disconnecting
            await blocker
            await asyncio.sleep(0.05)
            self.assertEqual(1, c1.loop_stop_calls)
            # B fekk starte først etter at fråkoplinga var ferdig, i ei ny økt
            c2 = mqtt._async_client
            self.assertIsNot(c1, c2)
            mqtt._async_stop_event.set()
            await asyncio.gather(a, b)

        loop.run_until_complete(scenario())
