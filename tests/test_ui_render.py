"""无头/离屏 GUI 冒烟测试：确保每一种控制器与硬盘都能渲染详情页。

这类测试需要 DISPLAY（GTK 无法纯 headless 初始化）。CI 无显示时自动跳过。
"""

import os
import unittest

try:
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import GLib, Gtk
except (ImportError, ValueError):  # pragma: no cover
    Gtk = None


@unittest.skipIf(Gtk is None, "PyGObject/GTK3 不可用")
@unittest.skipIf(not os.environ.get("DISPLAY"), "没有可用的 DISPLAY")
class DetailRenderTests(unittest.TestCase):
    def test_every_controller_and_drive_renders(self):
        from sas_hba_console.backend.demo import DemoProvider
        from sas_hba_console.ui import format as fmt
        from sas_hba_console.ui.window import MainWindow

        provider = DemoProvider()
        snapshot = provider.snapshot()
        errors = []

        app = Gtk.Application(application_id="io.github.sashbaconsole.uitest")

        def activate(application):
            # 绕过 pkexec：直接给窗口注入演示快照
            window = MainWindow(application, "demo")
            window.present()

            def run() -> bool:
                try:
                    window.snapshot = snapshot
                    window.hbas = snapshot["hbas"]
                    window.drives = snapshot["drives"]
                    window.drives_by_dev = {
                        drive["dev"]: drive for drive in window.drives
                    }
                    window._rebuild_hba_select()
                    for hba in window.hbas:
                        window.hba_filter = hba["pci_address"]
                        window._update_hba_card()
                        window._fill_table(None)
                        for drive in window._visible_drives():
                            window.selected_dev = drive["dev"]
                            window._update_detail(drive)
                            window._update_action_buttons()
                            # 健康、协议、电源字段都必须可计算
                            fmt.health(drive)
                            fmt.protocol_label(drive)
                            fmt.power_label(drive)
                            fmt.attribute_rows(drive)
                            fmt.scsi_rows(drive)
                            fmt.smart_messages(drive)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{type(exc).__name__}: {exc}")
                finally:
                    application.quit()
                return False

            GLib.timeout_add(1500, run)

        app.connect("activate", activate)
        app.run([])
        self.assertEqual(errors, [], errors)


@unittest.skipIf(Gtk is None, "PyGObject/GTK3 不可用")
@unittest.skipIf(not os.environ.get("DISPLAY"), "没有可用的 DISPLAY")
class ApmCheckboxTests(unittest.TestCase):
    """回归：热拔对话框的“移除前关闭 APM”在任何数据状态下都要可勾选。"""

    def _make_dialog(self, drive):
        from sas_hba_console.ui.dialogs import HotRemoveDialog

        app = Gtk.Application(application_id="io.github.sashbaconsole.apmcb")
        holder = {}

        def activate(application):
            dialog = HotRemoveDialog(None, drive)
            holder["dialog"] = dialog
            application.quit()

        app.connect("activate", activate)
        app.run([])
        return holder["dialog"]

    def _base_drive(self, **extra):
        drive = {
            "dev": "/dev/sdb",
            "model": "ST2000LM007",
            "size_human": "2.00 TB",
            "serial": "WDZ123",
            "firmware": "0001",
            "position": "Bay 1",
            "rotational": True,
            "media": "HDD",
            "protocol": "SATA",
            "blockers": [],
            "mountpoints": [],
            "can_spin": True,
            "can_remove": True,
        }
        drive.update(extra)
        return drive

    def test_checkbox_enabled_when_no_risk_data(self):
        # 未探测到 APM（最常见的情况）也必须能勾选
        dialog = self._make_dialog(self._base_drive())
        try:
            self.assertTrue(dialog._disable_apm.get_sensitive())
            self.assertFalse(dialog.get_disable_apm())
        finally:
            dialog.destroy()

    def test_checkbox_enabled_and_checked_when_risky(self):
        dialog = self._make_dialog(
            self._base_drive(
                power_saving_risk={"risky": True, "reason": "APM 电平 80"}
            )
        )
        try:
            self.assertTrue(dialog._disable_apm.get_sensitive())
            self.assertTrue(dialog.get_disable_apm())
        finally:
            dialog.destroy()

    def test_checkbox_enabled_when_risk_false(self):
        dialog = self._make_dialog(
            self._base_drive(power_saving_risk={"risky": False, "reason": ""})
        )
        try:
            self.assertTrue(dialog._disable_apm.get_sensitive())
        finally:
            dialog.destroy()

    def test_ssd_cannot_disable_apm(self):
        dialog = self._make_dialog(
            self._base_drive(rotational=False, media="SSD")
        )
        try:
            self.assertFalse(dialog._disable_apm.get_sensitive())
        finally:
            dialog.destroy()


if __name__ == "__main__":
    unittest.main()
