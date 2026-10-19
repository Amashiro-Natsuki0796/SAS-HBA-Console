"""对话框：安全热拔出确认、错误提示、诊断信息、关于。"""

from __future__ import annotations

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from .. import APP_NAME, __version__
from ..i18n import t  # noqa: E402
from . import theme  # noqa: E402


def _dim_label(text: str, small: bool = False) -> Gtk.Label:
    label = Gtk.Label(label=text)
    label.set_xalign(0)
    label.set_line_wrap(True)
    label.get_style_context().add_class("muted")
    if small:
        label.get_style_context().add_class("tiny")
    return label


def show_error(parent: Gtk.Window, title: str, message: str, detail: str = "") -> None:
    dialog = Gtk.MessageDialog(
        transient_for=parent,
        modal=True,
        message_type=Gtk.MessageType.ERROR,
        buttons=Gtk.ButtonsType.CLOSE,
        text=title,
    )
    dialog.format_secondary_text(message)
    if detail:
        box = dialog.get_message_area()
        expander = Gtk.Expander(label=t("详细信息"))
        text = Gtk.Label(label=detail)
        text.set_xalign(0)
        text.set_line_wrap(True)
        text.set_selectable(True)
        text.get_style_context().add_class("mono")
        text.get_style_context().add_class("tiny")
        expander.add(text)
        box.pack_start(expander, False, False, 6)
        expander.show_all()
    dialog.run()
    dialog.destroy()


