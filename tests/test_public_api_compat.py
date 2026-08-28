import typing
import unittest

from tests.support import import_fresh, import_source_module


class PublicApiCompatibilityTests(unittest.TestCase):
    def test_importing_public_package_succeeds(self):
        module = import_fresh("mower_sdk")
        self.assertIn("NavimowSDK", module.__all__)
        self.assertIn("NavimowMQTT", module.__all__)
        self.assertIn("MowerClient", module.__all__)

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
