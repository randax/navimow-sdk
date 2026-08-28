import importlib
import os
import sys
import types


class FakePahoClient:
    def __init__(self, callback_api_version=None, client_id=None, transport="tcp"):
        self.callback_api_version = callback_api_version
        self.client_id = client_id
        self.transport = transport
        self.connected = False
        self.username_password = None
        self.ws_options = None
        self.tls_enabled = False
        self.reconnect_delay = None
        self.published = []
        self.subscriptions = []
        self.unsubscriptions = []
        self.connect_async_calls = []
        self.connect_calls = []
        self.loop_start_calls = 0
        self.loop_stop_calls = 0
        self.disconnect_calls = 0
        self.on_connect = None
        self.on_disconnect = None
        self.on_message = None

    def username_pw_set(self, username, password):
        self.username_password = (username, password)

    def ws_set_options(self, path=None, headers=None):
        self.ws_options = {"path": path, "headers": headers}

    def tls_set(self):
        self.tls_enabled = True

    def reconnect_delay_set(self, min_delay=None, max_delay=None):
        self.reconnect_delay = {"min_delay": min_delay, "max_delay": max_delay}

    def connect_async(self, broker, port, keepalive):
        self.connect_async_calls.append((broker, port, keepalive))

    def connect(self, broker, port, keepalive):
        self.connect_calls.append((broker, port, keepalive))
        self.connected = True

    def loop_start(self):
        self.loop_start_calls += 1

    def loop_stop(self):
        self.loop_stop_calls += 1

    def disconnect(self):
        self.disconnect_calls += 1
        self.connected = False

    def subscribe(self, topic):
        self.subscriptions.append(topic)

    def unsubscribe(self, topic):
        self.unsubscriptions.append(topic)

    def publish(self, topic, payload):
        self.published.append((topic, payload))

    def is_connected(self):
        return self.connected


def purge_modules(prefix):
    for name in list(sys.modules):
        if name == prefix or name.startswith(prefix + "."):
            sys.modules.pop(name, None)


def install_dependency_stubs():
    aiohttp_module = types.ModuleType("aiohttp")

    class ClientError(Exception):
        pass

    class ClientSession:
        pass

    aiohttp_module.ClientError = ClientError
    aiohttp_module.ClientSession = ClientSession

    paho_module = types.ModuleType("paho")
    mqtt_module = types.ModuleType("paho.mqtt")
    client_module = types.ModuleType("paho.mqtt.client")
    client_module.Client = FakePahoClient
    client_module.CallbackAPIVersion = types.SimpleNamespace(VERSION2=2)
    mqtt_module.client = client_module
    paho_module.mqtt = mqtt_module

    sys.modules["aiohttp"] = aiohttp_module
    sys.modules["paho"] = paho_module
    sys.modules["paho.mqtt"] = mqtt_module
    sys.modules["paho.mqtt.client"] = client_module


def import_fresh(module_name):
    install_dependency_stubs()
    purge_modules("mower_sdk")
    return importlib.import_module(module_name)


def import_source_module(module_name):
    install_dependency_stubs()
    purge_modules("mower_sdk")

    package_module = types.ModuleType("mower_sdk")
    package_module.__path__ = [os.path.join(os.getcwd(), "mower_sdk")]
    package_module.__file__ = os.path.join(os.getcwd(), "mower_sdk", "__init__.py")
    sys.modules["mower_sdk"] = package_module

    return importlib.import_module(module_name)
