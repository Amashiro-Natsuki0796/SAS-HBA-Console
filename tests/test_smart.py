import json
import os
import unittest

from sas_hba_console.backend.smart import parse_smart_json, read_smart
from sas_hba_console.util import RunResult

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def load(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return json.load(fh)


class SmartParseTests(unittest.TestCase):
    def test_ata_ok(self):
        data = parse_smart_json(load("smartctl_ata_ok.json"), 0)
        self.assertTrue(data["available"])
        self.assertTrue(data["passed"])
        self.assertEqual(data["protocol"], "ATA")
        self.assertEqual(data["model"], "ST8000VN004-2M2101")
        self.assertEqual(data["temperature_c"], 36)
        self.assertEqual(data["power_on_hours"], 24310)
        self.assertEqual(data["power_cycle_count"], 78)
        self.assertEqual(data["rotation_rate"], 7200)
        self.assertEqual(len(data["attributes"]), 6)
        self.assertEqual(data["failed_attributes"], [])
        self.assertEqual(data["attributes"][0]["id"], 1)
        self.assertTrue(data["attributes"][0]["prefailure"])
        self.assertEqual(data["attributes"][0]["raw_string"], "128831221")

    def test_scsi_counters(self):
        data = parse_smart_json(load("smartctl_scsi.json"), 0)
        self.assertTrue(data["available"])
        self.assertEqual(data["protocol"], "SCSI")
        self.assertEqual(data["model"], "ST8000NM0075")
        self.assertEqual(data["scsi"]["read"]["gigabytes"], "412356.782")
        self.assertEqual(data["scsi"]["write"]["uncorrected"], 0)
        self.assertEqual(data["scsi"]["grown_defects"], 0)
        self.assertEqual(data["scsi"]["start_stop_cycles"], 78)

    def test_standby_is_not_available_and_not_an_error(self):
        data = parse_smart_json(load("smartctl_standby.json"), 2)
        self.assertFalse(data["available"])
        self.assertTrue(data["standby"])
        self.assertEqual(data["power_mode"], "standby")
        self.assertEqual(data["protocol"], "ATA")
        self.assertIsNone(data["temperature_c"])

    def test_open_failure_reports_error(self):
        data = parse_smart_json(load("smartctl_error.json"), 1)
        self.assertFalse(data["available"])
        self.assertIn("No such device", data["error"])
        self.assertFalse(data["standby"])

    def test_invalid_payload(self):
        data = parse_smart_json(None, -1, "boom")
        self.assertFalse(data["available"])
        self.assertIn("JSON", data["error"])

    def test_failed_attribute_marks_disk_unhealthy(self):
        payload = load("smartctl_ata_ok.json")
        table = payload["ata_smart_attributes"]["table"]
        table[1]["raw"] = {"value": 8, "string": "8"}
        table[1]["when_failed"] = "now"
        data = parse_smart_json(payload, 0)
        self.assertFalse(data["passed"])
        self.assertEqual(data["failed_attributes"], [5])

    def test_empty_json_from_smartctl(self):
        data = parse_smart_json({}, 1, "smartctl: command not found")
        self.assertFalse(data["available"])
        self.assertEqual(data["stderr"], "smartctl: command not found")


class PowerProbeTests(unittest.TestCase):
    def test_missing_device_is_not_standby(self):
        """回归：设备不存在时不能误报为 standby（会导致假阳性"已停转"）。"""
        from sas_hba_console.backend.smart import query_power_state

        self.assertEqual(query_power_state("/dev/definitely-not-a-device"), "missing")

    def test_wait_for_standby_stops_on_missing(self):
        from sas_hba_console.backend import control

        calls = {"n": 0}

        def probe(dev):
            calls["n"] += 1
            return "missing"

        state = control.wait_for_standby(
            "/dev/sdz", timeout=30, probe=probe, sleep=lambda s: None
        )
        self.assertEqual(state, "missing")
        self.assertEqual(calls["n"], 1, "设备消失后不应继续轮询")


class FakeSmartctlTest(unittest.TestCase):
    def test_read_smart_parses_stdout(self):
        payload = load("smartctl_ata_ok.json")

        def fake_runner(cmd, timeout=20.0):
            return RunResult(cmd=cmd, returncode=0, stdout=json.dumps(payload))

        import sas_hba_console.backend.smart as smart_mod

        original = smart_mod.find_tool
        smart_mod.find_tool = lambda name: "/usr/sbin/smartctl"
        try:
            data = read_smart("/dev/sdb", runner=fake_runner)
        finally:
            smart_mod.find_tool = original
        self.assertTrue(data["available"])
        self.assertIn("-n", data["command"])
        self.assertEqual(data["model"], "ST8000VN004-2M2101")

    def test_retries_with_sat_when_type_unknown(self):
        calls = []

        def fake_run_cmd(cmd, timeout=20.0):
            calls.append(list(cmd))
            if "-d" in cmd:
                return RunResult(
                    cmd=cmd, returncode=0, stdout=json.dumps(load("smartctl_ata_ok.json"))
                )
            return RunResult(
                cmd=cmd,
                returncode=1,
                stdout="",
                stderr="Smartctl open device: /dev/sdb failed: Unable to detect device type",
            )

        import sas_hba_console.backend.smart as smart_mod

        original_tool, original_run = smart_mod.find_tool, smart_mod.run_cmd
        smart_mod.find_tool = lambda name: "/usr/sbin/smartctl"
        smart_mod.run_cmd = fake_run_cmd
        try:
            data = read_smart("/dev/sdb", runner=fake_run_cmd)
        finally:
            smart_mod.find_tool, smart_mod.run_cmd = original_tool, original_run
        self.assertTrue(data["available"])
        self.assertEqual(len(calls), 2)
        self.assertIn("-d", calls[1])
        self.assertIn("sat", calls[1])


if __name__ == "__main__":
    unittest.main()
