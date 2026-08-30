"""Felt på posisjonsmeldingane som er observerte i ein reell klippeøkt (navimow-log2)."""

import importlib
import unittest

from tests.support import install_dependency_stubs, purge_modules

# Ordrett frå loggen: type 2 medan sone 8 (grense 11) vart klipt.
PROGRESS_ZONE8 = {
    "action": 8,
    "currentMowBoundary": 11,
    "currentMowProgress": 2401,
    "mapWorkPosition": "0000000800000006000000010000000B00000961" + "0" * 88,
    "mowStartType": 1,
    "mowingPercentage": 11,
    "mowingWeekArea": "19.11",
    "subAction": 6,
    "subtotalArea": "19.11",
    "time": 1788085035337,
    "type": 2,
}
PARTITIONS = {"partitionIds": [10, 11], "time": 1788085139768, "type": 3}
HEARTBEAT = {"time": 1788084093268, "type": 3}
TASK_DELAY = {"taskDelay": False, "type": 4}
POSE_CHARGING = {
    "postureTheta": "1.039", "postureX": "-0.262", "postureY": "-0.411",
    "time": 1788087137087, "type": 1, "vehicleState": 2,
}


def _models():
    install_dependency_stubs()
    purge_modules("mower_sdk")
    return importlib.import_module("mower_sdk.models")


class ProgressFieldsTest(unittest.TestCase):
    def test_zone_and_progress_are_exposed(self):
        m = _models()
        point = m.DeviceLocationMessage.from_dict(PROGRESS_ZONE8)
        self.assertEqual(point.current_zone, 11)
        self.assertEqual(point.zone_progress, 24.01)
        self.assertEqual(point.action, 8)
        self.assertEqual(point.sub_action, 6)
        self.assertEqual(point.week_area, 19.11)
        self.assertIsNone(point.partition_ids)
        self.assertIsNone(point.task_delay)

    def test_sub_action_absent_is_none(self):
        m = _models()
        point = m.DeviceLocationMessage.from_dict({**PROGRESS_ZONE8, "action": 5})
        point_without = m.DeviceLocationMessage.from_dict(
            {k: v for k, v in PROGRESS_ZONE8.items() if k != "subAction"}
        )
        self.assertEqual(point.sub_action, 6)
        self.assertIsNone(point_without.sub_action)

    def test_partition_ids_and_task_delay(self):
        m = _models()
        self.assertEqual(m.DeviceLocationMessage.from_dict(PARTITIONS).partition_ids, [10, 11])
        self.assertIsNone(m.DeviceLocationMessage.from_dict(HEARTBEAT).partition_ids)
        self.assertIs(m.DeviceLocationMessage.from_dict(TASK_DELAY).task_delay, False)

    def test_to_dict_carries_new_fields(self):
        m = _models()
        d = m.DeviceLocationMessage.from_dict(PROGRESS_ZONE8).to_dict()
        self.assertEqual(d["current_zone"], 11)
        self.assertEqual(d["zone_progress"], 24.01)
        self.assertEqual(d["week_area"], 19.11)


class VehicleStateTest(unittest.TestCase):
    def test_vehicle_state_maps_to_status(self):
        m = _models()
        expected = {
            1: m.MowerStatus.DOCKED,
            2: m.MowerStatus.CHARGING,
            4: m.MowerStatus.MOWING,
            5: m.MowerStatus.RETURNING,
            99: m.MowerStatus.UNKNOWN,
        }
        for raw, status in expected.items():
            point = m.DeviceLocationMessage.from_dict({**POSE_CHARGING, "vehicleState": raw})
            self.assertIs(point.status, status, raw)
        self.assertIsNone(m.DeviceLocationMessage.from_dict(HEARTBEAT).status)


if __name__ == "__main__":
    unittest.main()
