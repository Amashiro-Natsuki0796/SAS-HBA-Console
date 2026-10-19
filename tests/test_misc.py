import unittest

from sas_hba_console.backend.client import classify_start_error
from sas_hba_console.util import human_hours, human_size, pcie_link, strip_hex
from sas_hba_console.ui import format as fmt


class UtilTests(unittest.TestCase):
    def test_human_size(self):
        self.assertEqual(human_size(8001563222016), "8.00 TB")
        self.assertEqual(human_size(4000787030016), "4.00 TB")
        self.assertEqual(human_size(512), "512 B")
        self.assertEqual(human_size(None), "—")
        self.assertEqual(human_size(0), "—")

    def test_human_hours(self):
        self.assertEqual(human_hours(24310), "24,310 h")
        self.assertEqual(human_hours(None), "—")

    def test_pcie_and_sas(self):
        self.assertEqual(pcie_link("8.0 GT/s PCIe", "8"), "8.0 GT/s ×8")
        self.assertEqual(strip_hex("0x500605B00A1B2C3D"), "500605b00a1b2c3d")
        self.assertEqual(strip_hex(None), "")


class AuthErrorTests(unittest.TestCase):
    def test_no_agent(self):
        message = classify_start_error("Error executing command as another user: No authentication agent was found.")
        self.assertIn("polkit", message)

    def test_cancelled(self):
        self.assertIn("未获得 root 授权", classify_start_error("", 126))

    def test_missing_helper(self):
        self.assertIn("找不到特权助手", classify_start_error("", 127))


def drive(**overrides):
    base = {
        "dev": "/dev/sdb",
        "model": "ST8000",
        "media": "HDD",
        "rotational": True,
        "smart": None,
        "smart_error": None,
        "power_mode": "active",
        "blockers": [],
    }
    base.update(overrides)
    return base


class HealthTests(unittest.TestCase):
    def test_health_states(self):
        self.assertEqual(fmt.health(drive())[0], "loading")
        self.assertEqual(fmt.health(drive(power_mode="standby"))[0], "idle")
        smart_ok = {"available": True, "passed": True, "attributes": [], "scsi": {}}
        self.assertEqual(fmt.health(drive(smart=smart_ok))[0], "ok")
        smart_bad = {"available": True, "passed": False, "attributes": [], "scsi": {}}
        self.assertEqual(fmt.health(drive(smart=smart_bad))[0], "err")
        smart_warn = {
            "available": True,
            "passed": True,
            "attributes": [{"id": 5, "raw_value": 2}],
            "scsi": {},
        }
        self.assertEqual(fmt.health(drive(smart=smart_warn))[0], "warn")
        smart_hot = {
            "available": True,
            "passed": True,
            "temperature_c": 55,
            "attributes": [],
            "scsi": {},
        }
        self.assertEqual(fmt.health(drive(smart=smart_hot))[0], "warn")
        smart_standby = {"available": False, "standby": True, "attributes": [], "scsi": {}}
        self.assertEqual(fmt.health(drive(smart=smart_standby))[0], "idle")

    def test_summary(self):
        text, css = fmt.drives_summary([])
        self.assertEqual(css, "pill-idle")
        text, css = fmt.drives_summary([drive()])
        self.assertEqual(css, "pill-idle")
        self.assertIn("读取", text)
        self.assertEqual(fmt.drives_summary([drive(power_mode="standby")])[1], "pill-idle")

    def test_protocol_and_power(self):
        self.assertEqual(fmt.protocol_label(drive(smart={"protocol": "ATA"})), "SATA")
        self.assertEqual(fmt.protocol_label(drive(smart={"protocol": "SCSI"})), "SAS")
        self.assertEqual(fmt.power_label(drive(power_mode="standby")), "已停转")
        self.assertEqual(fmt.rotation_text(drive(smart={"rotation_rate": 7200})), "7200 RPM")
        self.assertEqual(fmt.rotation_text(drive(media="SSD")), "SSD")


if __name__ == "__main__":
    unittest.main()
