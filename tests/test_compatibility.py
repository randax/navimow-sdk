import asyncio
import importlib
import sys
import threading
import types
import typing
import unittest
from enum import Enum


class _FakeAiohttpClientError(Exception):
    pass


class _FakeAiohttpClientSession:
    closed = False


class _FakePahoClient:
    instances = []

    def __init__(self, callback_api_version=None, client_id="", transport="tcp"):
        self.callback_api_version = callback_api_version
        self.client_id = client_id
        self.transport = transport
        self.username = None
        self.password = None
        self.ws_path = None
        self.auth_headers = None
        self.keepalive = None
        self.connected = False
        self.loop_started = False
        self.subscriptions = []
        self.unsubscriptions = []
        self.published = []
        self.connect_async_calls = []
        self.on_connect = None
        self.on_disconnect = None
        self.on_message = None
        _FakePahoClient.instances.append(self)

    def username_pw_set(self, username, password):
        self.username = username
        self.password = password

    def ws_set_options(self, path, headers):
        self.ws_path = path
        self.auth_headers = headers

    def tls_set(self):
        return None

    def reconnect_delay_set(self, min_delay, max_delay):
        return (min_delay, max_delay)

    def connect(self, host, port, keepalive):
        self.connected = True
        self.keepalive = keepalive
        return 0

    def connect_async(self, host, port, keepalive):
        self.connect_async_calls.append((host, port, keepalive))
        self.keepalive = keepalive
        return 0

    def disconnect(self):
        self.connected = False
        return 0

    def loop_start(self):
        self.loop_started = True

    def loop_stop(self):
        self.loop_started = False

    def subscribe(self, topic):
        self.subscriptions.append(topic)
        return (0, 1)

    def unsubscribe(self, topic):
        self.unsubscriptions.append(topic)
        return (0, 1)

    def publish(self, topic, payload):
        self.published.append((topic, payload))
        return (0, 1)

    def is_connected(self):
        return self.connected


def _install_dependency_stubs():
    aiohttp_module = types.ModuleType("aiohttp")
    aiohttp_module.ClientError = _FakeAiohttpClientError
    aiohttp_module.ClientSession = _FakeAiohttpClientSession
    sys.modules["aiohttp"] = aiohttp_module

    paho_module = types.ModuleType("paho")
    mqtt_package = types.ModuleType("paho.mqtt")
    client_module = types.ModuleType("paho.mqtt.client")

    class _CallbackAPIVersion(Enum):
        VERSION2 = 2

    client_module.CallbackAPIVersion = _CallbackAPIVersion
    client_module.Client = _FakePahoClient
    mqtt_package.client = client_module
    paho_module.mqtt = mqtt_package

    sys.modules["paho"] = paho_module
    sys.modules["paho.mqtt"] = mqtt_package
    sys.modules["paho.mqtt.client"] = client_module


_install_dependency_stubs()

for module_name in list(sys.modules):
    if module_name == "mower_sdk" or module_name.startswith("mower_sdk."):
        sys.modules.pop(module_name)