class HotRemoveDialog(Gtk.Dialog):
    def __init__(self, parent: Gtk.Window, drive: dict):
        super().__init__(title=t("安全热拔出"), transient_for=parent, modal=True)
        self.set_default_size(560, -1)
        self.set_resizable(False)
        self.add_button(t("取消"), Gtk.ResponseType.CANCEL)
        self.add_button(t("安全移除"), Gtk.ResponseType.OK)
        self.set_default_response(Gtk.ResponseType.CANCEL)
        self._drive = drive
        self._blockers = list(drive.get("blockers") or [])

        content = self.get_content_area()
        content.set_border_width(20)
        content.set_spacing(14)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        icon = Gtk.Image.new_from_icon_name(
            "dialog-warning-symbolic", Gtk.IconSize.DIALOG
        )
        icon.set_valign(Gtk.Align.START)
        head.pack_start(icon, False, False, 0)
        head_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        title = Gtk.Label(label=t("安全移除 {0}", drive.get('dev', t('硬盘'))))
        title.set_xalign(0)
        title.get_style_context().add_class("detail-title")
        subtitle = _dim_label(
            t("{0} · {1} · 序列号 {2}", drive.get('model', t('未知硬盘')), drive.get('size_human', ''), drive.get('serial') or '—')
        )
        head_text.pack_start(title, False, False, 0)
        head_text.pack_start(subtitle, False, False, 0)
        head.pack_start(head_text, True, True, 0)
        content.pack_start(head, False, False, 0)

        body = Gtk.Label(
            label=(
                t("该操作会让内核移除这个 SCSI 设备，使硬盘停止响应并进入可物理拔出状态。随后请确认托架/背板支持热插拔，再拔出硬盘。")
            )
        )
        body.set_xalign(0)
        body.set_line_wrap(True)
        body.get_style_context().add_class("muted")
        content.pack_start(body, False, False, 0)

        grid = Gtk.Grid(column_spacing=18, row_spacing=6)
        details = [
            (t("设备"), drive.get("dev", "—")),
            (t("容量"), drive.get("size_human", "—")),
            (t("位置"), drive.get("position", "—")),
            (t("挂载点"), "、".join(drive.get("mountpoints") or []) or t("未挂载")),
        ]
        for row, (key, value) in enumerate(details):
            label = Gtk.Label(label=key)
            label.set_xalign(0)
            label.get_style_context().add_class("kv-label")
            value_label = Gtk.Label(label=value)
            value_label.set_xalign(0)
            value_label.set_selectable(True)
            value_label.get_style_context().add_class("kv-value")
            grid.attach(label, 0, row, 1, 1)
            grid.attach(value_label, 1, row, 1, 1)
        content.pack_start(grid, False, False, 0)

        if self._blockers:
            blocker_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            blocker_box.get_style_context().add_class("error-banner")
            blocker_title = Gtk.Label(label=t("无法安全移除：硬盘正在被使用"))
            blocker_title.set_xalign(0)
            blocker_title.get_style_context().add_class("bold")
            blocker_box.pack_start(blocker_title, False, False, 0)
            for blocker in self._blockers:
                label = Gtk.Label(label=f"• {blocker}")
                label.set_xalign(0)
                label.set_line_wrap(True)
                blocker_box.pack_start(label, False, False, 0)
            content.pack_start(blocker_box, False, False, 0)
        else:
            ok_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            ok_box.get_style_context().add_class("detail-note")
            ok_icon = Gtk.Image.new_from_icon_name(
                "emblem-ok-symbolic", Gtk.IconSize.MENU
            )
            ok_label = Gtk.Label(
                label=t("未检测到挂载、交换分区或 LVM/RAID 占用，可以安全移除。")
            )
            ok_label.set_xalign(0)
            ok_label.set_line_wrap(True)
            ok_box.pack_start(ok_icon, False, False, 0)
            ok_box.pack_start(ok_label, True, True, 0)
            content.pack_start(ok_box, False, False, 0)

        self._spin_first = Gtk.CheckButton(label=t("移除前先停转硬盘（推荐，保护磁头）"))
        self._spin_first.set_active(True)
        self._spin_first.set_sensitive(bool(drive.get("rotational", True)))
        self._spin_first.set_tooltip_text(
            t(
                "先刷新缓存并让硬盘真正停转，再断开端口链路，最后让内核移除设备。"
                "先断链可以保证内核移除时的缓存同步命令送不到硬盘，"
                "从而不会把刚停转的盘重新唤醒。"
            )
        )
        content.pack_start(self._spin_first, False, False, 0)

        # 笔记本 SATA 盘常见：APM 允许硬盘自行停转/起转。
        self._apm_risk = drive.get("power_saving_risk") or {}
        self._apm_note = drive.get("apm_note") or ""
        self._disable_apm = Gtk.CheckButton(
            label=t("移除前关闭 APM（防止硬盘自行起转，笔记本硬盘常见）")
        )
        self._disable_apm.set_active(bool(self._apm_risk.get("risky")))
        # 始终可勾选：APM 可能尚未探测完成，或用户想主动关闭。
        self._disable_apm.set_sensitive(bool(drive.get("rotational", True)))
        self._disable_apm.set_tooltip_text(
            t("通过 smartctl -s apm,off 关闭高级电源管理，阻止硬盘自行停转或起转")
        )
        content.pack_start(self._disable_apm, False, False, 0)

        self._disconnect_phy = Gtk.CheckButton(
            label=t("移除后断开端口链路（SAS 直通卡必须，防止盘被重新发现而起转）")
        )
        self._disconnect_phy.set_active(True)
        self._disconnect_phy.set_tooltip_text(
            t(
                "断开 PHY 可阻止 SAS 控制器重新发现该盘并下发 Power-on/reset，"
                "同时让内核移除时的同步缓存命令无法送达，从而保持停转。"
                "注意：断开后该盘会从系统消失，重新插入也不会自动识别，"
                "需要手动点菜单“恢复已断开的端口链路”。"
                "断开的是物理端口链路，该端口上的其他盘也会一并离线。"
            )
        )
        content.pack_start(self._disconnect_phy, False, False, 0)

        if not self._apm_risk:
            apm_unknown = Gtk.Label(
                label=t("尚未检测到 APM 状态；如该盘会在移除后自行起转，可勾选上面的选项")
            )
            apm_unknown.set_xalign(0)
            apm_unknown.set_line_wrap(True)
            apm_unknown.get_style_context().add_class("tiny")
            apm_unknown.get_style_context().add_class("faint")
            content.pack_start(apm_unknown, False, False, 0)

        if self._apm_risk.get("risky"):
            apm_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            apm_box.get_style_context().add_class("warning-banner")
            apm_title = Gtk.Label(label=t("检测到自动省电策略"))
            apm_title.set_xalign(0)
            apm_title.get_style_context().add_class("bold")
            apm_box.pack_start(apm_title, False, False, 0)
            apm_body = Gtk.Label(label=self._apm_risk.get("reason") or "")
            apm_body.set_xalign(0)
            apm_body.set_line_wrap(True)
            apm_box.pack_start(apm_body, False, False, 0)
            content.pack_start(apm_box, False, False, 0)

        hint = Gtk.Label(
            label=(
                t(
                    "执行顺序：刷新缓存 → 停转硬盘 → 断开端口链路 → 内核移除设备。"
                    "先断链是为了让移除时的缓存同步命令无法送达，避免硬盘被重新唤醒。"
                    "断链后该盘会从系统消失，重新使用需点菜单“恢复已断开的端口链路”。"
                )
            )
        )
        hint.set_xalign(0)
        hint.set_line_wrap(True)
        hint.get_style_context().add_class("tiny")
        hint.get_style_context().add_class("faint")
        content.pack_start(hint, False, False, 0)

        ok_button = self.get_widget_for_response(Gtk.ResponseType.OK)
        if ok_button:
            ok_button.get_style_context().add_class("danger-button")
            ok_button.set_sensitive(not self._blockers)

        self.show_all()

    def get_spin_first(self) -> bool:
        return bool(self._spin_first.get_active())

    def get_disable_apm(self) -> bool:
        return bool(self._disable_apm.get_active())

    def get_disconnect_phy(self) -> bool:
        return bool(self._disconnect_phy.get_active())


