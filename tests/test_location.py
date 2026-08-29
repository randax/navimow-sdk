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
        }
        for (cls, name), prefix in expected.items():
            params = list(inspect.signature(getattr(cls, name)).parameters)
            self.assertEqual(prefix, params[: len(prefix)], f"{cls.__name__}.{name}")

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
