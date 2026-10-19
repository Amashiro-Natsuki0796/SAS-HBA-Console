import unittest

from sas_hba_console.backend import pci_db


class ChipNameTests(unittest.TestCase):
    def test_chip_from_pci_names(self):
        cases = {
            "SAS2308 PCI-Express Fusion-MPT SAS-2": "SAS2308",
            "SAS3008 PCI-Express Fusion-MPT SAS-3": "SAS3008",
            "MegaRAID SAS-3 3108 [Invader]": "SAS3108",
            "MegaRAID SAS-3 3316 [Intruder]": "SAS3316",
            "MegaRAID Tri-Mode SAS3516": "SAS3516",
            "Fusion-MPT 24GSAS/PCIe SAS40xx/41xx": "SAS40xx",
            "MegaRAID SAS-3 3008 [Fury]": "SAS3008",
        }
        for name, chip in cases.items():
            self.assertEqual(pci_db.chip_from_name(name), chip, name)

    def test_chip_from_empty(self):
        self.assertEqual(pci_db.chip_from_name(""), "")
        self.assertEqual(pci_db.chip_from_name("Some RAID controller"), "")


class ClassifyTests(unittest.TestCase):
    def test_9207_8i(self):
        info = pci_db.classify("0x1000", "0x0087", "mpt3sas", "SAS2308-8i", "0x0107")
        self.assertEqual(info["model"], "LSI SAS 9207-8i")
        self.assertEqual(info["chip"], "SAS2308")
        self.assertEqual(info["kind"], "SAS HBA")
        self.assertFalse(info["is_raid"])
        self.assertEqual(info["confidence"], "high")

    def test_9207_8e_and_4i4e(self):
        self.assertEqual(
            pci_db.classify("0x1000", "0x0087", "mpt3sas", "SAS2308-8e")["model"],
            "LSI SAS 9207-8e",
        )
        self.assertEqual(
            pci_db.classify("0x1000", "0x0087", "mpt3sas", "SAS2308-4i4e")["model"],
            "LSI SAS 9207-4i4e",
        )

    def test_9211_9300_9400_9500(self):
        cases = [
            ("0x0072", "SAS2008-8i", "LSI SAS 9211-8i"),
            ("0x0097", "SAS3008-8i", "LSI SAS 9300-8i"),
            ("0x00af", "SAS3408-8i", "LSI SAS 9400-8i"),
            ("0x00ae", "SAS3508-8i", "LSI SAS 9500-8i"),
            ("0x00ab", "SAS3516-16i", "LSI SAS 9500-16i"),
        ]
        for device, board, model in cases:
            info = pci_db.classify("0x1000", device, "mpt3sas", board)
            self.assertEqual(info["model"], model)

    def test_megaraid_is_raid(self):
        info = pci_db.classify("0x1000", "0x005d", "megaraid_sas", "", "0x0104")
        self.assertIn("MegaRAID", info["model"])
        self.assertIn("3108", info["model"])
        self.assertEqual(info["chip"], "SAS3108")
        self.assertEqual(info["kind"], "RAID")
        self.assertTrue(info["is_raid"])

    def test_adaptec_smartpqi(self):
        info = pci_db.classify("0x9005", "0x028f", "smartpqi", "", "0x0104")
        self.assertIn("Adaptec", info["model"])
        self.assertIn("Smart Storage PQI", info["model"])
        self.assertEqual(info["kind"], "SmartRAID")
        self.assertTrue(info["is_raid"])

    def test_unknown_device_still_has_a_name(self):
        info = pci_db.classify("0x1234", "0x5678", "unknown_driver", "", "0x0100")
        self.assertIn("0x1234:0x5678", info["model"])
        self.assertEqual(info["kind"], "SCSI HBA")
        self.assertEqual(info["confidence"], "low")

    def test_board_name_wins_over_pci_ids(self):
        # 0x0097 在 pci.ids 中是 SAS3008，但 board_name 明确是 SAS2308-8i 时以板卡为准
        info = pci_db.classify("0x1000", "0x0097", "mpt3sas", "SAS2308-8i")
        self.assertEqual(info["model"], "LSI SAS 9207-8i")
        self.assertEqual(info["chip"], "SAS2308")


class FallbackTests(unittest.TestCase):
    def setUp(self):
        self.original_find = pci_db._find_pci_ids
        pci_db._find_pci_ids = lambda: ""
        pci_db.reload()

    def tearDown(self):
        pci_db._find_pci_ids = self.original_find
        pci_db.reload()

    def test_builtin_lsi_table(self):
        self.assertEqual(
            pci_db.device_name("0x1000", "0x0097"),
            "SAS3008 PCI-Express Fusion-MPT SAS-3",
        )
        info = pci_db.classify("0x1000", "0x0087", "mpt3sas", "")
        self.assertIn("SAS2308", info["model"])
        self.assertEqual(info["chip"], "SAS2308")

    def test_non_lsi_has_no_builtin_name(self):
        self.assertEqual(pci_db.device_name("0x9005", "0x028f"), "")
        info = pci_db.classify("0x9005", "0x028f", "smartpqi", "")
        self.assertIn("控制器", info["model"])


if __name__ == "__main__":
    unittest.main()