mower_sdk = importlib.import_module("mower_sdk")
cloud_module = importlib.import_module("mower_sdk.cloud")
models_module = importlib.import_module("mower_sdk.models")
mqtt_module = importlib.import_module("mower_sdk.mqtt")
sdk_module = importlib.import_module("mower_sdk.sdk")
state_manager_module = importlib.import_module("mower_sdk.state_manager")


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        _FakePahoClient.instances.clear()

    def test_public_import_and_version(self):
        self.assertEqual(mower_sdk.__version__, "0.2.0")
        self.assertIs(mower_sdk.NavimowSDK, sdk_module.NavimowSDK)

    def test_public_type_hints_resolve_on_python_39(self):
        targets = [
            sdk_module.NavimowSDK.__init__,
            mqtt_module.MowerMQTT.__init__,
            mqtt_module.NavimowMQTT.__init__,
            cloud_module.NavimowCloud.__init__,
            state_manager_module.StateManager.notification,
            models_module.DeviceStatus.from_dict,
        ]
        for target in targets:
            hints = typing.get_type_hints(target)
            self.assertIsInstance(hints, dict)

    def test_navimow_sdk_allows_sync_construction_without_running_loop(self):
        sdk = sdk_module.NavimowSDK("broker.example", 1883)
        self.assertIsNone(sdk._loop)

    def test_navimow_sdk_requires_loop_for_sync_connect_when_unbound(self):
        sdk = sdk_module.NavimowSDK("broker.example", 1883)
        with self.assertRaisesRegex(
            RuntimeError,
            "NavimowSDK.connect\\(\\) requires a running event loop or an explicit loop=",
        ):
            sdk.connect()

    def test_credentials_update_before_loop_binding_does_not_connect(self):
        sdk = sdk_module.NavimowSDK("broker.example", 1883)
        original_client = sdk._mqtt.client

        sdk.update_mqtt_credentials(
            username="updated-user",
            password="updated-password",
            auth_headers={"Authorization": "Bearer updated-token"},
        )

        self.assertIsNone(sdk._loop)
        self.assertIsNone(sdk._mqtt.loop)
        self.assertIsNot(sdk._mqtt.client, original_client)
        self.assertEqual(sdk._mqtt.client.username, "updated-user")
        self.assertEqual(sdk._mqtt.client.password, "updated-password")
        self.assertFalse(sdk._mqtt.client.connected)
        self.assertFalse(sdk._mqtt.client.loop_started)

    def test_disconnected_credentials_update_reconnects_when_loop_is_bound(self):
        loop = asyncio.new_event_loop()
        try:
            sdk = sdk_module.NavimowSDK("broker.example", 1883, loop=loop)
            sdk.connect()
            original_client = sdk._mqtt.client
            original_client.connected = False

            sdk.update_mqtt_credentials(username="updated-user")

            self.assertIs(sdk._mqtt.loop, loop)
            self.assertIsNot(sdk._mqtt.client, original_client)
            self.assertEqual(len(sdk._mqtt.client.connect_async_calls), 1)
            self.assertTrue(sdk._mqtt.client.loop_started)
        finally:
            loop.close()

    def test_credentials_update_after_explicit_disconnect_stays_disconnected(self):
        loop = asyncio.new_event_loop()
        try:
            sdk = sdk_module.NavimowSDK("broker.example", 1883, loop=loop)
            sdk.connect()
            sdk.disconnect()
            original_client = sdk._mqtt.client

            sdk.update_mqtt_credentials(username="updated-user")

            self.assertIsNot(sdk._mqtt.client, original_client)
            self.assertEqual(sdk._mqtt.client.connect_async_calls, [])
            self.assertFalse(sdk._mqtt.client.loop_started)
        finally:
            loop.close()

    def test_credentials_update_with_supplied_loop_waits_for_first_connect(self):
        loop = asyncio.new_event_loop()
        try:
            sdk = sdk_module.NavimowSDK("broker.example", 1883, loop=loop)

            sdk.update_mqtt_credentials(username="updated-user")

            self.assertEqual(sdk._mqtt.client.connect_async_calls, [])
            self.assertFalse(sdk._mqtt.client.loop_started)

            sdk.connect()

            self.assertEqual(len(sdk._mqtt.client.connect_async_calls), 1)
            self.assertTrue(sdk._mqtt.client.loop_started)
        finally:
            loop.close()

    def test_repeated_connect_while_pending_starts_network_loop_once(self):
        loop = asyncio.new_event_loop()
        try:
            sdk = sdk_module.NavimowSDK("broker.example", 1883, loop=loop)

            sdk.connect()
            sdk.connect()

            self.assertEqual(len(sdk._mqtt.client.connect_async_calls), 1)
            self.assertTrue(sdk._mqtt.client.loop_started)
        finally:
            loop.close()

    def test_connected_credentials_update_live_client_for_next_reconnect(self):
        loop = asyncio.new_event_loop()
        try:
            sdk = sdk_module.NavimowSDK(
                "broker.example",
                1883,
                username="initial-user",
                password="initial-password",
                ws_path="/mqtt",
                auth_headers={"Authorization": "Bearer initial-token"},
                loop=loop,
            )
            sdk.connect()
            client = sdk._mqtt.client
            client.connected = True

            sdk.update_mqtt_credentials(
                username="updated-user",
                password="updated-password",
                auth_headers={"Authorization": "Bearer updated-token"},
            )

            self.assertIs(sdk._mqtt.client, client)
            self.assertEqual(client.username, "updated-user")
            self.assertEqual(client.password, "updated-password")
            self.assertEqual(client.auth_headers, {"Authorization": "Bearer updated-token"})
        finally:
            loop.close()

    def test_auth_header_formatting_redacts_every_value(self):
        formatted = mqtt_module._format_auth_headers(
            {
                "Authorization": "Bearer example-token",
                "Cookie": "session=example-cookie",
                "X-Api-Key": "example-api-key",
            }
        )

        self.assertIn("Authorization", formatted)
        self.assertIn("Cookie", formatted)
        self.assertIn("X-Api-Key", formatted)
        self.assertNotIn("example-token", formatted)
        self.assertNotIn("example-cookie", formatted)
        self.assertNotIn("example-api-key", formatted)

    def test_sync_command_preserves_not_connected_error_without_loop(self):
        sdk = sdk_module.NavimowSDK("broker.example", 1883)

        with self.assertRaisesRegex(RuntimeError, "^MQTT not connected$"):
            sdk.start_mowing("device-1")

        self.assertEqual(sdk._mqtt.client.connect_async_calls, [])

    def test_closed_loop_is_rejected_at_construction(self):
        loop = asyncio.new_event_loop()
        loop.close()
        with self.assertRaisesRegex(RuntimeError, "loop is closed"):
            sdk_module.NavimowSDK("broker.example", 1883, loop=loop)

    def test_mower_client_passes_supplied_loop_to_mqtt(self):
        loop = asyncio.new_event_loop()
        try:
            client = mower_sdk.MowerClient(
                session=_FakeAiohttpClientSession(),
                token="token",
                loop=loop,
            )
            self.assertIs(client.mqtt.loop, loop)
        finally:
            loop.close()

    def test_navimow_cloud_accepts_explicit_loop(self):
        loop = asyncio.new_event_loop()
        try:
            mqtt = mqtt_module.NavimowMQTT(
                broker="broker.example",
                port=1883,
                username=None,
                password=None,
                records=[],
                loop=loop,
            )
            cloud = cloud_module.NavimowCloud(mqtt, cloud_client=object(), loop=loop)
            self.assertIs(cloud.loop, loop)
        finally:
            loop.close()

    def test_late_disconnect_callback_on_closed_loop_is_ignored(self):
        loop = asyncio.new_event_loop()
        mqtt = mqtt_module.NavimowMQTT(
            broker="broker.example",
            port=1883,
            username=None,
            password=None,
            records=[],
            loop=loop,
        )
        mqtt.on_disconnected = self._async_noop
        loop.close()
        mqtt._on_disconnect(mqtt.client, None, None, 0, None)

    async def _async_noop(self):
        return None


class LoopBindingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _FakePahoClient.instances.clear()

    async def test_navimow_sdk_uses_current_running_loop_when_unsupplied(self):
        sdk = sdk_module.NavimowSDK("broker.example", 1883)
        sdk.connect()
        self.assertIs(sdk._mqtt.loop, asyncio.get_running_loop())
        sdk.disconnect()

    async def test_async_subscribe_device_uses_threadsafe_stop_event_signal(self):
        mqtt = mqtt_module.MowerMQTT("broker.example", 1883)
        task = asyncio.create_task(mqtt.async_subscribe_device("device-1"))
        await asyncio.sleep(0)

        disconnect_thread = threading.Thread(
            target=mqtt._async_client.on_disconnect,
            args=(mqtt._async_client, None, None, 0, None),
        )
        disconnect_thread.start()
        disconnect_thread.join()

        await asyncio.wait_for(task, timeout=1)


class CrossLoopTests(unittest.TestCase):
    def setUp(self):
        _FakePahoClient.instances.clear()

    def test_supplied_loop_can_queue_callbacks_before_loop_runs(self):
        loop = asyncio.new_event_loop()
        seen = []

        async def on_connected():
            seen.append("connected")

        try:
            sdk = sdk_module.NavimowSDK("broker.example", 1883, loop=loop)
            sdk._mqtt.on_connected = on_connected
            sdk.connect()
            sdk._mqtt._on_connect(sdk._mqtt.client, None, None, 0, None)
            loop.run_until_complete(asyncio.sleep(0))
            loop.run_until_complete(asyncio.sleep(0))
            self.assertEqual(seen, ["connected"])
            sdk.disconnect()
        finally:
            loop.close()

    def test_cross_loop_use_fails_immediately(self):
        bound_loop = asyncio.new_event_loop()
        try:
            sdk = sdk_module.NavimowSDK("broker.example", 1883, loop=bound_loop)

            async def invoke():
                with self.assertRaisesRegex(
                    RuntimeError, "NavimowMQTT is bound to a different event loop"
                ):
                    sdk.connect()

            asyncio.run(invoke())
        finally:
            bound_loop.close()


if __name__ == "__main__":
    unittest.main()
