import os
import tempfile
import unittest

from sas_hba_console.backend import sysfs
from tests.sysfs_fixture import add_pci_device, build_tree


class SysfsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        build_tree(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_finds_9207_hba(self):
        hbas = sysfs.find_hbas(self.root, enrich=False)
        self.assertEqual(len(hbas), 1)
        hba = hbas[0]
        self.assertEqual(hba["model"], "LSI SAS 9207-8i")
        self.assertEqual(hba["device_id"], "0x0087")
        self.assertEqual(hba["firmware_version"], "20.00.07.00")
        self.assertEqual(hba["bios_version"], "07.39.02.00")
        self.assertEqual(hba["sas_address"], "500605b00a1b2c3d")
        self.assertEqual(hba["driver"], "mpt3sas")
        self.assertTrue(hba["it_mode"])
        self.assertEqual(hba["pcie_link"], "8.0 GT/s ×8")
        self.assertEqual(hba["host_ids"], ["host0"])
        self.assertEqual(hba["board_name"], "SAS2308-8i")

    def test_enumerates_drives(self):
        drives = {d["name"]: d for d in sysfs.enumerate_drives(self.root)}
        self.assertEqual(set(drives), {"sdb", "sdc", "sdd"})
        sdb = drives["sdb"]
        self.assertEqual(sdb["dev"], "/dev/sdb")
        self.assertEqual(sdb["model"], "ST8000NM0075")
        self.assertEqual(sdb["protocol"], "SAS")
        self.assertEqual(sdb["media"], "HDD")
        self.assertEqual(sdb["size_human"], "8.00 TB")
        self.assertIn("槽 1", sdb["position"])

        sdc = drives["sdc"]
        self.assertEqual(sdc["protocol"], "SATA")
        self.assertEqual(sdc["media"], "SSD")
        self.assertEqual(sdc["position"], "直连")

    def test_mount_swap_and_holder_blockers(self):
        drives = {d["name"]: d for d in sysfs.enumerate_drives(self.root)}
        self.assertIn("/srv/data", drives["sdc"]["mountpoints"])
        self.assertFalse(drives["sdc"]["removable"])
        self.assertFalse(drives["sdb"]["removable"])
        self.assertTrue(drives["sdb"]["is_system"] is False)
        self.assertIn("dm-0", drives["sdb"]["holders"])
        joined = " ".join(drives["sdb"]["blockers"])
        self.assertIn("交换", joined)
        self.assertIn("LVM", joined)
        self.assertIn("/srv/vm", " ".join(drives["sdb"]["mountpoints"]))

    def test_idle_drive_is_removable(self):
        # 没有挂载/交换/LVM 的盘：把 sdd 换成没有根挂载的挂载表
        build_tree(self.root, mounts="")
        drives = {d["name"]: d for d in sysfs.enumerate_drives(self.root)}
        # sdc1 与 sdb 的占用仍在，sdd 现在是空闲的
        self.assertTrue(drives["sdd"]["removable"], drives["sdd"]["blockers"])
        self.assertEqual(drives["sdd"]["blockers"], [])

    def test_rescan_writes_to_scan(self):
        # scan 是只写属性；用普通文件无法真正 open("w") 追加语义，但写入应成功
        result = sysfs.rescan_hosts(self.root)
        self.assertEqual(result["scanned"], ["host0"])
        with open(
            os.path.join(self.root, "sys/class/scsi_host/host0/scan"), encoding="utf-8"
        ) as fh:
            self.assertEqual(fh.read(), "- - -\n")

    def test_no_hba_returns_empty(self):
        empty = tempfile.TemporaryDirectory()
        try:
            os.makedirs(os.path.join(empty.name, "sys/bus/pci/devices"))
            os.makedirs(os.path.join(empty.name, "sys/class/scsi_host"))
            self.assertEqual(sysfs.find_hbas(empty.name, enrich=False), [])
            self.assertEqual(sysfs.enumerate_drives(empty.name), [])
        finally:
            empty.cleanup()

    def test_other_lsi_controller_is_reported(self):
        add_pci_device(
            self.root,
            "0000:04:00.0",
            "0x1000",
            "0x0030",
            "0x010000",
            driver="mptspi",
        )
        summary = sysfs.pci_summary(self.root)
        self.assertEqual(len(summary), 2)
        self.assertEqual(
            sorted(item["device_id"] for item in summary), ["0x0030", "0x0087"]
        )

    def test_9207_uses_sas2308_id(self):
        hba = sysfs.find_hbas(self.root, enrich=False)[0]
        self.assertEqual(hba["device_id"], "0x0087")
        self.assertEqual(hba["chip"], "SAS2308")
        self.assertEqual(hba["kind"], "SAS HBA")
        self.assertTrue(hba["capabilities"]["spin"])
        self.assertTrue(hba["capabilities"]["remove"])

    def test_sas3008_is_9300(self):
        import tempfile

        with tempfile.TemporaryDirectory() as root:
            build_tree(
                root,
                device_id="0x0097",
                board_name="SAS3008-8i",
                driver="mpt3sas",
            )
            hba = sysfs.find_hbas(root, enrich=False)[0]
            self.assertEqual(hba["model"], "LSI SAS 9300-8i")
            self.assertEqual(hba["chip"], "SAS3008")

    def test_megaraid_is_raid_and_not_controllable(self):
        import tempfile

        with tempfile.TemporaryDirectory() as root:
            build_tree(
                root,
                device_id="0x005d",
                board_name="",
                driver="megaraid_sas",
                class_code="0x010400",
                mounts="",
            )
            hba = sysfs.find_hbas(root, enrich=False)[0]
            self.assertEqual(hba["kind"], "RAID")
            self.assertTrue(hba["is_raid"])
            self.assertFalse(hba["capabilities"]["spin"])
            self.assertFalse(hba["capabilities"]["remove"])
            self.assertEqual(hba["chip"], "SAS3108")

    def test_unknown_vendor_uses_pci_ids_or_fallback(self):
        import tempfile

        with tempfile.TemporaryDirectory() as root:
            build_tree(
                root,
                device_id="0x0097",
                board_name="",
                driver="mpt3sas",
                class_code="0x010700",
            )
            hba = sysfs.find_hbas(root, enrich=False)[0]
            self.assertIn("SAS3008", hba["model"])
            self.assertEqual(hba["vendor_name"], "Broadcom / LSI")


if __name__ == "__main__":
    unittest.main()