class DiagnosticsDialog(Gtk.Dialog):
    def __init__(self, parent: Gtk.Window, text: str):
        super().__init__(title=t("诊断信息"), transient_for=parent, modal=True)
        self.set_default_size(820, 620)
        content = self.get_content_area()
        content.set_border_width(14)
        content.set_spacing(10)

        hint = Gtk.Label(
            label=t("以下信息可用于排查硬件识别或权限问题。复制时不会包含密码。")
        )
        hint.set_xalign(0)
        hint.get_style_context().add_class("muted")
        hint.get_style_context().add_class("tiny")
        content.pack_start(hint, False, False, 0)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_hexpand(True)
        scrolled.set_vexpand(True)
        view = Gtk.TextView()
        view.set_editable(False)
        view.set_cursor_visible(False)
        view.set_monospace(True)
        view.get_buffer().set_text(text)
        view.set_left_margin(10)
        view.set_right_margin(10)
        view.set_top_margin(10)
        view.set_bottom_margin(10)
        scrolled.add(view)
        content.pack_start(scrolled, True, True, 0)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        buttons.set_halign(Gtk.Align.END)
        copy_button = Gtk.Button(label=t("复制到剪贴板"))
        copy_button.connect("clicked", self._copy, text)
        close_button = Gtk.Button(label=t("关闭"))
        close_button.connect("clicked", lambda _b: self.response(Gtk.ResponseType.CLOSE))
        buttons.pack_start(copy_button, False, False, 0)
        buttons.pack_start(close_button, False, False, 0)
        content.pack_start(buttons, False, False, 0)
        self.show_all()

    def _copy(self, button: Gtk.Button, text: str) -> None:
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(text, -1)
        clipboard.store()
        button.set_label(t("已复制 ✓"))

        def restore() -> bool:
            button.set_label(t("复制到剪贴板"))
            return False

        GLib.timeout_add_seconds(2, restore)


def show_about(parent: Gtk.Window) -> None:
    dialog = Gtk.AboutDialog(transient_for=parent, modal=True)
    dialog.set_program_name(APP_NAME)
    dialog.set_version(__version__)
    dialog.set_comments(
        t("通用 SAS / SCSI / RAID 控制器与硬盘 SMART 图形化控制台。\\n支持全系 LSI/Broadcom、Adaptec、Microchip、HPE 等 HBA/RAID 卡，以及硬盘 SMART 监控、单盘停转/启动与安全热拔出。")
    )
    dialog.set_license_type(Gtk.License.GPL_3_0)
    dialog.set_authors(["SAS HBA Storage Console contributors"])
    dialog.set_copyright("© 2026 SAS HBA Storage Console")
    icon = theme.load_icon(96)
    if icon is not None:
        dialog.set_logo(icon)
    dialog.run()
    dialog.destroy()


def show_info(parent: Gtk.Window, title: str, message: str) -> None:
    dialog = Gtk.MessageDialog(
        transient_for=parent,
        modal=True,
        message_type=Gtk.MessageType.INFO,
        buttons=Gtk.ButtonsType.CLOSE,
        text=title,
    )
    dialog.format_secondary_text(message)
    dialog.run()
    dialog.destroy()
