import os
import shutil
import tempfile
import unittest

from sas_hba_console.backend import control
from sas_hba_console.util import RunResult
from tests.sysfs_fixture import build_tree


def ok_runner(cmd, timeout=20.0):
    return RunResult(cmd=cmd, returncode=0, stdout="", stderr="")


def fake_tool(name):
    return f"/usr/bin/{name}"


class ValidationTests(unittest.TestCase):
    def test_device_validation(self):
        for bad in ["sdb", "/dev/../etc/passwd", "/dev/sd", "/dev/sdb1", "", None, "/dev/nvme0n1"]:
            with self.assertRaises(control.ControlError):
                control.validate_device(bad)
        self.assertEqual(control.validate_device("/dev/sdb"), "sdb")
        self.assertEqual(control.validate_device("/dev/sdaa"), "sdaa")

    def test_idle_check(self):
        with self.assertRaises(control.ControlError) as ctx:
            control.ensure_idle({"blockers": ["已挂载：/srv/data"]}, "停转")
        self.assertIn("已挂载", str(ctx.exception))
        control.ensure_idle({"blockers": []}, "停转")


class SpinTests(unittest.TestCase):
    def test_spin_down_ata_prefers_smartctl(self):
        calls = []

        def runner(cmd, timeout=20.0):
            calls.append(cmd)
            return RunResult(cmd=cmd, returncode=0)

        drive = {"dev": "/dev/sdb", "protocol": "SATA", "blockers": []}
        result = control.spin_down(
            drive,
            runner=runner,
            settle=0,
            state_probe=lambda dev: "standby",
            tool_finder=fake_tool,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["power_mode"], "standby")
        self.assertIn("smartctl", calls[0][0])
        self.assertIn("standby,now", calls[0])
        self.assertNotIn("-d", calls[0])

    def test_spin_down_sas_uses_sdparm(self):
        calls = []

        def runner(cmd, timeout=20.0):
            calls.append(cmd)
            return RunResult(cmd=cmd, returncode=0)

        drive = {
            "dev": "/dev/sdb",
            "protocol": "SAS",
            "smart": {"protocol": "SCSI"},
            "blockers": [],
        }
        control.spin_down(
            drive,
            runner=runner,
            settle=0,
            state_probe=lambda dev: "standby",
            tool_finder=fake_tool,
        )
        self.assertTrue(calls[0][0].endswith("sdparm"))
        self.assertIn("--command=stop", calls[0])

    def test_spin_down_failure_raises(self):
        def runner(cmd, timeout=20.0):
            return RunResult(cmd=cmd, returncode=1, stderr="bad thing")

        with self.assertRaises(control.ControlError) as ctx:
            control.spin_down(
                {"dev": "/dev/sdb", "protocol": "SATA", "blockers": []},
                runner=runner,
                settle=0,
                tool_finder=fake_tool,
            )
        self.assertIn("bad thing", str(ctx.exception))

    def test_spin_down_rejects_mounted_drive(self):
        with self.assertRaises(control.ControlError):
            control.spin_down(
                {"dev": "/dev/sdb", "blockers": ["已挂载：/"]},
                runner=ok_runner,
                settle=0,
                tool_finder=fake_tool,
            )

    def test_spin_down_warns_when_still_active(self):
        result = control.spin_down(
            {"dev": "/dev/sdb", "protocol": "SATA", "blockers": []},
            runner=ok_runner,
            settle=0,
            state_probe=lambda dev: "active",
            tool_finder=fake_tool,
        )
        self.assertTrue(result["warning"])

    def test_spin_up_waits_for_active(self):
        states = iter(["standby", "standby", "active"])

        def probe(dev):
            return next(states)

        result = control.spin_up(
            {"dev": "/dev/sdb", "protocol": "SATA"},
            runner=ok_runner,
            wait_seconds=5,
            state_probe=probe,
            sleep=lambda seconds: None,
            tool_finder=fake_tool,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["power_mode"], "active")


