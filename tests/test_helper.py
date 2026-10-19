import json
import os
import subprocess
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HELPER = os.path.join(PROJECT_ROOT, "scripts", "sas-hba-helper-dev")


class HelperProtocolTests(unittest.TestCase):
    def setUp(self):
        env = dict(os.environ)
        env["SAS_HBA_ALLOW_NONROOT"] = "1"
        env["SAS_HBA_HELPER_DEMO"] = "1"
        self.proc = subprocess.Popen(
            [sys.executable, HELPER],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        self.hello = json.loads(self.proc.stdout.readline())

    def tearDown(self):
        try:
            self.proc.stdin.write('{"id":999,"op":"shutdown"}\n')
            self.proc.stdin.flush()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            try:
                stream.close()
            except OSError:
                pass

    def request(self, payload):
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        return json.loads(line)

    def test_hello(self):
        self.assertEqual(self.hello["event"], "hello")
        self.assertEqual(self.hello["protocol"], 1)
        self.assertTrue(self.hello["demo"])

    def test_ping_and_snapshot(self):
        ping = self.request({"id": 1, "op": "ping"})
        self.assertTrue(ping["ok"])
        self.assertTrue(ping["data"]["pong"])

        snapshot = self.request({"id": 2, "op": "snapshot"})
        self.assertTrue(snapshot["ok"], snapshot)
        data = snapshot["data"]
        self.assertEqual(data["hba"]["model"], "LSI SAS 9207-8i")
        self.assertEqual(len(data["hbas"]), 3)
        self.assertEqual(len(data["drives"]), 7)
        self.assertEqual(data["hbas"][1]["model"], "LSI SAS 9300-8i")
        self.assertEqual(data["hbas"][2]["kind"], "RAID")
        self.assertIn("tools", data)

    def test_smart_demo(self):
        response = self.request({"id": 3, "op": "smart", "dev": "/dev/sdb"})
        self.assertTrue(response["ok"])
        self.assertEqual(response["data"]["protocol"], "SCSI")
        self.assertTrue(response["data"]["available"])

    def test_invalid_device_is_rejected(self):
        response = self.request({"id": 4, "op": "smart", "dev": "/etc/passwd"})
        self.assertFalse(response["ok"])
        self.assertIn("非法", response["error"])

    def test_unlisted_device_is_rejected(self):
        response = self.request({"id": 5, "op": "spin_down", "dev": "/dev/sdz"})
        self.assertFalse(response["ok"])
        self.assertIn("不在", response["error"])

    def test_unknown_operation(self):
        response = self.request({"id": 6, "op": "format_disk", "dev": "/dev/sdb"})
        self.assertFalse(response["ok"])
        self.assertIn("不支持", response["error"])

    def test_malformed_json_keeps_helper_alive(self):
        self.proc.stdin.write("not json\n")
        self.proc.stdin.flush()
        error = json.loads(self.proc.stdout.readline())
        self.assertFalse(error["ok"])
        self.assertIn("JSON", error["error"])
        ping = self.request({"id": 7, "op": "ping"})
        self.assertTrue(ping["ok"])

    def test_spin_down_then_smart_unavailable_then_spin_up(self):
        down = self.request({"id": 8, "op": "spin_down", "dev": "/dev/sde"})
        self.assertTrue(down["ok"], down)
        self.assertEqual(down["data"]["power_mode"], "standby")

        smart = self.request({"id": 9, "op": "smart", "dev": "/dev/sde"})
        self.assertTrue(smart["ok"])
        self.assertTrue(smart["data"]["standby"])

        up = self.request({"id": 10, "op": "spin_up", "dev": "/dev/sde"})
        self.assertTrue(up["ok"], up)
        self.assertEqual(up["data"]["power_mode"], "active")

    def test_hot_remove_removes_drive_from_snapshot(self):
        response = self.request(
            {"id": 11, "op": "hot_remove", "dev": "/dev/sdd", "params": {"spin_first": False}}
        )
        self.assertTrue(response["ok"], response)
        snapshot = self.request({"id": 12, "op": "snapshot"})
        devices = [drive["dev"] for drive in snapshot["data"]["drives"]]
        self.assertNotIn("/dev/sdd", devices)


class NonRootGuardTests(unittest.TestCase):
    def test_helper_refuses_non_root_without_override(self):
        env = dict(os.environ)
        env.pop("SAS_HBA_ALLOW_NONROOT", None)
        env.pop("SAS_HBA_HELPER_DEMO", None)
        result = subprocess.run(
            [sys.executable, HELPER],
            input="",
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("root", result.stderr)


if __name__ == "__main__":
    unittest.main()
