import subprocess
import sys
import typing
import unittest
from pathlib import Path

from tests.support import import_fresh, import_source_module


class PublicApiCompatibilityTests(unittest.TestCase):
    def test_distribution_name_is_unique_to_the_fork(self):
        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"

        self.assertIn('name = "randax-navimow-sdk"', pyproject.read_text())

    def test_importing_public_package_succeeds(self):
        module = import_fresh("mower_sdk")
        self.assertIn("NavimowSDK", module.__all__)
        self.assertIn("NavimowMQTT", module.__all__)
        self.assertIn("MowerClient", module.__all__)
        self.assertIn("UrllibSession", module.__all__)

    def test_public_package_import_does_not_require_aiohttp(self):
        script = """
import builtins
import sys
import types
from enum import Enum

paho_module = types.ModuleType('paho')
mqtt_module = types.ModuleType('paho.mqtt')
client_module = types.ModuleType('paho.mqtt.client')
class CallbackAPIVersion(Enum):
    VERSION2 = 2
client_module.CallbackAPIVersion = CallbackAPIVersion
client_module.Client = type('Client', (), {})
mqtt_module.client = client_module
paho_module.mqtt = mqtt_module
sys.modules['paho'] = paho_module
sys.modules['paho.mqtt'] = mqtt_module
sys.modules['paho.mqtt.client'] = client_module

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name == 'aiohttp' or name.startswith('aiohttp.'):
        raise AssertionError('mower_sdk imported aiohttp at runtime')
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
import mower_sdk
assert mower_sdk.__version__ == '0.2.0'
"""

        result = subprocess.run(
            [sys.executable, "-c", script],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_get_type_hints_resolves_public_constructor_annotations(self):
        targets = [
            ("mower_sdk.sdk", "NavimowSDK"),
            ("mower_sdk.mqtt", "NavimowMQTT"),
            ("mower_sdk.client", "MowerClient"),
            ("mower_sdk.navimow", "Navimow"),
        ]

        for module_name, class_name in targets:
            with self.subTest(target=class_name):
                module = import_source_module(module_name)
                constructor = getattr(module, class_name).__init__
                hints = typing.get_type_hints(constructor)
                self.assertIsInstance(hints, dict)
                self.assertIn("return", hints)
