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
    "postureTheta": "1.039",
    "postureX": "-0.262",
    "postureY": "-0.411",
    "time": 1788087137087,
    "type": 1,
    "vehicleState": 2,
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


# Ordrett frå ei seinare økt: klipparen køyrde rundt medan tilstandskanalen sa
# «Error», og posisjonsmeldingane mangla `vehicleState` heilt til han vende heim.
POSE_IN_ERROR = {
    "postureTheta": "1.017",
    "postureX": "-0.61",
    "postureY": "-0.904",
    "time": 1788093775011,
    "type": 1,
}
POSE_MOWING = {**POSE_CHARGING, "time": 1788161432759, "vehicleState": 4}
POSE_RETURNING = {**POSE_CHARGING, "time": 1788087036598, "vehicleState": 5}
# Første type 2 etter start: verdiane frå førre økt (sone 1, 100 %, gamalt areal).
STALE_PROGRESS = {
    "action": 5,
    "currentMowBoundary": 1,
    "currentMowProgress": 0,
    "mowStartType": 1,
    "mowingPercentage": 100,
    "mowingWeekArea": "161.9",
    "subtotalArea": "161.97",
    "time": 1788161432780,
    "type": 2,
}
FRESH_PROGRESS = {**PROGRESS_ZONE8, "time": 1788161483147, "mowingPercentage": 0}


def _points(m, *payloads):
    return [m.DeviceLocationMessage.from_dict({"device_id": "dev", **p}) for p in payloads]


class ErrorWindowTest(unittest.TestCase):
    def test_pose_without_vehicle_state_has_no_status_but_keeps_position(self):
        m = _models()
        point = m.DeviceLocationMessage.from_dict(POSE_IN_ERROR)
        self.assertIsNone(point.vehicle_state)
        self.assertIsNone(point.status)
        self.assertEqual((point.x, point.y), (-0.61, -0.904))


class StaleProgressTest(unittest.TestCase):
    def test_first_progress_message_is_marked_stale(self):
        m = _models()
        f = m.LocationFilter()
        stale, fresh = f.filter(_points(m, STALE_PROGRESS, FRESH_PROGRESS))
        self.assertTrue(stale.is_stale_progress)
        self.assertFalse(fresh.is_stale_progress)

    def test_first_progress_after_mowing_resumes_is_marked_stale(self):
        m = _models()
        f = m.LocationFilter()
        # Økt 1: framdrift, heim, i stasjonen. Økt 2: klippar att, så gamal framdrift.
        first = f.filter(_points(m, STALE_PROGRESS, FRESH_PROGRESS, POSE_RETURNING))
        second = f.filter(
            _points(
                m,
                {**POSE_CHARGING, "time": 1788161400000, "vehicleState": 1},
                {**POSE_MOWING, "time": 1788161432759},
                {**STALE_PROGRESS, "time": 1788161500000},
                {**FRESH_PROGRESS, "time": 1788161550000},
            )
        )
        self.assertEqual([p.is_stale_progress for p in first], [True, False, False])
        self.assertEqual([p.is_stale_progress for p in second], [False, False, True, False])

    def test_pose_without_vehicle_state_does_not_reset_progress(self):
        m = _models()
        f = m.LocationFilter()
        points = f.filter(
            _points(
                m,
                FRESH_PROGRESS,
                {**POSE_IN_ERROR, "time": 1788161500000},
                {**FRESH_PROGRESS, "time": 1788161550000},
            )
        )
        self.assertEqual([p.is_stale_progress for p in points], [True, False, False])

    def test_stale_flag_is_per_device_and_in_to_dict(self):
        m = _models()
        f = m.LocationFilter()
        a = m.DeviceLocationMessage.from_dict({"device_id": "a", **FRESH_PROGRESS})
        b = m.DeviceLocationMessage.from_dict({"device_id": "b", **FRESH_PROGRESS})
        f.filter([a])
        f.filter([b])
        self.assertTrue(a.is_stale_progress)
        self.assertTrue(b.is_stale_progress)
        self.assertIs(a.to_dict()["is_stale_progress"], True)
        self.assertIs(m.DeviceLocationMessage.from_dict(HEARTBEAT).is_stale_progress, False)


if __name__ == "__main__":
    unittest.main()
