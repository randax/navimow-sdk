import asyncio
import types
import unittest
from unittest import mock

from tests.support import import_source_module


class RecordingLoop:
    def __init__(self, running=False, closed=False):
        self.running = running
        self.closed = closed
        self.calls = []

    def is_running(self):
        return self.running

    def is_closed(self):
        return self.closed

    def call_soon_threadsafe(self, callback, *args):
        self.calls.append((callback, args))


class TrackingAwaitable:
    def __init__(self):
        self.closed = False

    def __await__(self):
        if False:
            yield None
        return None

    def close(self):
        self.closed = True


class ReasonCode:
    def __init__(self, is_failure=False):
        self.is_failure = is_failure

    def __str__(self):
        return "success" if not self.is_failure else "failure"


class LoopBindingTests(unittest.TestCase):
    def test_navimow_sdk_constructs_without_a_running_loop(self):
        module = import_source_module("mower_sdk.sdk")
        sdk = module.NavimowSDK("broker", 1883)
        self.assertIsNone(sdk._loop)
        with self.assertRaisesRegex(RuntimeError, "explicit loop= argument"):
            sdk.connect()

    def test_navimow_mqtt_constructs_without_a_running_loop(self):
        module = import_source_module("mower_sdk.mqtt")
        mqtt = module.NavimowMQTT("broker", 1883, None, None, [])
        self.assertIsNone(mqtt.loop)
        with self.assertRaisesRegex(RuntimeError, "explicit loop= argument"):
            mqtt.connect_async()

    def test_navimow_sdk_rejects_closed_supplied_loop(self):
        module = import_source_module("mower_sdk.sdk")
        loop = asyncio.new_event_loop()
        try:
            loop.close()
            with self.assertRaises(RuntimeError):
                module.NavimowSDK("broker", 1883, loop=loop)
        finally:
            if not loop.is_closed():
                loop.close()

    def test_navimow_mqtt_accepts_open_non_running_supplied_loop(self):
        module = import_source_module("mower_sdk.mqtt")
        loop = asyncio.new_event_loop()
        try:
            mqtt = module.NavimowMQTT("broker", 1883, None, None, [], loop=loop)
            self.assertIs(mqtt.loop, loop)
        finally:
            loop.close()

    def test_navimow_cloud_reuses_bound_mqtt_loop(self):
        mqtt_module = import_source_module("mower_sdk.mqtt")
        cloud_module = import_source_module("mower_sdk.cloud")
        loop = asyncio.new_event_loop()
        try:
            mqtt = mqtt_module.NavimowMQTT("broker", 1883, None, None, [], loop=loop)
            cloud = cloud_module.NavimowCloud(mqtt, cloud_client=object())
            self.assertIs(cloud.loop, loop)
        finally:
            loop.close()

    def test_navimow_mqtt_rejects_late_callbacks_after_loop_close(self):
        module = import_source_module("mower_sdk.mqtt")
        loop = RecordingLoop(running=False, closed=False)
        mqtt = module.NavimowMQTT("broker", 1883, None, None, [], loop=loop)
        loop.closed = True

        async def callback():
            return None

        mqtt._schedule(callback())
        self.assertEqual([], loop.calls)

    def test_navimow_mqtt_schedules_callbacks_threadsafely_on_bound_loop(self):
        module = import_source_module("mower_sdk.mqtt")
        loop = RecordingLoop(running=True, closed=False)
        mqtt = module.NavimowMQTT("broker", 1883, None, None, [], loop=loop)
        scheduled = []

        async def on_ready():
            scheduled.append("ready")

        mqtt.on_ready = on_ready
        mqtt._on_connect(mqtt.client, None, None, ReasonCode(), None)

        self.assertEqual(1, len(loop.calls))
        callback, args = loop.calls[0]
        self.assertIs(callback, asyncio.create_task)
        self.assertEqual(1, len(args))
        args[0].close()


class RunningLoopBindingTests(unittest.IsolatedAsyncioTestCase):
    async def test_navimow_sdk_uses_current_running_loop_when_loop_omitted(self):
        module = import_source_module("mower_sdk.sdk")
        sdk = module.NavimowSDK("broker", 1883)
        self.assertIsNone(sdk._loop)
        sdk.connect()
        self.assertIs(sdk._loop, asyncio.get_running_loop())
        self.assertIs(sdk._mqtt.loop, asyncio.get_running_loop())

    async def test_navimow_sdk_prefers_supplied_loop_over_current_running_loop(self):
        module = import_source_module("mower_sdk.sdk")
        supplied_loop = asyncio.new_event_loop()
        try:
            sdk = module.NavimowSDK("broker", 1883, loop=supplied_loop)
            self.assertIs(sdk._loop, supplied_loop)
            self.assertIsNot(sdk._loop, asyncio.get_running_loop())
            with self.assertRaisesRegex(RuntimeError, "different event loop"):
                sdk.connect()
        finally:
            supplied_loop.close()

    async def test_async_subscribe_device_schedules_status_callback_threadsafely(self):
        module = import_source_module("mower_sdk.mqtt")
        mqtt = module.MowerMQTT("broker", 1883)
        callback = mock.Mock()
        subscribe_task = asyncio.create_task(
            mqtt.async_subscribe_device("device-1", on_status_update=callback)
        )
        await asyncio.sleep(0)

        loop = asyncio.get_running_loop()
        with mock.patch.object(
            loop, "call_soon_threadsafe", wraps=loop.call_soon_threadsafe
        ) as scheduler:
            message = types.SimpleNamespace(
                topic=mqtt._get_status_topic("device-1"),
                payload=b'{"device_id": "device-1", "status": "idle", "battery": 87}',
            )
            mqtt._async_client.on_message(mqtt._async_client, None, message)
            await asyncio.sleep(0)
            mqtt._async_client.on_disconnect(mqtt._async_client, None, None, ReasonCode(), None)
            await subscribe_task

        callback.assert_called_once()
        scheduler.assert_called()

    async def test_async_subscribe_device_schedules_event_callback_threadsafely(self):
        module = import_source_module("mower_sdk.mqtt")
        mqtt = module.MowerMQTT("broker", 1883)
        callback = mock.Mock()
        subscribe_task = asyncio.create_task(
            mqtt.async_subscribe_device("device-1", on_event=callback)
        )
        await asyncio.sleep(0)

        loop = asyncio.get_running_loop()
        with mock.patch.object(
            loop, "call_soon_threadsafe", wraps=loop.call_soon_threadsafe
        ) as scheduler:
            message = types.SimpleNamespace(
                topic=mqtt._get_event_topic("device-1"),
                payload=b'{"device_id": "device-1", "event": "rain"}',
            )
            mqtt._async_client.on_message(mqtt._async_client, None, message)
            await asyncio.sleep(0)
            mqtt._async_client.on_disconnect(mqtt._async_client, None, None, ReasonCode(), None)
            await subscribe_task

        callback.assert_called_once()
        scheduler.assert_called()