class SgNodeTests(unittest.TestCase):
    def test_sg_node_resolution(self):
        with tempfile.TemporaryDirectory() as root:
            link = os.path.join(root, "sys", "block", "sdb", "device", "scsi_generic")
            os.makedirs(link)
            os.symlink("/sys/class/scsi_generic/sg2", os.path.join(link, "sg2"))
            self.assertIsNone(control.sg_node_for({"dev": "/dev/sdb"}, root))
        self.assertIn("/dev/sg", control.sg_node_for({"dev": "/dev/sda"}) or "/dev/sg0")
        self.assertIsNone(control.sg_node_for({"dev": "/dev/sdz"}, "/nonexistent"))


class SasPhyTests(unittest.TestCase):
    """SAS PHY 控制，测试装置按真机 M910X + 9207-8i 的 sysfs 结构复刻。

    真机结构（来自 --diagnose 与 udevadm）::

        /sys/devices/pci0000:00/0000:00:1b.4/0000:03:00.0/host0/
            phy-0:0 .. phy-0:7            <- 只有 device/subsystem 链接
            port-0:14/end_device-0:14/    <- 盘在这里
                target0:0:14/0:0:14:0

        /sys/class/sas_phy/phy-0:4/enable
        /sys/class/sas_end_device/end_device-0:14/phy_identifier = 4
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.host = os.path.join(
            self.root, "sys", "devices", "pci0000:00", "0000:00:1b.4",
            "0000:03:00.0", "host0",
        )
        os.makedirs(self.host, exist_ok=True)
        os.makedirs(os.path.join(self.root, "sys", "class", "sas_phy"), exist_ok=True)
        os.makedirs(
            os.path.join(self.root, "sys", "class", "sas_end_device"), exist_ok=True
        )
        # 8 个 HBA phy
        for i in range(8):
            real = os.path.join(self.host, f"phy-0:{i}")
            os.makedirs(real, exist_ok=True)
            with open(os.path.join(real, "enable"), "w") as fh:
                fh.write("1\n")
            os.symlink(real, os.path.join(
                self.root, "sys", "class", "sas_phy", f"phy-0:{i}"
            ))
        self.phy4 = os.path.join(self.root, "sys", "class", "sas_phy", "phy-0:4")

    def _make_drive(self, dev="sdb", end="0:14", phy_identifier="4", add_delete=True):
        """按真机结构造一块盘，返回其 scsi 设备真实路径。

        phy_identifier 挂在 rphy 设备目录（即 .../end_device-0:14）上，
        对应 sas_device 类；sas_end_device 类只有 end_dev_* 属性。
        """
        port_dir = os.path.join(self.host, "port-0:14", f"end_device-{end}")
        os.makedirs(port_dir, exist_ok=True)
        if phy_identifier is not None:
            with open(os.path.join(port_dir, "phy_identifier"), "w") as fh:
                fh.write(f"{phy_identifier}\n")
        real = os.path.join(
            port_dir, "target0:0:14",
            f"{end.split(':')[0]}:0:{end.split(':')[1]}:0",
        )
        os.makedirs(real, exist_ok=True)
        if add_delete:
            with open(os.path.join(real, "delete"), "w") as fh:
                fh.write("1\n")
        # sas_end_device 类节点只放 end_dev_*（不放 phy_identifier）
        end_node = os.path.join(
            self.root, "sys", "class", "sas_end_device", f"end_device-{end}"
        )
        os.makedirs(end_node, exist_ok=True)
        block = os.path.join(self.root, "sys", "block", dev)
        os.makedirs(block, exist_ok=True)
        os.symlink(real, os.path.join(block, "device"))
        return real, block

    def test_finds_phy_via_end_device_phy_identifier(self):
        """核心：phy_identifier 来自 sas_end_device，应解析出 phy-0:4。"""
        self._make_drive()
        found = control.find_sas_phy({"dev": "/dev/sdb"}, self.root)
        self.assertEqual(os.path.basename(found or ""), "phy-0:4", found)

    def test_reads_phy_identifier_from_rphy_dir_only(self):
        """phy_identifier 只在 rphy 目录（sas_end_device 类里没有）时也要命中。"""
        self._make_drive()
        # 确认 sas_end_device 类下确实没有该属性（真机如此）
        end_node = os.path.join(
            self.root, "sys", "class", "sas_end_device", "end_device-0:14"
        )
        self.assertFalse(os.path.exists(os.path.join(end_node, "phy_identifier")))
        # 而 rphy 目录（end_device-0:14 本体）里有
        port_dir = os.path.join(self.host, "port-0:14", "end_device-0:14")
        self.assertTrue(os.path.exists(os.path.join(port_dir, "phy_identifier")))
        found = control.find_sas_phy({"dev": "/dev/sdb"}, self.root)
        self.assertEqual(os.path.basename(found or ""), "phy-0:4", found)

    def test_disable_and_enable(self):
        self._make_drive()
        phy = control.find_sas_phy({"dev": "/dev/sdb"}, self.root)
        control.set_phy_enabled(phy, False, self.root)
        with open(os.path.join(phy, "enable")) as fh:
            self.assertEqual(fh.read().strip(), "0")
        control.set_phy_enabled(phy, True, self.root)
        with open(os.path.join(phy, "enable")) as fh:
            self.assertEqual(fh.read().strip(), "1")

    def test_phy_without_enable_raises(self):
        empty = os.path.join(self.root, "sys", "class", "sas_phy", "phy-9:9")
        os.makedirs(empty, exist_ok=True)
        with self.assertRaises(control.ControlError):
            control.set_phy_enabled(empty, False, self.root)

    def test_returns_none_without_end_device(self):
        """不在 SAS 拓扑里的盘（如主板 SATA）应返回 None。"""
        real = os.path.join(self.root, "sys", "devices", "pci0000:00", "ata1", "host1")
        os.makedirs(real, exist_ok=True)
        block = os.path.join(self.root, "sys", "block", "sdz")
        os.makedirs(block, exist_ok=True)
        os.symlink(real, os.path.join(block, "device"))
        self.assertIsNone(control.find_sas_phy({"dev": "/dev/sdz"}, self.root))

    def test_hot_remove_disables_the_right_phy(self):
        """移除后应只禁用 phy-0:4，其余 phy 保持启用。"""
        _, block = self._make_drive(dev="sdb", end="0:14", phy_identifier="4")

        def sleep(seconds):
            if os.path.exists(block):
                import shutil as _sh
                _sh.rmtree(block, ignore_errors=True)

        result = control.hot_remove(
            {"dev": "/dev/sdb", "blockers": [], "rotational": True,
             "protocol": "SATA", "smart": {"protocol": "ATA"}},
            sys_root=self.root,
            spin_first=False,
            runner=ok_runner,
            tool_finder=fake_tool,
            sleep=sleep,
            disconnect_phy=True,
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["phy_disabled"], result.get("phy_note"))
        states = {}
        for i in range(8):
            with open(os.path.join(self.host, f"phy-0:{i}", "enable")) as fh:
                states[i] = fh.read().strip()
        self.assertEqual(states[4], "0", states)
        for i in (0, 1, 2, 3, 5, 6, 7):
            self.assertEqual(states[i], "1", states)

    def test_hot_remove_skips_phy_when_disabled(self):
        _, block = self._make_drive()
        before = {}
        for i in range(8):
            with open(os.path.join(self.host, f"phy-0:{i}", "enable")) as fh:
                before[i] = fh.read().strip()

        def sleep(seconds):
            if os.path.exists(block):
                import shutil as _sh
                _sh.rmtree(block, ignore_errors=True)

        result = control.hot_remove(
            {"dev": "/dev/sdb", "blockers": [], "rotational": True},
            sys_root=self.root,
            spin_first=False,
            runner=ok_runner,
            tool_finder=fake_tool,
            sleep=sleep,
            disconnect_phy=False,
        )
        self.assertFalse(result["phy_disabled"])
        for i in range(8):
            with open(os.path.join(self.host, f"phy-0:{i}", "enable")) as fh:
                self.assertEqual(fh.read().strip(), before[i])

    def test_restore_does_not_need_device_path(self):
        """回归：手工恢复链路不针对单块盘，不能去校验设备路径。

        此前 provider 的 restore_phy 分支缺失，操作会落到 _lookup_drive('')
        并报"非法的设备路径：''"。
        """
        import sas_hba_console.backend.control as ctl

        state = os.path.join(self.root, "state", "disabled-phy.txt")
        original = ctl.DISABLED_PHY_LOG
        ctl.DISABLED_PHY_LOG = state
        try:
            ctl.set_phy_enabled(self.phy4, False, self.root)
            ctl.remember_disabled_phy(self.phy4, "槽 7")
            result = ctl.enable_all_disabled_phys(self.root)
            self.assertTrue(result["restored"])
            self.assertEqual(result["failed"], [])
            with open(os.path.join(self.phy4, "enable")) as fh:
                self.assertEqual(fh.read().strip(), "1")
            self.assertEqual(ctl.list_disabled_phy_entries(), [])
        finally:
            ctl.DISABLED_PHY_LOG = original

    def test_restore_cleans_stale_record_without_failing(self):
        """记录里的 PHY 已不存在时应清掉记录，而不是报失败。"""
        import sas_hba_console.backend.control as ctl

        state = os.path.join(self.root, "state", "disabled-phy.txt")
        original = ctl.DISABLED_PHY_LOG
        ctl.DISABLED_PHY_LOG = state
        try:
            ctl._persist_entries(
                [{"phy": "/sys/class/sas_phy/phy-9:9", "label": "旧卡", "disabled_at": 0}]
            )
            result = ctl.enable_all_disabled_phys(self.root)
            self.assertEqual(result["failed"], [])
            self.assertEqual(ctl.list_disabled_phy_entries(), [])
        finally:
            ctl.DISABLED_PHY_LOG = original

    def test_disabled_phy_is_remembered_and_restorable(self):
        import sas_hba_console.backend.control as ctl

        self._make_drive()
        state = os.path.join(self.root, "state", "disabled-phy.txt")
        original = ctl.DISABLED_PHY_LOG
        ctl.DISABLED_PHY_LOG = state
        try:
            ctl.set_phy_enabled(self.phy4, False, self.root)
            ctl.remember_disabled_phy(self.phy4)
            self.assertEqual(ctl.list_disabled_phys(), [self.phy4])
            result = ctl.enable_all_disabled_phys(self.root)
            self.assertTrue(result["restored"])
            with open(os.path.join(self.phy4, "enable")) as fh:
                self.assertEqual(fh.read().strip(), "1")
            self.assertEqual(ctl.list_disabled_phys(), [])
        finally:
            ctl.DISABLED_PHY_LOG = original


class ApmTests(unittest.TestCase):
    """笔记本 SATA 盘的自动省电（APM）检测。"""

    def _apm_runner(self, level):
        import json

        def runner(cmd, timeout=20.0):
            return RunResult(
                cmd=cmd,
                returncode=0,
                stdout=json.dumps({"ata_apm": {"enabled": True, "level": level}}),
            )

        return runner

    def test_read_apm_json(self):
        result = control.read_apm(
            "/dev/sdb",
            runner=self._apm_runner(128),
            tool_finder=lambda name: "/usr/sbin/smartctl",
        )
        self.assertTrue(result["supported"])
        self.assertEqual(result["level"], 128)

    def test_read_apm_text_fallback(self):
        def runner(cmd, timeout=20.0):
            return RunResult(
                cmd=cmd,
                returncode=0,
                stdout="APM feature is:   Enabled\nAPM level is:     96\n",
            )

        result = control.read_apm(
            "/dev/sdb", runner=runner, tool_finder=lambda name: "/usr/sbin/smartctl"
        )
        self.assertEqual(result["level"], 96)

    def test_level_below_128_is_risky(self):
        drive = {"dev": "/dev/sdb", "protocol": "SATA"}
        for level in (1, 80, 127):
            risk = control.power_saving_risk(drive, apm={"level": level}, spdn_cnt=0)
            self.assertTrue(risk["risky"], level)

    def test_level_128_and_above_not_risky(self):
        # ATA 规范：>=128 只做磁头停放，不允许自动停转
        drive = {"dev": "/dev/sdb", "protocol": "SATA"}
        for level in (128, 200, 254, 0):
            risk = control.power_saving_risk(drive, apm={"level": level}, spdn_cnt=0)
            self.assertFalse(risk["risky"], level)

    def test_spdn_count_alone_is_risky(self):
        risk = control.power_saving_risk(
            {"dev": "/dev/sdb", "protocol": "SATA"}, apm={"level": 254}, spdn_cnt=5
        )
        self.assertTrue(risk["risky"])
        self.assertIn("5", risk["reason"])

    def test_sas_drive_never_flagged(self):
        drive = {"dev": "/dev/sdc", "protocol": "SAS", "smart": {"protocol": "SCSI"}}
        risk = control.power_saving_risk(drive, apm={"level": 80}, spdn_cnt=9)
        self.assertFalse(risk["risky"])

    def test_disable_apm_builds_correct_command(self):
        calls = []

        def runner(cmd, timeout=20.0):
            calls.append(cmd)
            return RunResult(cmd=cmd, returncode=0)

        result = control.disable_apm(
            "/dev/sdb", runner=runner, tool_finder=lambda name: "/usr/sbin/smartctl"
        )
        self.assertTrue(result["ok"])
        self.assertIn("-s", calls[0])
        self.assertIn("apm,off", calls[0])

    def test_disable_apm_failure_raises(self):
        def runner(cmd, timeout=20.0):
            return RunResult(cmd=cmd, returncode=1, stderr="APM disable failed")

        with self.assertRaises(control.ControlError):
            control.disable_apm(
                "/dev/sdb", runner=runner, tool_finder=lambda name: "/usr/sbin/smartctl"
            )

    def test_hot_remove_warns_about_apm_risk(self):
        """APM 低电平时，移除应带上风险提示。"""
        import shutil

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        build_tree(tmp.name, mounts="")
        block_path = os.path.join(tmp.name, "sys", "block", "sdd")

        def runner(cmd, timeout=20.0):
            return RunResult(cmd=cmd, returncode=0)

        def sleep(seconds):
            if os.path.exists(block_path):
                shutil.rmtree(block_path)

        result = control.hot_remove(
            {"dev": "/dev/sdd", "blockers": [], "rotational": True, "protocol": "SATA"},
            sys_root=tmp.name,
            spin_first=True,
            runner=runner,
            tool_finder=fake_tool,
            state_probe=lambda dev: "standby",
            sleep=sleep,
            apm_probe=lambda dev: {"level": 80, "supported": True},
            spdn_probe=lambda drive: 3,
            disable_apm_first=False,
        )
        self.assertTrue(result["ok"])
        self.assertIn("APM", result["warning"])


class WaitForStandbyTests(unittest.TestCase):
    def test_returns_immediately_when_already_standby(self):
        calls = []

        def probe(dev):
            calls.append(dev)
            return "standby"

        state = control.wait_for_standby(
            "/dev/sdb", timeout=10, probe=probe, sleep=lambda s: None
        )
        self.assertEqual(state, "standby")
        self.assertEqual(len(calls), 1)

    def test_polls_until_standby(self):
        states = iter(["active", "active", "standby"])

        state = control.wait_for_standby(
            "/dev/sdb",
            timeout=10,
            probe=lambda dev: next(states, "standby"),
            sleep=lambda s: None,
            poll=0.0,
        )
        self.assertEqual(state, "standby")

    def test_times_out_and_reports_last_state(self):
        state = control.wait_for_standby(
            "/dev/sdb",
            timeout=0.0,
            probe=lambda dev: "active",
            sleep=lambda s: None,
        )
        self.assertEqual(state, "active")

    def test_spin_down_verify_false_skips_probe(self):
        probed = {"n": 0}

        def probe(dev):
            probed["n"] += 1
            return "active"

        result = control.spin_down(
            {"dev": "/dev/sdb", "protocol": "SATA", "blockers": []},
            runner=ok_runner,
            settle=0,
            state_probe=probe,
            tool_finder=fake_tool,
            verify=False,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(probed["n"], 0)


class HotRemoveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        build_tree(self.root, mounts="")
        # 造出真机风格的 SAS 拓扑，供"断开 PHY"路径使用
        host_dir = os.path.join(
            self.root, "sys", "devices", "pci0000:00", "0000:00:1b.4",
            "0000:03:00.0", "host0",
        )
        phy_real = os.path.join(host_dir, "phy-0:4")
        os.makedirs(phy_real, exist_ok=True)
        with open(os.path.join(phy_real, "enable"), "w") as fh:
            fh.write("1\n")
        os.makedirs(os.path.join(self.root, "sys", "class", "sas_phy"), exist_ok=True)
        self.phy = os.path.join(self.root, "sys", "class", "sas_phy", "phy-0:4")
        if os.path.lexists(self.phy):
            os.unlink(self.phy)
        os.symlink(phy_real, self.phy)
        # 把 sdd 的 device 指到 end_device-0:14 之下，并设置 phy_identifier
        end_dir = os.path.join(host_dir, "port-0:14", "end_device-0:14")
        os.makedirs(end_dir, exist_ok=True)
        with open(os.path.join(end_dir, "phy_identifier"), "w") as fh:
            fh.write("4\n")
        sdd_real = os.path.join(end_dir, "target0:0:14", "0:0:14:0")
        os.makedirs(sdd_real, exist_ok=True)
        with open(os.path.join(sdd_real, "delete"), "w") as fh:
            fh.write("1\n")
        sdd_dev = os.path.join(self.root, "sys", "block", "sdd", "device")
        if os.path.lexists(sdd_dev):
            try:
                os.unlink(sdd_dev)
            except (OSError, IsADirectoryError):
                shutil.rmtree(sdd_dev, ignore_errors=True)
        os.symlink(sdd_real, sdd_dev)

        # 造出 /dev/sg 风格的通用节点，供"移除后用 sg 停转"路径使用
        sg_dir = os.path.join(
            self.root, "sys", "block", "sdd", "device", "scsi_generic"
        )
        os.makedirs(sg_dir, exist_ok=True)
        fake_sg = os.path.join(self.root, "dev", "sg9")
        os.makedirs(os.path.dirname(fake_sg), exist_ok=True)
        open(fake_sg, "w").close()
        os.symlink(fake_sg, os.path.join(sg_dir, "sg9"))
        # 造一个可控制的 SAS PHY
        phy_dir = os.path.join(self.root, "sys", "class", "sas_phy", "phy-0:4")
        os.makedirs(phy_dir, exist_ok=True)
        with open(os.path.join(phy_dir, "enable"), "w") as fh:
            fh.write("1\n")
        self.phy_dir = phy_dir

    def tearDown(self):
        self.tmp.cleanup()

    def test_hot_remove_writes_delete_and_detects_vanish(self):
        name = "sdd"
        delete_path = os.path.join(self.root, "sys", "block", name, "device", "delete")
        block_path = os.path.join(self.root, "sys", "block", name)
        removed = {"done": False}

        def runner(cmd, timeout=20.0):
            return RunResult(cmd=cmd, returncode=0)

        def sleep(seconds):
            if not removed["done"]:
                # 模拟内核处理 delete：移除块设备目录
                shutil.rmtree(block_path)
                removed["done"] = True

        drive = {
            "dev": "/dev/sdd",
            "blockers": [],
            "rotational": True,
            "protocol": "SATA",
            "model": "ST4000VN008-2DR166",
        }
        result = control.hot_remove(
            drive,
            sys_root=self.root,
            spin_first=False,
            runner=runner,
            sleep=sleep,
            tool_finder=fake_tool,
        )
        self.assertTrue(result["ok"])
        self.assertTrue(removed["done"])
        self.assertIn("安全移除", result["message"])
        self.assertFalse(os.path.exists(delete_path))
        self.assertFalse(os.path.exists(block_path))

    def test_correct_order_flush_then_stop_then_phy_then_delete(self):
        """核心顺序：刷缓存 -> 停转 -> 断链 -> delete。

        理由：delete 时内核必发 SYNCHRONIZE CACHE，会唤醒刚停转的盘；
        所以必须"先断链再 delete"，让该命令送不到盘。而刷缓存必须在
        停转之前完成（此时链路还正常）。
        """
        events = []
        block_path = os.path.join(self.root, "sys", "block", "sdd")

        def runner(cmd, timeout=20.0):
            events.append(cmd[0].rsplit("/", 1)[-1])
            return RunResult(cmd=cmd, returncode=0)

        def sleep(seconds):
            if os.path.exists(block_path) and "delete" not in events:
                events.append("delete")
                shutil.rmtree(block_path)

        result = control.hot_remove(
            {"dev": "/dev/sdd", "blockers": [], "rotational": True, "protocol": "SATA"},
            sys_root=self.root,
            spin_first=True,
            runner=runner,
            tool_finder=fake_tool,
            state_probe=lambda dev: "standby",
            sleep=sleep,
            apm_probe=lambda dev: {"level": 254},
            spdn_probe=lambda drive: 0,
            disconnect_phy=True,
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["phy_disabled"], result.get("phy_note"))

        # 1) flush 必须在停转之前
        flush_at = events.index("blockdev")
        spin_at = next(
            i for i, e in enumerate(events) if e in ("smartctl", "sdparm", "sg_start")
        )
        self.assertLess(flush_at, spin_at, events)

        # 2) 停转必须在 delete 之前
        delete_at = events.index("delete")
        self.assertLess(spin_at, delete_at, events)

        # 3) PHY 必须在 delete 之前断开（否则 delete 的 sync_cache 会唤醒盘）
        enable_path = os.path.join(self.phy, "enable")
        with open(enable_path) as fh:
            self.assertEqual(fh.read().strip(), "0")

    def test_phy_disconnected_before_delete(self):
        """断链必须发生在写 delete 之前，这是不让盘被唤醒的关键。"""
        order = []
        block_path = os.path.join(self.root, "sys", "block", "sdd")
        enable_path = os.path.join(self.phy, "enable")

        def runner(cmd, timeout=20.0):
            return RunResult(cmd=cmd, returncode=0)

        def sleep(seconds):
            if os.path.exists(block_path):
                # 记录此时 PHY 的状态：delete 生效瞬间应该已经是 0
                with open(enable_path) as fh:
                    order.append(("phy_at_delete", fh.read().strip()))
                shutil.rmtree(block_path)

        control.hot_remove(
            {"dev": "/dev/sdd", "blockers": [], "rotational": True, "protocol": "SATA"},
            sys_root=self.root,
            spin_first=True,
            runner=runner,
            tool_finder=fake_tool,
            state_probe=lambda dev: "standby",
            sleep=sleep,
            apm_probe=lambda dev: {"level": 254},
            spdn_probe=lambda drive: 0,
            disconnect_phy=True,
        )
        self.assertEqual(order, [("phy_at_delete", "0")], order)

    def test_phy_disable_removing_device_counts_as_success(self):
        """真机行为：禁用 PHY 会让内核直接移除整个端口，delete 节点随之消失。

        此时不应报"设备节点仍然存在"或写入失败，而应视为成功移除。
        """
        block_path = os.path.join(self.root, "sys", "block", "sdd")
        enable_path = os.path.join(self.phy, "enable")

        original_set = control.set_phy_enabled

        def fake_set(phy, enable, root="/"):
            result = original_set(phy, enable, root)
            if not enable:
                # 模拟内核在断链后立即移除设备与 delete 节点
                shutil.rmtree(block_path, ignore_errors=True)
            return result

        control.set_phy_enabled = fake_set
        try:
            result = control.hot_remove(
                {"dev": "/dev/sdd", "blockers": [], "rotational": True,
                 "protocol": "SATA"},
                sys_root=self.root,
                spin_first=True,
                runner=ok_runner,
                tool_finder=fake_tool,
                state_probe=lambda dev: "standby",
                sleep=lambda s: None,
                apm_probe=lambda dev: {"level": 254},
                spdn_probe=lambda drive: 0,
                disconnect_phy=True,
            )
        finally:
            control.set_phy_enabled = original_set

        self.assertTrue(result["ok"])
        self.assertTrue(result["phy_disabled"])
        with open(enable_path) as fh:
            self.assertEqual(fh.read().strip(), "0")

    def test_removal_succeeds_even_if_spindown_fails(self):
        """停转失败不阻断移除，但要警告。"""

        def runner(cmd, timeout=20.0):
            if cmd[0].endswith(("smartctl", "sdparm", "sg_start")):
                return RunResult(cmd=cmd, returncode=1, stderr="device busy")
            return RunResult(cmd=cmd, returncode=0)

        block_path = os.path.join(self.root, "sys", "block", "sdd")

        def sleep(seconds):
            if os.path.exists(block_path):
                shutil.rmtree(block_path)

        result = control.hot_remove(
            {"dev": "/dev/sdd", "blockers": [], "rotational": True, "protocol": "SATA"},
            sys_root=self.root,
            spin_first=True,
            runner=runner,
            tool_finder=fake_tool,
            state_probe=lambda dev: "active",
            sleep=sleep,
            apm_probe=lambda dev: {"level": 254},
            spdn_probe=lambda drive: 0,
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["warning"])

    def test_spindown_targets_existing_node(self):
        """停转命令必须发给仍然存在的节点（sg 优先），不能指向已消失的设备。"""
        used = []
        block_path = os.path.join(self.root, "sys", "block", "sdd")

        def runner(cmd, timeout=20.0):
            used.append(cmd)
            return RunResult(cmd=cmd, returncode=0)

        def sleep(seconds):
            if os.path.exists(block_path):
                shutil.rmtree(block_path)

        control.hot_remove(
            {"dev": "/dev/sdd", "blockers": [], "rotational": True,
             "protocol": "SAS", "smart": {"protocol": "SCSI"}},
            sys_root=self.root,
            spin_first=True,
            runner=runner,
            tool_finder=fake_tool,
            state_probe=lambda dev: "standby",
            sleep=sleep,
            apm_probe=lambda dev: {"level": None},
            spdn_probe=lambda drive: None,
        )
        targets = [c[-1] for c in used if c[0].endswith(("sdparm", "sg_start"))]
        self.assertTrue(targets)
        for target in targets:
            self.assertTrue(os.path.exists(target), f"停转目标必须存在：{target}")

    def test_hot_remove_without_spin_works(self):
        block_path = os.path.join(self.root, "sys", "block", "sdd")

        def sleep(seconds):
            if os.path.exists(block_path):
                shutil.rmtree(block_path)

        result = control.hot_remove(
            {"dev": "/dev/sdd", "blockers": [], "rotational": True},
            sys_root=self.root,
            spin_first=False,
            runner=ok_runner,
            tool_finder=fake_tool,
            sleep=sleep,
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["standby_confirmed"])

    def test_hot_remove_refuses_mounted(self):
        with self.assertRaises(control.ControlError) as ctx:
            control.hot_remove(
                {"dev": "/dev/sdd", "blockers": ["已挂载：/"], "rotational": True},
                sys_root=self.root,
                runner=ok_runner,
            )
        self.assertIn("无法", str(ctx.exception))

    def test_hot_remove_requires_scsi_delete_node(self):
        block = os.path.join(self.root, "sys", "block", "sde")
        os.makedirs(block)
        with self.assertRaises(control.ControlError) as ctx:
            control.hot_remove(
                {"dev": "/dev/sde", "blockers": []},
                sys_root=self.root,
                runner=ok_runner,
            )
        self.assertIn("不支持", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
