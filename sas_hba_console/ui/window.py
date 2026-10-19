"""主窗口：HBA 概览、硬盘列表、SMART 详情与电源控制。"""

from __future__ import annotations

import json
import os
import re
import time
from collections import deque

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402

from .. import APP_NAME, __version__  # noqa: E402
from ..backend.backends import create_backend  # noqa: E402
from .. import i18n
from ..i18n import t
from ..util import human_hours, iso_now  # noqa: E402
from . import format as fmt, theme  # noqa: E402
from .dialogs import (  # noqa: E402
    DiagnosticsDialog,
    HotRemoveDialog,
    show_about,
    show_error,
    show_info,
)
from .settings import ALLOWED_INTERVALS, load as load_settings, save as save_settings  # noqa: E402


# 硬盘表列
(
    COL_DOT,
    COL_DEV,
    COL_MODEL,
    COL_SIZE,
    COL_PROTO,
    COL_HEALTH,
    COL_TEMP,
    COL_POWER,
    COL_HOURS,
    COL_POS,
    COL_DRIVE,
    COL_DOT_COLOR,
    COL_HEALTH_COLOR,
    COL_TEMP_COLOR,
    COL_ROW_BG,
) = range(15)

def _detail_empty_hint() -> str:
    """运行时求值，保证语言切换后文案正确。"""
    return t("在左侧列表中选中一块硬盘，查看 SMART 健康信息，并控制停转、启动与安全热拔出。")


def _palette() -> dict:
    return theme.LIGHT if theme.current_theme() == "light" else theme.DARK


def _clear_box(box: Gtk.Box) -> None:
    for child in box.get_children():
        box.remove(child)


def _clear_grid(grid: Gtk.Grid) -> None:
    for child in grid.get_children():
        grid.remove(child)


class MainWindow(Gtk.ApplicationWindow):
    def __init__(
        self,
        application: Gtk.Application,
        mode: str = "auto",
        sys_root: str = "/",
    ):
        super().__init__(application=application, title=APP_NAME)
        self.mode = mode
        self.sys_root = sys_root
        self.settings = load_settings()
        i18n.set_language(self.settings.get("language", "auto"))
        self.theme_name = self.settings["theme"]
        self.auto_refresh = bool(self.settings["auto_refresh"])
        self.auto_refresh_seconds = int(self.settings["auto_refresh_seconds"])
        self.wake_on_smart = bool(self.settings["wake_on_smart"])

        self.backend = None
        self.ready = False
        self.busy = False
        self.refreshing = False
        self.snapshot: dict = {}
        self.hbas: list[dict] = []
        self.hba_filter: str | None = None
        self.drives: list[dict] = []
        self.drives_by_dev: dict[str, dict] = {}
        self.selected_dev: str | None = None
        self._pending_select: str | None = None
        self.smart_queue: deque[str] = deque()
        self.smart_active = 0
        self.refresh_generation = 0
        self._last_snapshot_mono = 0.0
        self._first_refresh_done = False
        self._toast_source = None
        self._banner_callback = None
        self._auto_source = None
        self._last_error = ""
        self._closing = False
        self._dot_cache: dict = {}
        # 正在执行电源/热拔动作的设备 -> 操作名；用于丢弃在途 SMART 结果。
        self._action_lock: dict[str, str] = {}
        self.disabled_phys: list[dict] = []

        self.set_default_size(
            int(self.settings["window_width"]), int(self.settings["window_height"])
        )
        self.set_size_request(1080, 700)
        # 旧接口只影响部分老 WM；首选程序级 Gdk program class（见 ui/app.py）。
        try:
            self.set_wmclass("sas-hba-console", "SasHbaConsole")
        except Exception:
            pass
        self.get_style_context().add_class("app-bg")
        icon = theme.load_icon(128)
        if icon is not None:
            self.set_icon(icon)

        theme.apply_theme(self.theme_name)
        self._build_ui()
        self._install_actions()
        self.show_all()
        self.auth_overlay.hide()
        self.banner_revealer.set_reveal_child(False)
        self.toast_revealer.set_reveal_child(False)
        self._new_backend()
        self._auto_source = GLib.timeout_add_seconds(5, self._auto_tick)
        self.connect("destroy", self._on_destroy)
        self.connect("delete-event", self._on_delete_event)

    # ================================================================== UI
    def _build_ui(self) -> None:
        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.set_title(APP_NAME)
        header.set_subtitle(t("通用 SAS / SCSI / RAID 控制器 · SMART 与电源控制"))
        self.header_subtitle = header
        self.set_titlebar(header)

        self.refresh_button = Gtk.Button()
        self.refresh_button.set_image(
            Gtk.Image.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON)
        )
        self.refresh_button.set_tooltip_text(t("刷新硬件与 SMART 信息（F5）"))
        self.refresh_button.connect("clicked", lambda _b: self._manual_refresh())
        header.pack_end(self.refresh_button)

        self.theme_button = Gtk.Button()
        self.theme_button.set_tooltip_text(t("切换浅色主题"))
        self.theme_button.connect("clicked", lambda _b: self._toggle_theme())
        header.pack_end(self.theme_button)

        self.menu_button = Gtk.MenuButton()
        self.menu_button.set_image(
            Gtk.Image.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.BUTTON)
        )
        self.menu_button.set_tooltip_text(t("菜单"))
        self.menu_button.set_popover(self._build_menu())
        header.pack_end(self.menu_button)
        self._update_theme_button()

        self.overlay = Gtk.Overlay()
        self.add(self.overlay)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        root.set_border_width(14)
        self.overlay.add(root)

        # 顶部断开提示条
        self.banner_revealer = Gtk.Revealer()
        self.banner_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.banner_revealer.set_transition_duration(160)
        banner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        banner.get_style_context().add_class("error-banner")
        self.banner_icon = Gtk.Image.new_from_icon_name(
            "dialog-error-symbolic", Gtk.IconSize.MENU
        )
        self.banner_label = Gtk.Label()
        self.banner_label.set_xalign(0)
        self.banner_label.set_line_wrap(True)
        self.banner_label.set_hexpand(True)
        self.banner_action = Gtk.Button()
        self.banner_action.get_style_context().add_class("ghost-button")
        self.banner_action.connect("clicked", self._on_banner_action)
        banner.pack_start(self.banner_icon, False, False, 0)
        banner.pack_start(self.banner_label, True, True, 0)
        banner.pack_start(self.banner_action, False, False, 0)
        self.banner_revealer.add(banner)
        root.pack_start(self.banner_revealer, False, False, 0)

        # HBA 卡片
        self.hba_card = self._build_hba_card()
        root.pack_start(self.hba_card, False, False, 0)

        # 主区域
        self.paned = Gtk.Paned.new(Gtk.Orientation.HORIZONTAL)
        self.paned.set_wide_handle(True)
        self.paned.set_position(int(self.settings["paned_position"]))
        self.drive_card = self._build_drive_card()
        self.detail_panel = self._build_detail_panel()
        self.paned.pack1(self.drive_card, resize=True, shrink=False)
        self.paned.pack2(self.detail_panel, resize=False, shrink=False)
        self.paned.set_vexpand(True)
        root.pack_start(self.paned, True, True, 0)

        # Toast
        self.toast_revealer = Gtk.Revealer()
        self.toast_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        self.toast_revealer.set_transition_duration(160)
        self.toast_label = Gtk.Label()
        self.toast_label.set_xalign(0)
        self.toast_label.set_line_wrap(True)
        self.toast_label.set_max_width_chars(120)
        self.toast_label.get_style_context().add_class("toast")
        self.toast_revealer.add(self.toast_label)
        self.toast_revealer.set_halign(Gtk.Align.START)
        root.pack_start(self.toast_revealer, False, False, 0)

        # 状态栏
        root.pack_start(self._build_statusbar(), False, False, 0)

        self._build_auth_overlay()

    def _build_menu(self) -> Gtk.Popover:
        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_border_width(8)
        box.set_size_request(250, -1)

        def action(label: str, callback):
            button = Gtk.ModelButton(text=label)
            button.connect("clicked", lambda _b: (popover.popdown(), callback()))
            box.pack_start(button, False, False, 0)
            return button

        action(t("刷新（F5）"), self._manual_refresh)
        action(t("重新扫描 SCSI 总线"), self._rescan_bus)
        self.restore_item = action(t("恢复已断开的端口链路"), self._restore_phy)
        box.pack_start(Gtk.Separator(), False, False, 5)

        self.auto_check = Gtk.CheckButton(label=t("自动刷新"))
        self.auto_check.set_active(self.auto_refresh)
        self.auto_check.set_margin_start(8)
        self.auto_check.connect("toggled", self._on_auto_refresh_toggled)
        box.pack_start(self.auto_check, False, False, 2)

        interval_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        interval_box.set_margin_start(28)
        interval_box.set_margin_end(8)
        interval_box.set_margin_bottom(3)
        interval_label = Gtk.Label(label=t("刷新间隔"))
        interval_label.get_style_context().add_class("muted")
        interval_label.get_style_context().add_class("tiny")
        self.interval_combo = Gtk.ComboBoxText()
        labels = {30: t("30 秒"), 60: t("1 分钟"), 120: t("2 分钟"), 300: t("5 分钟")}
        for seconds in ALLOWED_INTERVALS:
            self.interval_combo.append(str(seconds), labels[seconds])
        self.interval_combo.set_active_id(str(self.auto_refresh_seconds))
        self.interval_combo.set_sensitive(self.auto_refresh)
        self.interval_combo.connect("changed", self._on_interval_changed)
        interval_box.pack_start(interval_label, False, False, 0)
        interval_box.pack_start(self.interval_combo, True, True, 0)
        box.pack_start(interval_box, False, False, 0)

        self.wake_check = Gtk.CheckButton(label=t("查询 SMART 时唤醒休眠硬盘"))
        self.wake_check.set_active(self.wake_on_smart)
        self.wake_check.set_tooltip_text(t("关闭时使用 smartctl -n standby，可避免读 SMART 时把硬盘唤醒"))
        self.wake_check.set_margin_start(8)
        self.wake_check.connect("toggled", self._on_wake_toggled)
        box.pack_start(self.wake_check, False, False, 2)

        box.pack_start(Gtk.Separator(), False, False, 5)
        # 语言切换
        lang_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        lang_row.set_margin_start(8)
        lang_row.set_margin_end(8)
        lang_row.set_margin_bottom(3)
        lang_label = Gtk.Label(label=t("语言"))
        lang_label.get_style_context().add_class("muted")
        lang_label.get_style_context().add_class("tiny")
        self.lang_combo = Gtk.ComboBoxText()
        for code, label in (
            ("zh_CN", "简体中文"),
            ("en_US", "English"),
            ("auto", t("跟随系统")),
        ):
            self.lang_combo.append(code, label)
        self.lang_combo.set_active_id(self.settings.get("language", "zh_CN"))
        self.lang_combo.connect("changed", self._on_language_changed)
        lang_row.pack_start(lang_label, False, False, 0)
        lang_row.pack_start(self.lang_combo, True, True, 0)
        box.pack_start(lang_row, False, False, 0)

        action(t("切换深色 / 浅色主题"), self._toggle_theme)
        action(t("诊断信息"), self._show_diagnostics)
        if self.mode == "demo":
            action(t("重置演示数据"), self._reset_demo)
        box.pack_start(Gtk.Separator(), False, False, 5)
        action(t("关于"), self._show_about)
        action(t("退出（Ctrl+Q）"), self.close)

        popover.add(box)
        box.show_all()
        return popover

    def _build_hba_card(self) -> Gtk.Box:
        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        card.get_style_context().add_class("card")

        icon_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        icon_box.get_style_context().add_class("hba-icon-box")
        icon_box.set_valign(Gtk.Align.CENTER)
        self.hba_icon = Gtk.Image()
        pix = theme.load_icon(60)
        if pix is not None:
            self.hba_icon.set_from_pixbuf(pix)
        else:
            self.hba_icon.set_from_icon_name("drive-harddisk", Gtk.IconSize.DIALOG)
        icon_box.pack_start(self.hba_icon, False, False, 0)
        card.pack_start(icon_box, False, False, 0)

        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        info.set_hexpand(True)
        card.pack_start(info, True, True, 0)

        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.hba_model = Gtk.Label(label=t("正在检测存储控制器…"))
        self.hba_model.set_xalign(0)
        self.hba_model.get_style_context().add_class("hba-title")
        self.hba_model.set_ellipsize(Pango.EllipsizeMode.END)
        self.hba_chip = Gtk.Label(label="—")
        self.hba_chip.get_style_context().add_class("pill")
        self.hba_chip.get_style_context().add_class("pill-info")
        self.hba_mode = Gtk.Label(label=t("IT 直通"))
        self.hba_mode.get_style_context().add_class("pill")
        self.hba_mode.get_style_context().add_class("pill-ok")
        title_row.pack_start(self.hba_model, False, False, 0)
        title_row.pack_start(self.hba_chip, False, False, 0)
        title_row.pack_start(self.hba_mode, False, False, 0)
        self.hba_select = Gtk.ComboBoxText()
        self.hba_select.set_valign(Gtk.Align.CENTER)
        self.hba_select.set_size_request(-1, -1)
        self.hba_select.get_style_context().add_class("hba-select")
        self.hba_select.set_tooltip_text(t("切换要查看的控制器"))
        self.hba_select.set_no_show_all(True)
        self._hba_select_handler = self.hba_select.connect(
            "changed", self._on_hba_changed
        )
        info.pack_start(title_row, False, False, 0)

        select_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        select_label = Gtk.Label(label=t("控制器"))
        select_label.get_style_context().add_class("stat-label")
        select_row.pack_start(select_label, False, False, 0)
        select_row.pack_start(self.hba_select, False, False, 0)
        self.select_row = select_row
        info.pack_start(select_row, False, False, 0)

        self.hba_sub = Gtk.Label(label=t("等待后端连接…"))
        self.hba_sub.set_xalign(0)
        self.hba_sub.get_style_context().add_class("hba-sub")
        self.hba_sub.set_ellipsize(Pango.EllipsizeMode.END)
        self.hba_sub.set_max_width_chars(90)
        info.pack_start(self.hba_sub, False, False, 0)

        self.hba_stats_grid = Gtk.Grid(column_spacing=34, row_spacing=4)
        self.hba_stats_grid.set_margin_top(5)
        self.hba_stats: dict[str, Gtk.Label] = {}
        stats = [
            ("firmware", t("固件版本"), False),
            ("bios", t("BIOS 版本"), False),
            ("driver", t("驱动"), False),
            ("sas", t("SAS 地址"), True),
            ("pcie", t("PCIe 链路"), False),
            ("temp", t("板载温度"), False),
        ]
        for index, (key, title, mono) in enumerate(stats):
            cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            label = Gtk.Label(label=title)
            label.set_xalign(0)
            label.get_style_context().add_class("stat-label")
            value = Gtk.Label(label="—")
            value.set_xalign(0)
            value.set_selectable(True)
            value.get_style_context().add_class(
                "stat-value-mono" if mono else "stat-value"
            )
            value.set_ellipsize(Pango.EllipsizeMode.END)
            cell.pack_start(label, False, False, 0)
            cell.pack_start(value, False, False, 0)
            self.hba_stats_grid.attach(cell, index % 3, index // 3, 1, 1)
            self.hba_stats[key] = value
        info.pack_start(self.hba_stats_grid, False, False, 0)

        self.hba_warn_label = Gtk.Label()
        self.hba_warn_label.set_xalign(0)
        self.hba_warn_label.set_line_wrap(True)
        self.hba_warn_label.get_style_context().add_class("warning-banner")
        self.hba_warn_label.set_no_show_all(True)
        info.pack_start(self.hba_warn_label, False, False, 2)

        self.hba_hint = Gtk.Label()
        self.hba_hint.set_xalign(0)
        self.hba_hint.set_line_wrap(True)
        self.hba_hint.set_max_width_chars(95)
        self.hba_hint.get_style_context().add_class("muted")
        self.hba_hint.get_style_context().add_class("tiny")
        self.hba_hint.set_no_show_all(True)
        info.pack_start(self.hba_hint, False, False, 2)

        self.hba_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.hba_buttons.set_no_show_all(True)
        detect_button = Gtk.Button(label=t("重新检测"))
        detect_button.get_style_context().add_class("ghost-button")
        detect_button.connect("clicked", lambda _b: self._manual_refresh())
        self.hba_buttons.pack_start(detect_button, False, False, 0)
        self.demo_button = Gtk.Button(label=t("进入演示模式"))
        self.demo_button.get_style_context().add_class("ghost-button")
        self.demo_button.connect("clicked", lambda _b: self._switch_backend_mode("demo"))
        self.hba_buttons.pack_start(self.demo_button, False, False, 0)
        info.pack_start(self.hba_buttons, False, False, 2)

        card.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 0)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        right.set_valign(Gtk.Align.CENTER)
        right.set_halign(Gtk.Align.END)
        right.set_size_request(160, -1)
        self.hba_status_pill = Gtk.Label(label=t("等待连接"))
        self.hba_status_pill.get_style_context().add_class("pill")
        self.hba_status_pill.get_style_context().add_class("pill-idle")
        self.hba_status_pill.set_halign(Gtk.Align.END)
        right.pack_start(self.hba_status_pill, False, False, 0)
        self.hba_drive_count = Gtk.Label(label=t("0 块硬盘"))
        self.hba_drive_count.set_xalign(1)
        self.hba_drive_count.get_style_context().add_class("section-hint")
        right.pack_start(self.hba_drive_count, False, False, 0)
        self.hba_last = Gtk.Label(label=t("尚未刷新"))
        self.hba_last.set_xalign(1)
        self.hba_last.get_style_context().add_class("faint")
        self.hba_last.get_style_context().add_class("tiny")
        right.pack_start(self.hba_last, False, False, 0)
        card.pack_start(right, False, False, 0)
        return card

    def _build_drive_card(self) -> Gtk.Box:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
        card.get_style_context().add_class("card")
        card.get_style_context().add_class("card-tight")
        card.set_hexpand(True)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label=t("连接的硬盘"))
        title.set_xalign(0)
        title.get_style_context().add_class("section-title")
        self.drive_count_label = Gtk.Label(label="0")
        self.drive_count_label.get_style_context().add_class("pill")
        self.drive_count_label.get_style_context().add_class("pill-idle")
        self.drive_hint = Gtk.Label(label=t("选中硬盘查看 SMART 与电源控制"))
        self.drive_hint.set_xalign(0)
        self.drive_hint.set_ellipsize(Pango.EllipsizeMode.END)
        self.drive_hint.get_style_context().add_class("section-hint")
        head.pack_start(title, False, False, 0)
        head.pack_start(self.drive_count_label, False, False, 0)
        head.pack_start(self.drive_hint, True, True, 4)
        card.pack_start(head, False, False, 0)

        self.drive_stack = Gtk.Stack()
        self.drive_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.drive_stack.set_transition_duration(140)
        self.drive_stack.set_vexpand(True)
        card.pack_start(self.drive_stack, True, True, 0)

        # 表格
        # 第 0 列存 GdkPixbuf 圆点，避免被选中态的文字颜色覆盖。
        self.store = Gtk.ListStore(
            GdkPixbuf.Pixbuf, *([str] * 9 + [object] + [str] * 3 + [str])
        )
        self.treeview = Gtk.TreeView(model=self.store)
        self.treeview.set_headers_visible(True)
        self.treeview.set_grid_lines(Gtk.TreeViewGridLines.NONE)
        self.treeview.set_show_expanders(False)
        self.treeview.set_tooltip_column(-1)
        self.treeview.get_style_context().add_class("drive-table")
        self.treeview.set_activate_on_single_click(True)
        self._build_columns()
        selection = self.treeview.get_selection()
        selection.set_mode(Gtk.SelectionMode.SINGLE)
        selection.connect("changed", self._on_selection_changed)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_min_content_height(250)
        scrolled.add(self.treeview)
        self.drive_stack.add_named(scrolled, "table")

        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        empty.set_valign(Gtk.Align.CENTER)
        empty.set_halign(Gtk.Align.CENTER)
        empty.set_border_width(28)
        empty_icon = Gtk.Image.new_from_icon_name(
            "drive-harddisk-symbolic", Gtk.IconSize.DIALOG
        )
        empty_icon.set_opacity(0.55)
        self.empty_title = Gtk.Label(label=t("等待硬盘信息"))
        self.empty_title.get_style_context().add_class("empty-title")
        self.empty_hint = Gtk.Label(label=_detail_empty_hint())
        self.empty_hint.set_line_wrap(True)
        self.empty_hint.set_max_width_chars(62)
        self.empty_hint.set_justify(Gtk.Justification.CENTER)
        self.empty_hint.get_style_context().add_class("empty-hint")
        self.empty_button = Gtk.Button(label=t("重新检测"))
        self.empty_button.get_style_context().add_class("primary-button")
        self.empty_button.connect("clicked", lambda _b: self._manual_refresh())
        empty.pack_start(empty_icon, False, False, 0)
        empty.pack_start(self.empty_title, False, False, 0)
        empty.pack_start(self.empty_hint, False, False, 0)
        empty.pack_start(self.empty_button, False, False, 6)
        self.drive_stack.add_named(empty, "empty")
        self.drive_stack.set_visible_child_name("empty")
        return card

    def _add_column(
        self,
        title: str,
        index: int,
        width: int,
        mono: bool = False,
        bold: bool = False,
        xalign: float = 0.0,
        color_index: int | None = None,
        expand: bool = False,
        ellipsize: bool = False,
        pixbuf: bool = False,
    ) -> Gtk.TreeViewColumn:
        if pixbuf:
            renderer = Gtk.CellRendererPixbuf()
            renderer.set_property("xalign", 0.5)
            renderer.set_property("xpad", 6)
            column = Gtk.TreeViewColumn(title, renderer, pixbuf=index)
            column.set_fixed_width(width)
            column.set_min_width(width)
            column.set_resizable(False)
            column.set_sort_column_id(-1)
            self.treeview.append_column(column)
            return column
        renderer = Gtk.CellRendererText()
        renderer.set_property("xpad", 7)
        renderer.set_property("ypad", 9)
        renderer.set_property("xalign", xalign)
        if mono:
            renderer.set_property("family", "monospace")
        if bold:
            renderer.set_property("weight", Pango.Weight.SEMIBOLD)
        if ellipsize:
            renderer.set_property("ellipsize", Pango.EllipsizeMode.END)
        kwargs = {"text": index}
        if color_index is not None:
            kwargs["foreground"] = color_index
        column = Gtk.TreeViewColumn(title, renderer, **kwargs)
        column.set_fixed_width(width)
        column.set_min_width(max(24, width - 20))
        column.set_resizable(True)
        column.set_expand(expand)
        column.set_sort_column_id(index)
        column.set_clickable(True)
        self.treeview.append_column(column)
        return column

    def _build_columns(self) -> None:
        self._add_column(t("状态"), COL_DOT, 44, pixbuf=True)
        self._add_column(t("设备"), COL_DEV, 84, mono=True, bold=True)
        self._add_column(t("型号"), COL_MODEL, 180, expand=True, ellipsize=True)
        self._add_column(t("容量"), COL_SIZE, 82)
        self._add_column(t("协议"), COL_PROTO, 56)
        self._add_column(t("健康"), COL_HEALTH, 68, bold=True, color_index=COL_HEALTH_COLOR)
        self._add_column(t("温度"), COL_TEMP, 62, color_index=COL_TEMP_COLOR)
        self._add_column(t("电源"), COL_POWER, 66)
        self._add_column(t("通电时间"), COL_HOURS, 80)
        self._add_column(t("位置"), COL_POS, 122, ellipsize=True)

    def _build_detail_panel(self) -> Gtk.Box:
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        panel.get_style_context().add_class("detail-panel")
        panel.set_size_request(410, -1)

        self.detail_stack = Gtk.Stack()
        self.detail_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.detail_stack.set_transition_duration(120)
        panel.pack_start(self.detail_stack, True, True, 0)

        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        empty.set_valign(Gtk.Align.CENTER)
        empty.set_halign(Gtk.Align.CENTER)
        empty.set_border_width(26)
        icon = Gtk.Image.new_from_icon_name(
            "drive-harddisk-symbolic", Gtk.IconSize.DIALOG
        )
        icon.set_opacity(0.5)
        empty_title = Gtk.Label(label=t("选择一块硬盘"))
        empty_title.get_style_context().add_class("empty-title")
        empty_hint = Gtk.Label(label=_detail_empty_hint())
        empty_hint.set_line_wrap(True)
        empty_hint.set_max_width_chars(38)
        empty_hint.set_justify(Gtk.Justification.CENTER)
        empty_hint.get_style_context().add_class("empty-hint")
        empty.pack_start(icon, False, False, 0)
        empty.pack_start(empty_title, False, False, 0)
        empty.pack_start(empty_hint, False, False, 0)
        self.detail_stack.add_named(empty, "empty")

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        content.set_border_width(16)
        self.detail_stack.add_named(content, "content")
        self.detail_content = content

        # 标题
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        head_left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        head_left.set_hexpand(True)
        self.detail_title = Gtk.Label(label="—")
        self.detail_title.set_xalign(0)
        self.detail_title.set_line_wrap(True)
        self.detail_title.set_max_width_chars(24)
        self.detail_title.get_style_context().add_class("detail-title")
        title_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.detail_dev = Gtk.Label(label="—")
        self.detail_dev.set_xalign(0)
        self.detail_dev.get_style_context().add_class("detail-dev")
        self.detail_proto = Gtk.Label(label="—")
        self.detail_proto.get_style_context().add_class("pill")
        self.detail_proto.get_style_context().add_class("pill-info")
        self.detail_media = Gtk.Label(label="—")
        self.detail_media.get_style_context().add_class("pill")
        self.detail_media.get_style_context().add_class("pill-idle")
        title_line.pack_start(self.detail_dev, False, False, 0)
        title_line.pack_start(self.detail_proto, False, False, 0)
        title_line.pack_start(self.detail_media, False, False, 0)
        head_left.pack_start(self.detail_title, False, False, 0)
        head_left.pack_start(title_line, False, False, 0)

        head_right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        head_right.set_halign(Gtk.Align.END)
        self.detail_health = Gtk.Label(label="—")
        self.detail_health.get_style_context().add_class("pill")
        self.detail_health.get_style_context().add_class("pill-idle")
        self.detail_health.set_halign(Gtk.Align.END)
        head_right.pack_start(self.detail_health, False, False, 0)
        self.detail_refresh = Gtk.Button()
        self.detail_refresh.set_image(
            Gtk.Image.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.MENU)
        )
        self.detail_refresh.set_tooltip_text(t("重新读取此硬盘的 SMART"))
        self.detail_refresh.get_style_context().add_class("ghost-button")
        self.detail_refresh.set_halign(Gtk.Align.END)
        self.detail_refresh.connect("clicked", lambda _b: self._manual_refresh())
        head_right.pack_start(self.detail_refresh, False, False, 0)
        head.pack_start(head_left, True, True, 0)
        head.pack_start(head_right, False, False, 0)
        content.pack_start(head, False, False, 0)

        self.detail_serial = Gtk.Label(label="—")
        self.detail_serial.set_xalign(0)
        self.detail_serial.set_ellipsize(Pango.EllipsizeMode.END)
        self.detail_serial.get_style_context().add_class("muted")
        self.detail_serial.get_style_context().add_class("tiny")
        self.detail_serial.get_style_context().add_class("mono")
        content.pack_start(self.detail_serial, False, False, 0)

        self.detail_power = Gtk.Label(label=t("电源 —"))
        self.detail_power.set_xalign(0)
        self.detail_power.get_style_context().add_class("section-hint")
        content.pack_start(self.detail_power, False, False, 0)

        self.detail_blocker = Gtk.Label()
        self.detail_blocker.set_xalign(0)
        self.detail_blocker.set_line_wrap(True)
        self.detail_blocker.set_max_width_chars(44)
        self.detail_blocker.get_style_context().add_class("detail-blocker")
        self.detail_blocker.set_no_show_all(True)
        content.pack_start(self.detail_blocker, False, False, 0)

        # 概览
        overview = Gtk.Grid(column_spacing=20, row_spacing=6)
        self.detail_stats: dict[str, Gtk.Label] = {}
        stats = [
            ("temp", t("温度")),
            ("blocks", t("坏块数量")),
            ("hours", t("通电时间")),
            ("cycles", t("通电次数")),
            ("rpm", t("转速 / 介质")),
            ("position", t("位置")),
            ("capacity", t("容量")),
        ]
        for index, (key, title) in enumerate(stats):
            cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            label = Gtk.Label(label=title)
            label.set_xalign(0)
            label.get_style_context().add_class("kv-label")
            value = Gtk.Label(label="—")
            value.set_xalign(0)
            value.set_ellipsize(Pango.EllipsizeMode.END)
            value.get_style_context().add_class("kv-value")
            cell.pack_start(label, False, False, 0)
            cell.pack_start(value, False, False, 0)
            overview.attach(cell, index % 3, index // 3, 1, 1)
            self.detail_stats[key] = value
        content.pack_start(overview, False, False, 0)

        # Notebook
        self.notebook = Gtk.Notebook()
        self.notebook.set_vexpand(True)
        self.notebook.set_scrollable(True)
        content.pack_start(self.notebook, True, True, 0)

        self.attr_stack = Gtk.Stack()
        self.attr_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.attr_stack.set_transition_duration(100)

        # ID、名称、当前、最差、阈值、原始值、状态、颜色、提示
        self.attr_store = Gtk.ListStore(*([str] * 9))
        self.attr_view = Gtk.TreeView(model=self.attr_store)
        self.attr_view.set_tooltip_column(8)
        self.attr_view.set_headers_visible(True)
        self.attr_view.set_grid_lines(Gtk.TreeViewGridLines.NONE)
        self.attr_view.get_style_context().add_class("drive-table")
        attr_columns = [
            ("ID", 0, 40, 0.0, False),
            (t("属性"), 1, 108, 0.0, True),
            (t("当前"), 2, 40, 1.0, False),
            (t("最差"), 3, 40, 1.0, False),
            (t("阈值"), 4, 40, 1.0, False),
            (t("原始值"), 5, 84, 0.0, False),
            (t("状态"), 6, 58, 0.0, False),
        ]
        for title, index, width, xalign, expand in attr_columns:
            renderer = Gtk.CellRendererText()
            renderer.set_property("ypad", 5)
            renderer.set_property("xpad", 5)
            renderer.set_property("xalign", xalign)
            renderer.set_property("ellipsize", Pango.EllipsizeMode.END)
            if index in (0, 5):
                renderer.set_property("family", "monospace")
            if index == 6:
                renderer.set_property("weight", Pango.Weight.SEMIBOLD)
            column = Gtk.TreeViewColumn(
                title, renderer, text=index, foreground=7
            )
            column.set_fixed_width(width)
            column.set_resizable(True)
            column.set_expand(expand)
            self.attr_view.append_column(column)
        attr_scrolled = Gtk.ScrolledWindow()
        attr_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        attr_scrolled.set_min_content_height(150)
        attr_scrolled.add(self.attr_view)
        self.attr_stack.add_named(attr_scrolled, "table")

        scsi_scrolled = Gtk.ScrolledWindow()
        scsi_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scsi_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        scsi_box.set_border_width(6)
        self.scsi_grid = Gtk.Grid(column_spacing=16, row_spacing=6)
        scsi_box.pack_start(self.scsi_grid, False, False, 0)
        scsi_scrolled.add(scsi_box)
        self.attr_stack.add_named(scsi_scrolled, "scsi")

        note_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        note_box.set_valign(Gtk.Align.CENTER)
        note_box.set_halign(Gtk.Align.CENTER)
        note_box.set_border_width(18)
        note_icon = Gtk.Image.new_from_icon_name(
            "dialog-information-symbolic", Gtk.IconSize.DIALOG
        )
        note_icon.set_opacity(0.6)
        self.attr_note = Gtk.Label()
        self.attr_note.set_line_wrap(True)
        self.attr_note.set_max_width_chars(38)
        self.attr_note.set_justify(Gtk.Justification.CENTER)
        self.attr_note.get_style_context().add_class("empty-hint")
        self.attr_note_button = Gtk.Button(label=t("重新读取 SMART"))
        self.attr_note_button.get_style_context().add_class("ghost-button")
        self.attr_note_button.connect("clicked", lambda _b: self._manual_refresh())
        note_box.pack_start(note_icon, False, False, 0)
        note_box.pack_start(self.attr_note, False, False, 0)
        note_box.pack_start(self.attr_note_button, False, False, 0)
        self.attr_stack.add_named(note_box, "note")
        self.attr_stack.set_visible_child_name("note")

        self.notebook.append_page(self.attr_stack, Gtk.Label(label=t("SMART 属性")))

        self.info_scrolled = Gtk.ScrolledWindow()
        self.info_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        info_scrolled = self.info_scrolled
        info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        info_box.set_border_width(6)
        self.info_grid = Gtk.Grid(column_spacing=14, row_spacing=6)
        info_box.pack_start(self.info_grid, False, False, 0)
        self.info_messages_title = Gtk.Label(label=t("设备消息"))
        self.info_messages_title.set_xalign(0)
        self.info_messages_title.get_style_context().add_class("kv-label")
        self.info_messages = Gtk.Label()
        self.info_messages.set_xalign(0)
        self.info_messages.set_line_wrap(True)
        # 不要设为可选中：获得焦点时 GtkScrolledWindow 会自动滚动到底部。
        self.info_messages.set_selectable(False)
        self.info_messages.set_can_focus(False)
        self.info_messages.get_style_context().add_class("tiny")
        self.info_messages.get_style_context().add_class("muted")
        self.info_messages.get_style_context().add_class("mono")
        info_box.pack_start(self.info_messages_title, False, False, 0)
        info_box.pack_start(self.info_messages, False, False, 0)
        info_scrolled.add(info_box)
        self.notebook.append_page(info_scrolled, Gtk.Label(label=t("健康信息")))

        raw_scrolled = Gtk.ScrolledWindow()
        raw_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.raw_view = Gtk.TextView()
        self.raw_view.set_editable(False)
        self.raw_view.set_monospace(True)
        self.raw_view.set_left_margin(8)
        self.raw_view.set_right_margin(8)
        self.raw_view.set_top_margin(8)
        self.raw_view.set_bottom_margin(8)
        raw_scrolled.add(self.raw_view)
        self.notebook.append_page(raw_scrolled, Gtk.Label(label=t("原始 JSON")))

        # 操作按钮
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        actions.set_margin_top(2)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        self.btn_spin_down = self._make_action_button(
            t("停转"), "media-playback-pause-symbolic", "action-button"
        )
        self.btn_spin_down.connect("clicked", lambda _b: self._spin_down_selected())
        self.btn_spin_up = self._make_action_button(
            t("启动"), "media-playback-start-symbolic", "action-button"
        )
        self.btn_spin_up.connect("clicked", lambda _b: self._spin_up_selected())
        self.btn_remove = self._make_action_button(
            t("安全热拔出"), "media-eject-symbolic", "danger-button"
        )
        self.btn_remove.connect("clicked", lambda _b: self._hot_remove_selected())
        actions.pack_start(self.btn_spin_down, False, False, 0)
        actions.pack_start(self.btn_spin_up, False, False, 0)
        actions.pack_start(spacer, True, True, 0)
        actions.pack_start(self.btn_remove, False, False, 0)
        content.pack_start(actions, False, False, 0)
        return panel

    def _make_action_button(self, label: str, icon: str, css: str) -> Gtk.Button:
        button = Gtk.Button()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        image = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
        text = Gtk.Label(label=label)
        box.pack_start(image, False, False, 0)
        box.pack_start(text, False, False, 0)
        button.add(box)
        button.get_style_context().add_class(css)
        return button

    def _build_statusbar(self) -> Gtk.Box:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        bar.get_style_context().add_class("statusbar")
        self.conn_label = Gtk.Label()
        self.conn_label.set_markup(t('<span foreground="#8b98a8">●</span>  未连接'))
        self.conn_label.set_xalign(0)
        self.status_label = Gtk.Label(label=t("正在启动…"))
        self.status_label.set_xalign(0)
        self.status_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.status_label.set_hexpand(True)
        self.last_status = Gtk.Label(label=t("最后刷新 —"))
        self.last_status.get_style_context().add_class("tiny")
        self.last_status.get_style_context().add_class("faint")
        self.spinner = Gtk.Spinner()
        self.spinner.set_no_show_all(True)
        self.tools_label = Gtk.Label(label="")
        self.tools_label.get_style_context().add_class("tools-ok")
        bar.pack_start(self.conn_label, False, False, 0)
        bar.pack_start(self.status_label, True, True, 0)
        bar.pack_start(self.last_status, False, False, 0)
        bar.pack_start(self.spinner, False, False, 0)
        bar.pack_start(self.tools_label, False, False, 0)
        return bar

    def _build_auth_overlay(self) -> None:
        self.auth_overlay = Gtk.EventBox()
        self.auth_overlay.get_style_context().add_class("auth-overlay")
        self.auth_overlay.set_visible_window(True)
        align = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        align.set_valign(Gtk.Align.CENTER)
        align.set_halign(Gtk.Align.CENTER)
        align.set_hexpand(True)
        align.set_vexpand(True)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card.get_style_context().add_class("auth-card")
        card.set_size_request(510, -1)
        icon_image = Gtk.Image()
        pix = theme.load_icon(64)
        if pix is not None:
            icon_image.set_from_pixbuf(pix)
        else:
            icon_image.set_from_icon_name("system-lock-screen-symbolic", Gtk.IconSize.DIALOG)
        icon_image.set_halign(Gtk.Align.CENTER)
        card.pack_start(icon_image, False, False, 0)
        self.auth_title = Gtk.Label(label=t("需要 root 授权"))
        self.auth_title.get_style_context().add_class("auth-title")
        self.auth_title.set_halign(Gtk.Align.CENTER)
        card.pack_start(self.auth_title, False, False, 0)
        self.auth_body = Gtk.Label(
            label=t("读取 SMART、控制硬盘启停和热拔出都需要 root 权限。\n系统授权窗口会自动弹出，请输入当前用户密码。")

        )
        self.auth_body.set_justify(Gtk.Justification.CENTER)
        self.auth_body.set_line_wrap(True)
        self.auth_body.set_max_width_chars(52)
        self.auth_body.get_style_context().add_class("muted")
        card.pack_start(self.auth_body, False, False, 0)
        self.auth_spinner = Gtk.Spinner()
        self.auth_spinner.set_halign(Gtk.Align.CENTER)
        card.pack_start(self.auth_spinner, False, False, 0)
        self.auth_detail = Gtk.Label()
        self.auth_detail.set_line_wrap(True)
        self.auth_detail.set_max_width_chars(56)
        self.auth_detail.set_selectable(True)
        self.auth_detail.set_justify(Gtk.Justification.CENTER)
        self.auth_detail.get_style_context().add_class("tiny")
        self.auth_detail.get_style_context().add_class("faint")
        self.auth_detail.set_no_show_all(True)
        card.pack_start(self.auth_detail, False, False, 0)
        auth_hint = Gtk.Label(
            label=t("主界面不会以 root 运行；授权仅用于启动一个最小化的特权助手进程。")
        )
        auth_hint.set_line_wrap(True)
        auth_hint.set_max_width_chars(56)
        auth_hint.set_justify(Gtk.Justification.CENTER)
        auth_hint.get_style_context().add_class("tiny")
        auth_hint.get_style_context().add_class("faint")
        card.pack_start(auth_hint, False, False, 0)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        buttons.set_halign(Gtk.Align.CENTER)
        self.auth_retry = Gtk.Button(label=t("重新授权"))
        self.auth_retry.get_style_context().add_class("primary-button")
        self.auth_retry.connect("clicked", lambda _b: self._retry_auth())
        self.auth_readonly = Gtk.Button(label=t("以只读模式继续"))
        self.auth_readonly.get_style_context().add_class("ghost-button")
        self.auth_readonly.connect("clicked", lambda _b: self._switch_to_readonly())
        self.auth_quit = Gtk.Button(label=t("退出"))
        self.auth_quit.get_style_context().add_class("ghost-button")
        self.auth_quit.connect("clicked", lambda _b: self.close())
        buttons.pack_start(self.auth_retry, False, False, 0)
        buttons.pack_start(self.auth_readonly, False, False, 0)
        buttons.pack_start(self.auth_quit, False, False, 0)
        card.pack_start(buttons, False, False, 4)
        align.pack_start(card, False, False, 0)
        self.auth_overlay.add(align)
        self.overlay.add_overlay(self.auth_overlay)

    # =============================================================== actions
    def _install_actions(self) -> None:
        specs = [
            ("refresh", self._manual_refresh, ["F5", "<Control>r"]),
            ("rescan", self._rescan_bus, []),
            ("toggle-theme", self._toggle_theme, []),
            ("diagnostics", self._show_diagnostics, []),
            ("about", self._show_about, []),
            ("quit", self.close, ["<Control>q"]),
        ]
        application = self.get_application()
        for name, callback, accels in specs:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, cb=callback: cb())
            self.add_action(action)
            if application is not None and accels:
                application.set_accels_for_action(f"win.{name}", accels)

    def _glyph_pixbuf(self, kind: str):
        """自绘太阳/月亮图标，避免不同发行版图标主题缺名。"""
        cache = getattr(self, "_glyph_cache", None)
        if cache is None:
            cache = {}
            self._glyph_cache = cache
        key = (kind, self.theme_name)
        if key in cache:
            return cache[key]
        palette = _palette()
        size = 16
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        ctx = cairo.Context(surface)
        ctx.set_line_width(1.6)
        if kind == "sun":
            color = Gdk.RGBA()
            color.parse(palette["amber"])
            ctx.set_source_rgba(color.red, color.green, color.blue, 1)
            ctx.arc(size / 2, size / 2, 3.4, 0, 2 * 3.141592653589793)
            ctx.fill()
            for angle in range(0, 360, 45):
                import math

                rad = math.radians(angle)
                ctx.move_to(
                    size / 2 + math.cos(rad) * 5.4,
                    size / 2 + math.sin(rad) * 5.4,
                )
                ctx.line_to(
                    size / 2 + math.cos(rad) * 7.2,
                    size / 2 + math.sin(rad) * 7.2,
                )
            ctx.stroke()
        else:
            color = Gdk.RGBA()
            color.parse(palette["muted"])
            ctx.set_source_rgba(color.red, color.green, color.blue, 1)
            ctx.arc(size / 2, size / 2, 6.4, 0, 2 * 3.141592653589793)
            ctx.fill()
            ctx.set_operator(cairo.OPERATOR_DEST_OUT)
            ctx.arc(size / 2 + 3.6, size / 2 - 2.6, 5.6, 0, 2 * 3.141592653589793)
            ctx.fill()
            ctx.set_operator(cairo.OPERATOR_OVER)
        pixbuf = Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
        cache[key] = pixbuf
        return pixbuf

    def _update_theme_button(self) -> None:
        if self.theme_name == "dark":
            self.theme_button.set_image(
                Gtk.Image.new_from_pixbuf(self._glyph_pixbuf("sun"))
            )
            self.theme_button.set_tooltip_text(t("切换到浅色主题"))
        else:
            self.theme_button.set_image(
                Gtk.Image.new_from_pixbuf(self._glyph_pixbuf("moon"))
            )
            self.theme_button.set_tooltip_text(t("切换到深色主题"))

    def _toggle_theme(self) -> None:
        self.theme_name = "light" if self.theme_name == "dark" else "dark"
        theme.apply_theme(self.theme_name)
        self.settings["theme"] = self.theme_name
        save_settings(self.settings)
        self._update_theme_button()
        self._dot_cache = {}
        for drive in self.drives:
            self._update_row(drive)
        if self.selected_dev and self.selected_dev in self.drives_by_dev:
            self._update_detail(self.drives_by_dev[self.selected_dev])
        self._refresh_style_icons()
        self._show_toast(
            t("已切换到浅色主题") if self.theme_name == "light" else t("已切换到深色主题"),
            "info",
            3,
        )

    def _refresh_style_icons(self) -> None:
        # 图标颜色由主题提供；Gtk.Image 无需重建。保留接口便于后续扩展。
        return

    # =============================================================== backend
    def _new_backend(self) -> None:
        self.ready = False
        self.busy = False
        self.refreshing = False
        self.snapshot = {}
        self.hbas = []
        self.hba_filter = None
        self.drives = []
        self.drives_by_dev = {}
        self.selected_dev = None
        self.smart_queue.clear()
        self.smart_active = 0
        self.refresh_generation += 1
        self.backend = create_backend(
            self.mode,
            on_disconnect=self._on_backend_disconnect,
            sys_root=self.sys_root,
        )
        self._set_status(t("正在启动硬件访问后端…"))
        if self.backend.mode == "privileged":
            self._show_auth_waiting()
        else:
            self.auth_overlay.hide()
        self._update_connection()
        self.backend.start(self._on_backend_ready, self._on_backend_error)

    def _on_backend_ready(self, hello: dict) -> None:
        self.ready = True
        self._last_error = ""
        self.auth_overlay.hide()
        self.auth_spinner.stop()
        self.banner_revealer.set_reveal_child(False)
        self._update_connection()
        if self.backend.mode == "demo":
            self.header_subtitle.set_subtitle(
                t("演示模式 · 模拟 9207-8i / 9300-8i / MegaRAID 三种控制器")
            )
        else:
            self.header_subtitle.set_subtitle(t("通用 SAS / SCSI / RAID 控制器 · SMART 与电源控制"))
        self._set_status(t("已连接，正在读取硬件信息…"))
        self.refresh(silent=True)

    def _on_backend_error(self, message: str) -> None:
        self.ready = False
        self._last_error = message
        self.spinner.stop()
        self.spinner.hide()
        if self.backend is not None and self.backend.mode == "privileged":
            self._show_auth_error(message)
        else:
            self._show_banner(
                message, t("重试"), lambda: self._switch_backend_mode(self.mode)
            )
            self._set_status(t("后端未就绪"))
        self._update_action_buttons()

    def _on_backend_disconnect(self, message: str) -> None:
        self.ready = False
        self._last_error = message
        self.spinner.stop()
        self.spinner.hide()
        self._update_connection()
        self._show_banner(message, t("重新授权"), self._retry_auth)
        self._set_status(t("特权助手已断开"))
        self._update_action_buttons()

    def _retry_auth(self) -> None:
        target = "privileged" if os.geteuid() != 0 else "local"
        self._switch_backend_mode(target)

    def _switch_to_readonly(self) -> None:
        self._switch_backend_mode("readonly")

    def _switch_backend_mode(self, mode: str) -> None:
        if self.backend is not None:
            self.backend.close()
        self.backend = None
        self.mode = mode
        self.banner_revealer.set_reveal_child(False)
        self.store.clear()
        self.drive_stack.set_visible_child_name("empty")
        self.detail_stack.set_visible_child_name("empty")
        self.selected_dev = None
        self._new_backend()

    def _show_auth_waiting(self) -> None:
        self.auth_title.set_text(t("需要 root 授权"))
        self.auth_body.set_text(
            t("读取 SMART、控制硬盘启停和热拔出都需要 root 权限。\n系统授权窗口会自动弹出，请输入当前用户密码。")

        )
        self.auth_body.show()
        self.auth_detail.set_text("")
        self.auth_detail.hide()
        self.auth_spinner.show()
        self.auth_spinner.start()
        self.auth_retry.hide()
        self.auth_readonly.show()
        self.auth_quit.show()
        self.auth_overlay.show()
        self._set_status(t("等待 root 授权…"))

    def _show_auth_error(self, message: str) -> None:
        self.auth_title.set_text(t("无法获得 root 授权"))
        self.auth_body.set_text(t("硬件访问后端未能启动。你可以重新授权，或以只读模式查看 HBA 与硬盘列表。"))
        self.auth_body.show()
        self.auth_detail.set_text(message)
        self.auth_detail.show()
        self.auth_spinner.stop()
        self.auth_spinner.hide()
        self.auth_retry.show()
        self.auth_readonly.show()
        self.auth_quit.show()
        self.auth_overlay.show()
        self._set_status(t("未获得 root 授权"))
        self.spinner.stop()
        self.spinner.hide()

    # ================================================================ refresh
    def _manual_refresh(self) -> None:
        if self.busy:
            return
        self.refresh(silent=False)

    def refresh(self, silent: bool = False) -> None:
        if not self.ready or self.backend is None or self.busy:
            return
        if self.refreshing:
            return
        self.refreshing = True
        self._refresh_silent = silent
        self.refresh_generation += 1
        generation = self.refresh_generation
        self.smart_queue.clear()
        self.smart_active = 0
        self.spinner.show()
        self.spinner.start()
        self.refresh_button.set_sensitive(False)
        self._set_status(t("正在读取 HBA 与硬盘信息…"))
        self.backend.snapshot(
            lambda ok, data, error: self._on_snapshot(generation, ok, data, error)
        )

    def _on_snapshot(self, generation: int, ok: bool, data, error) -> None:
        if generation != self.refresh_generation:
            return
        if not ok or not isinstance(data, dict):
            self.refreshing = False
            self.spinner.stop()
            self.spinner.hide()
            self.refresh_button.set_sensitive(True)
            self._set_status(t("读取硬件信息失败"))
            self._show_toast(error or t("读取硬件信息失败"), "err", 6)
            self._show_banner(error or t("读取硬件信息失败"), t("重试"), self._manual_refresh)
            return

        previous = self.selected_dev or getattr(self, "_pending_select", None)
        self._pending_select = None
        self.snapshot = data
        self.drives = data.get("drives") or []
        self.drives_by_dev = {drive["dev"]: drive for drive in self.drives}
        self.disabled_phys = data.get("disabled_phys") or []
        self._update_restore_item()
        self.hbas = data.get("hbas") or ([data["hba"]] if data.get("hba") else [])
        known = {hba.get("pci_address") for hba in self.hbas}
        if self.hba_filter not in known:
            self.hba_filter = self.hbas[0]["pci_address"] if self.hbas else None
        self._rebuild_hba_select()
        self._update_hba_card()
        self._fill_table(previous)
        self._update_tools(data.get("tools") or {})
        self._update_connection()
        self._last_snapshot_mono = time.monotonic()

        # 启动 SMART 查询（最多 3 个并发）
        self.smart_queue = deque(drive["dev"] for drive in self.drives)
        self.smart_active = 0
        if not self.drives:
            self._finish_refresh(silent=getattr(self, "_refresh_silent", False))
            return
        for _ in range(3):
            self._pump_smart(generation)
        if self.smart_active == 0:
            self._finish_refresh(silent=getattr(self, "_refresh_silent", False))

    def _pump_smart(self, generation: int) -> None:
        while self.smart_active < 3 and self.smart_queue:
            dev = self.smart_queue.popleft()
            if dev not in self.drives_by_dev:
                continue
            drive = self.drives_by_dev[dev]
            drive["smart_loading"] = True
            self.smart_active += 1
            self.backend.smart(
                dev,
                self.wake_on_smart,
                lambda ok, data, error, d=dev, g=generation: self._on_smart(
                    g, d, ok, data, error
                ),
            )

    def _on_smart(self, generation: int, dev: str, ok: bool, data, error) -> None:
        if generation != self.refresh_generation:
            return
        self.smart_active = max(0, self.smart_active - 1)
        # 该设备正在执行停转/热拔出：丢弃在途结果，避免覆盖真实状态。
        if dev in self._action_lock:
            return
        drive = self.drives_by_dev.get(dev)
        if drive is not None:
            drive["smart_loading"] = False
            if ok and isinstance(data, dict):
                drive["smart"] = data
                if data.get("standby") or not data.get("available"):
                    drive["smart_error"] = data.get("error")
                else:
                    drive["smart_error"] = None
                if data.get("power_mode") in ("active", "standby", "sleep", "idle"):
                    drive["power_mode"] = data["power_mode"]
                if data.get("protocol") == "ATA":
                    drive["protocol"] = "SATA"
                elif data.get("protocol") == "SCSI":
                    drive["protocol"] = "SAS"
            else:
                drive["smart"] = None
                drive["smart_error"] = error or t("SMART 读取失败")
            self._update_row(drive)
            if self.selected_dev == dev:
                self._update_detail(drive)
            self._update_hba_card()
            self._maybe_query_apm(drive)
        self._pump_smart(generation)
        if self.smart_active == 0 and not self.smart_queue:
            self._finish_refresh(silent=getattr(self, "_refresh_silent", False))

    def _maybe_query_apm(self, drive: dict) -> None:
        """对 SATA 盘异步查询 APM / 自动停转状态（每块盘只查一次）。

        与 SMART 是否读取成功无关：待机盘、读取失败的盘同样需要这项信息，
        否则热拔对话框会因为拿不到风险数据而无法提示/勾选。
        """
        if not isinstance(drive, dict):
            return
        protocol = (drive.get("protocol") or "").upper()
        if protocol != "SATA":
            return
        if drive.get("_apm_queried"):
            return
        if self.backend is None or not self.backend.can_control:
            return
        dev = drive.get("dev")
        if not dev or dev in self._action_lock:
            return
        drive["_apm_queried"] = True
        self.backend.query_apm(dev, lambda ok, data, err, d=dev: self._on_apm(d, ok, data, err))

    def _on_apm(self, dev: str, ok: bool, data, error) -> None:
        drive = self.drives_by_dev.get(dev)
        if drive is None or not ok or not isinstance(data, dict):
            return
        drive["apm"] = data.get("apm") or {}
        drive["spdn_cnt"] = data.get("spdn_cnt")
        drive["power_saving_risk"] = data.get("risk") or {}
        if self.selected_dev == dev:
            self._update_detail(drive)

    def _finish_refresh(self, silent: bool) -> None:
        self.refreshing = False
        self.spinner.stop()
        self.spinner.hide()
        self.refresh_button.set_sensitive(True)
        now = time.strftime("%H:%M:%S")
        self.last_status.set_text(t("最后刷新 {0}", now))
        self.hba_last.set_text(t("最后刷新 {0}", now))
        failures = [
            drive
            for drive in self.drives
            if drive.get("smart_error") and not (drive.get("smart") or {}).get("standby")
        ]
        if failures:
            self._set_status(t("就绪 · {0} 块硬盘 SMART 读取失败", len(failures)))
        elif self.drives:
            self._set_status(t("就绪 · 已更新 {0} 块硬盘", len(self.drives)))
        else:
            self._set_status(t("就绪 · 未检测到硬盘"))
        self._update_action_buttons()
        if not silent:
            self._first_refresh_done = True
            if failures:
                self._show_toast(
                    t("刷新完成，但 {0} 块硬盘的 SMART 读取失败", len(failures)),
                    "warn",
                    5,
                )
            else:
                self._show_toast(t("已刷新 {0} 块硬盘的状态", len(self.drives)), "ok", 4)

    # ================================================================ 表格
    def _dot_pixbuf(self, state: str):
        palette = _palette()
        if state == "loading":
            state = "muted"
        key = (state, self.theme_name)
        cache = getattr(self, "_dot_cache", None)
        if cache is None:
            cache = {}
            self._dot_cache = cache
        if key in cache:
            return cache[key]
        color = Gdk.RGBA()
        hexcolor = {
            "ok": palette["green"],
            "warn": palette["amber"],
            "err": palette["red"],
            "idle": palette["faint"],
            "unknown": palette["muted"],
            "muted": palette["faint"],
        }.get(state, palette["muted"])
        color.parse(hexcolor)
        size = 10
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        ctx = cairo.Context(surface)
        ctx.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        ctx.arc(size / 2, size / 2, size / 2 - 0.5, 0, 2 * 3.141592653589793)
        ctx.fill()
        pixbuf = Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
        cache[key] = pixbuf
        return pixbuf

    def _row_values(self, drive: dict) -> list:
        state, health_text = fmt.health(drive)
        smart = drive.get("smart") or {}
        temp = smart.get("temperature_c")
        palette = _palette()
        if temp is None:
            temp_color = palette["muted"]
        elif int(temp) >= 50:
            temp_color = palette["red"]
        elif int(temp) >= 45:
            temp_color = palette["amber"]
        else:
            temp_color = palette["text"]
        return [
            self._dot_pixbuf(fmt.health(drive)[0]),
            drive.get("dev", ""),
            drive.get("model", ""),
            drive.get("size_human", ""),
            fmt.protocol_label(drive),
            health_text,
            fmt.temperature_text(drive),
            fmt.power_label(drive),
            human_hours(smart.get("power_on_hours")),
            drive.get("position") or "—",
            drive,
            fmt.dot_color(drive),
            fmt.health_color(drive),
            temp_color,
            "",
        ]

    def _current_hba(self) -> dict | None:
        if not self.hbas:
            return None
        for hba in self.hbas:
            if hba.get("pci_address") == self.hba_filter:
                return hba
        return self.hbas[0]

    def _visible_drives(self) -> list[dict]:
        hba = self._current_hba()
        if hba is None:
            return [drive for drive in self.drives if not drive.get("hba_pci")]
        if not any(drive.get("hba_pci") for drive in self.drives):
            return list(self.drives)
        return [
            drive
            for drive in self.drives
            if drive.get("hba_pci") == hba.get("pci_address")
        ]

    def _rebuild_hba_select(self) -> None:
        with self.hba_select.handler_block(self._hba_select_handler):
            self.hba_select.remove_all()
            for hba in self.hbas:
                label = "{0} · {1}".format(hba.get("model", t("控制器")), hba.get("pci_short", ""))
                self.hba_select.append(hba.get("pci_address", ""), label)
            if self.hba_filter:
                self.hba_select.set_active_id(self.hba_filter)
            elif self.hbas:
                self.hba_select.set_active(0)
        self.hba_select.set_visible(len(self.hbas) > 1)
        self.select_row.set_visible(len(self.hbas) > 1)
        self.hba_select.set_no_show_all(False)

    def _on_hba_changed(self, combo: Gtk.ComboBoxText) -> None:
        active = combo.get_active_id()
        if not active or active == self.hba_filter:
            return
        self.hba_filter = active
        previous = self.selected_dev
        visible = self._visible_drives()
        if previous not in {drive["dev"] for drive in visible}:
            previous = visible[0]["dev"] if visible else None
        self._fill_table(previous)
        self._update_hba_card()
        self._update_action_buttons()

    def _fill_table(self, previous: str | None) -> None:
        self.store.clear()
        selected_iter = None
        visible = self._visible_drives()
        for drive in visible:
            row = self.store.append(self._row_values(drive))
            if drive["dev"] == previous:
                selected_iter = row
        count = len(visible)
        self.drive_count_label.set_text(str(count))
        ctx = self.drive_count_label.get_style_context()
        ctx.remove_class("pill-ok")
        ctx.remove_class("pill-idle")
        ctx.add_class("pill-ok" if count else "pill-idle")
        if count:
            self.drive_stack.set_visible_child_name("table")
            selection = self.treeview.get_selection()
            if selected_iter is None:
                selected_iter = self.store.get_iter_first()
            if selected_iter is not None:
                selection.select_iter(selected_iter)
                self.treeview.scroll_to_cell(
                    self.store.get_path(selected_iter), None, False, 0, 0
                )
        else:
            self.drive_stack.set_visible_child_name("empty")
            self.detail_stack.set_visible_child_name("empty")
            hba = self._current_hba()
            if hba and hba.get("is_raid"):
                self.empty_title.set_text(t("该 RAID 控制器下未检测到逻辑卷"))
                self.empty_hint.set_text(
                    t("RAID 卡需要先在固件/管理工具中创建逻辑卷（Virtual Drive），系统才会出现 /dev/sdX。物理盘 SMART 需要厂商工具或 smartctl -d megaraid,N。")


                )
            elif hba:
                self.empty_title.set_text(t("该控制器上未检测到硬盘"))
                self.empty_hint.set_text(
                    t("请检查 SAS 线缆、背板供电与硬盘是否插紧；热插入后可点击“重新检测”，或使用菜单中的“重新扫描 SCSI 总线”。")

                )
            else:
                self.empty_title.set_text(t("未检测到存储控制器"))
                self.empty_hint.set_text(
                    t("请确认 HBA 已插好、对应内核驱动已加载（mpt3sas / megaraid_sas / smartpqi 等）；也可以点击 HBA 卡片中的“进入演示模式”预览完整界面。")

                )

    def _update_row(self, drive: dict) -> None:
        dev = drive.get("dev")
        values = self._row_values(drive)
        model = self.store
        row = model.get_iter_first()
        while row is not None:
            if model.get_value(row, COL_DRIVE).get("dev") == dev:
                for index, value in enumerate(values):
                    model.set_value(row, index, value)
                return
            row = model.iter_next(row)

    def _on_selection_changed(self, selection: Gtk.TreeSelection) -> None:
        model, row = selection.get_selected()
        if row is None:
            self.selected_dev = None
            self.detail_stack.set_visible_child_name("empty")
            self._update_action_buttons()
            return
        drive = model.get_value(row, COL_DRIVE)
        if not isinstance(drive, dict):
            return
        self.selected_dev = drive.get("dev")
        self._maybe_query_apm(drive)
        self._update_detail(drive)
        self._update_action_buttons()

    # ================================================================ HBA
    _TEXT_CLASSES = ("text-ok", "text-warn", "text-err", "text-muted")

    def _set_widget_color(self, widget: Gtk.Label, level: str) -> None:
        """按级别给标签上色（ok/warn/err/muted）。"""
        context = widget.get_style_context()
        for name in self._TEXT_CLASSES:
            context.remove_class(name)
        if level in ("ok", "warn", "err"):
            context.add_class(f"text-{level}")
        else:
            context.add_class("text-muted")

    def _set_pill(self, widget: Gtk.Label, text: str, css: str) -> None:
        widget.set_text(text)
        context = widget.get_style_context()
        for name in ("pill-ok", "pill-warn", "pill-err", "pill-info", "pill-idle"):
            context.remove_class(name)
        context.add_class(css)

    def _update_hba_card(self) -> None:
        hba = self._current_hba()
        visible = self._visible_drives()
        if hba:
            self.hba_model.set_text(hba.get("model") or t("存储控制器"))
            self.hba_chip.show()
            chip = hba.get("chip") or hba.get("class_name") or "—"
            self.hba_chip.set_text(chip)
            self.hba_mode.show()
            kind = hba.get("kind") or "SCSI HBA"
            if kind in ("RAID", "SmartRAID"):
                self._set_pill(self.hba_mode, kind, "pill-warn")
            elif kind in ("虚拟 SCSI", "FC HBA"):
                self._set_pill(self.hba_mode, kind, "pill-info")
            else:
                self._set_pill(self.hba_mode, kind, "pill-ok")

            sub_parts = [f"PCI {hba.get('pci_short', '—')}"]
            if hba.get("driver"):
                driver = hba["driver"]
                if hba.get("driver_version"):
                    driver += " " + hba["driver_version"]
                sub_parts.append(driver)
            if hba.get("host_ids"):
                sub_parts.append(" ".join(hba["host_ids"]))
            if hba.get("vendor_name"):
                sub_parts.append(hba["vendor_name"])
            if len(self.hbas) > 1:
                sub_parts.append(
                    t("控制器 {0}/{1}", hba.get("index", 1), len(self.hbas))
                )
            self.hba_sub.set_text(" · ".join(part for part in sub_parts if part))

            self.hba_stats["firmware"].set_text(
                hba.get("firmware_version") or "—"
            )
            self.hba_stats["bios"].set_text(hba.get("bios_version") or "—")
            driver_text = hba.get("driver") or "—"
            if hba.get("driver_version"):
                driver_text += " " + hba["driver_version"]
            self.hba_stats["driver"].set_text(driver_text)
            self.hba_stats["sas"].set_text(hba.get("sas_address") or "—")
            self.hba_stats["pcie"].set_text(hba.get("pcie_link") or "—")
            temp = hba.get("temperature_c")
            self.hba_stats["temp"].set_text(
                f"{temp} °C" if temp is not None else "—"
            )
            self.hba_stats_grid.show()
            warnings = list(hba.get("warnings") or [])
            if warnings:
                self.hba_warn_label.set_max_width_chars(120)
                self.hba_warn_label.set_text("• " + "\n• ".join(warnings[:3]))
                self.hba_warn_label.show()
            else:
                self.hba_warn_label.hide()
            self.hba_hint.hide()
            self.hba_buttons.set_no_show_all(True)
            self.hba_buttons.hide()
        else:
            self.hba_model.set_text(t("未检测到存储控制器"))
            self.hba_chip.hide()
            self.hba_mode.hide()
            self.hba_select.hide()
            self.select_row.hide()
            others = self.snapshot.get("other_controllers") or []
            if others:
                first = others[0]
                extra = t("（共 {0} 个存储控制器）", len(others)) if len(others) > 1 else ""
                self.hba_sub.set_text(
                    t(
                        "发现 {0} {1} · PCI {2} · 驱动 {3}",
                        first.get("model", t("未知控制器")),
                        extra,
                        first.get("pci_address", ""),
                        first.get("driver") or t("未加载"),
                    )
                )
                hint = (
                    t("该卡可能未被本程序识别，或驱动未加载。请确认 lspci -nn 能看到它，并用 lsmod 检查对应驱动；程序会读取系统 pci.ids 显示任意厂商的卡型号。")


                )
            else:
                self.hba_sub.set_text(t("未发现 PCI 存储控制器或对应内核驱动"))
                hint = (
                    t("请检查 HBA 是否插好、驱动是否加载；也可以进入演示模式预览多控制器界面。")

                )
            self.hba_stats_grid.hide()
            self.hba_warn_label.hide()
            self.hba_hint.set_text(hint)
            self.hba_hint.show()
            self.hba_buttons.set_no_show_all(False)
            self.hba_buttons.show_all()
            self.demo_button.set_visible(self.mode != "demo")
            self._set_pill(self.hba_status_pill, t("未就绪"), "pill-idle")
            self.hba_drive_count.set_text(t("0 块硬盘"))

        counts = fmt.health_counts(visible)
        summary, css = fmt.drives_summary(visible)
        self._set_pill(self.hba_status_pill, summary, css)
        total = len(self.drives)
        current = len(visible)
        if len(self.hbas) > 1:
            self.hba_drive_count.set_text(t("{0}/{1} 块硬盘", current, total))
        else:
            parts = [t("{0} 块硬盘", current)]
            if counts["ok"]:
                parts.append(t("{0} 正常", counts["ok"]))
            if counts["warn"]:
                parts.append(t("{0} 警告", counts["warn"]))
            if counts["err"]:
                parts.append(t("{0} 异常", counts["err"]))
            if counts["idle"]:
                parts.append(t("{0} 待机", counts["idle"]))
            self.hba_drive_count.set_text(" · ".join(parts))
        if counts["err"]:
            self._set_pill(self.hba_status_pill, t("存在异常"), "pill-err")
        elif counts["warn"]:
            self._set_pill(self.hba_status_pill, t("需要关注"), "pill-warn")

    def _update_tools(self, tools: dict) -> None:
        parts = []
        for name in ("smartctl", "sdparm", "sg_start"):
            info = tools.get(name) or {}
            if info.get("available"):
                version = info.get("version") or ""
                match = re.search(r"(\d+\.\d+(?:\.\d+)?)", version)
                parts.append(f"{name} {match.group(1)}" if match else name)
        context = self.tools_label.get_style_context()
        if not (tools.get("smartctl") or {}).get("available"):
            self.tools_label.set_text(t("未安装 smartmontools（SMART 不可用）"))
            context.add_class("tools-bad")
        else:
            context.remove_class("tools-bad")
            self.tools_label.set_text(" · ".join(parts))

    # ================================================================ 详情
    def _update_detail(self, drive: dict) -> None:
        if not isinstance(drive, dict):
            self.detail_stack.set_visible_child_name("empty")
            return
        self.detail_stack.set_visible_child_name("content")
        self.notebook.set_current_page(0)
        self.detail_title.set_text(drive.get("model") or t("未知硬盘"))
        self.detail_dev.set_text(drive.get("dev") or "—")
        self.detail_proto.set_text(fmt.protocol_label(drive))
        self.detail_media.set_text(drive.get("media") or "—")
        self.detail_serial.set_text(
            t(
                "序列号 {0}  ·  固件 {1}",
                drive.get("serial") or "—",
                drive.get("firmware") or "—",
            )
        )
        controller = drive.get("hba_model") or "—"
        self.detail_power.set_text(
            t(
                "控制器：{0}   ·  电源：{1}   ·  {2} · HCTL {3}",
                controller,
                fmt.power_label(drive),
                fmt.protocol_label(drive),
                drive.get("hctl") or "—",
            )
        )
        smart = drive.get("smart") or {}
        state, health_text = fmt.health(drive)
        self._set_pill(
            self.detail_health,
            health_text,
            {"ok": "pill-ok", "warn": "pill-warn", "err": "pill-err", "idle": "pill-idle"}.get(
                state, "pill-idle"
            ),
        )
        self.detail_stats["temp"].set_text(fmt.temperature_text(drive))
        self.detail_stats["blocks"].set_text(fmt.bad_blocks_text(drive))
        self._set_widget_color(
            self.detail_stats["blocks"], fmt.bad_blocks(drive)["level"]
        )
        self.detail_stats["hours"].set_text(human_hours(smart.get("power_on_hours")))
        self.detail_stats["cycles"].set_text(
            str(smart.get("power_cycle_count")) if smart.get("power_cycle_count") is not None else "—"
        )
        self.detail_stats["rpm"].set_text(fmt.rotation_text(drive))
        self.detail_stats["position"].set_text(drive.get("position") or "—")
        self.detail_stats["capacity"].set_text(drive.get("size_human") or "—")

        blockers = drive.get("blockers") or []
        if blockers:
            self.detail_blocker.set_text(t("⚠ 无法停转或热拔出：\n• ") + "\n• ".join(blockers))
            self.detail_blocker.show()
        else:
            self.detail_blocker.hide()

        self._update_attr_page(drive)
        self._update_info_page(drive)
        self._update_raw_page(drive)
        self._update_action_buttons()

    def _update_attr_page(self, drive: dict) -> None:
        smart = drive.get("smart") or {}
        if drive.get("hba_is_raid"):
            self.attr_note.set_text(
                t("这是 RAID 控制器上的逻辑卷（VD），通常不提供 ATA/SAS SMART 属性。\n物理盘 SMART 需通过厂商工具（storcli / arcconf / ssacli）或 smartctl -d megaraid,N 单独读取；逻辑卷也不能单独停转或热拔出。")



            )
            self.attr_note_button.hide()
            self.attr_stack.set_visible_child_name("note")
            return
        if drive.get("smart_loading"):
            self.attr_note.set_text(t("正在读取 SMART 数据…"))
            self.attr_note_button.hide()
            self.attr_stack.set_visible_child_name("note")
            return
        if not smart:
            self.attr_note.set_text(
                drive.get("smart_error") or t("尚未读取到 SMART 数据。可点击下方按钮重新读取。")
            )
            self.attr_note_button.show()
            self.attr_stack.set_visible_child_name("note")
            return
        if smart.get("standby"):
            self.attr_note.set_text(
                t("硬盘处于停转 / 待机状态，SMART 暂不可用。\n点击下方“启动”唤醒硬盘后，SMART 属性会自动恢复。")

            )
            self.attr_note_button.hide()
            self.attr_stack.set_visible_child_name("note")
            return
        if not smart.get("available"):
            self.attr_note.set_text(smart.get("error") or t("SMART 不可用"))
            self.attr_note_button.show()
            self.attr_stack.set_visible_child_name("note")
            return

        protocol = fmt.protocol_label(drive)
        if protocol == "SAS" or smart.get("protocol") == "SCSI":
            _clear_grid(self.scsi_grid)
            rows = fmt.scsi_rows_colored(drive)
            if not rows:
                self.attr_note.set_text(t("该 SAS 硬盘未报告 SCSI 错误计数器。"))
                self.attr_note_button.hide()
                self.attr_stack.set_visible_child_name("note")
                return
            for row, (label, value, level, reason) in enumerate(rows):
                key = Gtk.Label(label=label)
                key.set_xalign(0)
                key.get_style_context().add_class("kv-label")
                if level != "ok":
                    self._set_widget_color(key, level)
                val = Gtk.Label(label=value)
                val.set_xalign(0)
                val.set_selectable(True)
                val.get_style_context().add_class("kv-value")
                if level != "ok":
                    self._set_widget_color(val, level)
                    val.set_tooltip_text(reason or "")
                self.scsi_grid.attach(key, 0, row, 1, 1)
                self.scsi_grid.attach(val, 1, row, 1, 1)
                if reason:
                    marker = Gtk.Label(label="⚠")
                    marker.set_xalign(0)
                    self._set_widget_color(marker, level)
                    marker.set_tooltip_text(reason)
                    self.scsi_grid.attach(marker, 2, row, 1, 1)
            self.attr_stack.set_visible_child_name("scsi")
            self.scsi_grid.show_all()
            self.attr_note_button.hide()
            return

        rows = fmt.attribute_rows(drive)
        if not rows:
            self.attr_note.set_text(
                t("该硬盘未提供 ATA SMART 属性表。可查看“健康信息”和“原始 JSON”。")
            )
            self.attr_note_button.hide()
            self.attr_stack.set_visible_child_name("note")
            return
        self.attr_store.clear()
        for row in rows:
            self.attr_store.append(row)
        for index, row in enumerate(rows):
            if row[6]:
                GLib.idle_add(self._scroll_attr_to, index)
                break
        self.attr_stack.set_visible_child_name("table")

    def _scroll_attr_to(self, index: int) -> bool:
        path = Gtk.TreePath.new_from_string(str(index))
        self.attr_view.scroll_to_cell(path, None, False, 0.3, 0.3)
        return False

    def _update_info_page(self, drive: dict) -> None:
        _clear_grid(self.info_grid)
        smart = drive.get("smart") or {}
        rows = [
            (t("健康评估"), fmt.health(drive)[1]),
            (t("SMART 支持"), t("是") if smart.get("available") else t("否 / 未知")),
            (t("接口协议"), fmt.protocol_label(drive)),
            (t("所属控制器"), drive.get("hba_model") or "—"),
            (t("控制器类型"), drive.get("hba_kind") or "—"),
            (t("单盘启停/热拔"), t("支持") if drive.get("can_spin", True) else t("不支持（RAID/虚拟）")),
            (
                t("RAID 说明"),
                t("逻辑卷；物理盘 SMART 需 smartctl -d megaraid,N")
                if drive.get("hba_is_raid")
                else "—",
            ),
            (t("固件版本"), drive.get("firmware") or "—"),
            (t("序列号"), drive.get("serial") or "—"),
            ("WWN", drive.get("wwid") or "—"),
            (t("SAS 地址"), drive.get("sas_address") or "—"),
            (t("电源模式"), fmt.power_label(drive)),
            (t("APM / 自动省电"), self._apm_summary(drive)),
            (t("最近读取"), smart.get("timestamp") or "—"),
            (t("smartctl 退出码"), str(smart.get("exit_status")) if smart.get("exit_status") is not None else "—"),
            (t("容量"), drive.get("size_human") or "—"),
        ]
        for row, (key, value) in enumerate(rows):
            label = Gtk.Label(label=key)
            label.set_xalign(0)
            label.get_style_context().add_class("kv-label")
            val = Gtk.Label(label=str(value))
            val.set_xalign(0)
            val.set_line_wrap(True)
            val.set_selectable(False)
            val.set_can_focus(False)
            val.get_style_context().add_class("kv-value")
            self.info_grid.attach(label, 0, row, 1, 1)
            self.info_grid.attach(val, 1, row, 1, 1)
        # ---- 坏块明细 ----
        next_row = len(rows)
        section = Gtk.Label(label=t("坏块 / 坏道"))
        section.set_xalign(0)
        section.get_style_context().add_class("kv-label")
        section.get_style_context().add_class("bold")
        separator = Gtk.Separator()
        separator.set_margin_top(6)
        separator.set_margin_bottom(2)
        self.info_grid.attach(separator, 0, next_row, 2, 1)
        self.info_grid.attach(section, 0, next_row + 1, 2, 1)
        next_row += 2
        block_lines = fmt.bad_block_lines(drive)
        if block_lines:
            for label_text, value_text, level in block_lines:
                key = Gtk.Label(label=label_text)
                key.set_xalign(0)
                key.set_line_wrap(True)
                key.get_style_context().add_class("kv-value")
                val = Gtk.Label(label=value_text)
                val.set_xalign(0)
                val.set_can_focus(False)
                val.get_style_context().add_class("kv-value")
                if level != "ok":
                    self._set_widget_color(key, level)
                    self._set_widget_color(val, level)
                self.info_grid.attach(key, 0, next_row, 1, 1)
                self.info_grid.attach(val, 1, next_row, 1, 1)
                next_row += 1
            total = fmt.bad_blocks(drive)
            total_label = Gtk.Label(label=t("合计"))
            total_label.set_xalign(0)
            total_label.get_style_context().add_class("kv-label")
            total_value = Gtk.Label(label=t("{0} 个", total["total"]))
            total_value.set_xalign(0)
            total_value.set_can_focus(False)
            total_value.get_style_context().add_class("kv-value")
            total_value.get_style_context().add_class("bold")
            self._set_widget_color(total_value, total["level"])
            self.info_grid.attach(total_label, 0, next_row, 1, 1)
            self.info_grid.attach(total_value, 1, next_row, 1, 1)
            next_row += 1
        else:
            note = Gtk.Label(
                label=t("SMART 未提供坏块计数（待机、RAID 逻辑卷或该盘不支持）")
            )
            note.set_xalign(0)
            note.set_line_wrap(True)
            note.get_style_context().add_class("muted")
            note.get_style_context().add_class("tiny")
            self.info_grid.attach(note, 0, next_row, 2, 1)

        messages = fmt.smart_messages(drive)
        if drive.get("smart_error") and drive["smart_error"] not in messages:
            messages.insert(0, drive["smart_error"])
        if messages:
            self.info_messages.set_text("\n".join(f"• {item}" for item in messages))
            self.info_messages_title.show()
            self.info_messages.show()
        else:
            self.info_messages.set_text(t("无"))
            self.info_messages_title.show()
            self.info_messages.show()
        self.info_grid.show_all()

        def reset_scroll() -> bool:
            # 切换硬盘时回到顶部，避免沿用上一块硬盘的滚动位置。
            adjustment = self.info_scrolled.get_vadjustment()
            if adjustment is not None:
                adjustment.set_value(adjustment.get_lower())
            return False

        GLib.idle_add(reset_scroll)

    def _apm_summary(self, drive: dict) -> str:
        """把 APM 电平与自动停转次数转成一句话。"""
        apm = drive.get("apm") or {}
        level = apm.get("level")
        spdn = drive.get("spdn_cnt")
        if level is None and not spdn:
            return "—"
        parts = []
        if level is not None:
            parts.append(t("APM 电平 {0}", level))
        if spdn:
            parts.append(t("已自动停转 {0} 次", spdn))
        risk = drive.get("power_saving_risk") or {}
        if risk.get("risky"):
            parts.append(t("⚠ 可能自行起转"))
        return " · ".join(parts)

    def _update_raw_page(self, drive: dict) -> None:
        smart = drive.get("smart") or {}
        raw = smart.get("raw")
        buffer = self.raw_view.get_buffer()
        if raw:
            try:
                text = json.dumps(raw, ensure_ascii=False, indent=2)
            except (TypeError, ValueError):
                text = str(raw)
        else:
            text = t("暂无原始 SMART JSON。")
        buffer.set_text(text)

    def _update_action_buttons(self) -> None:
        drive = self.drives_by_dev.get(self.selected_dev or "")
        can = bool(
            self.ready
            and self.backend is not None
            and self.backend.can_control
            and not self.busy
        )
        if drive is None:
            for button, tip in (
                (self.btn_spin_down, t("请选择一块硬盘")),
                (self.btn_spin_up, t("请选择一块硬盘")),
                (self.btn_remove, t("请选择一块硬盘")),
            ):
                button.set_sensitive(False)
                button.set_tooltip_text(tip)
            return

        blockers = drive.get("blockers") or []
        idle = fmt.power_mode(drive) in ("standby", "sleep")
        ssd = drive.get("media") == "SSD"
        can_spin = bool(drive.get("can_spin", True))
        can_remove = bool(drive.get("can_remove", True))
        raid = bool(drive.get("hba_is_raid"))

        down_enabled = (
            can and can_spin and drive.get("rotational", True)
            and not ssd and not idle and not blockers
        )
        if not can:
            down_tip = t("需要 root 授权")
        elif raid or not can_spin:
            down_tip = t("该硬盘由 RAID/虚拟控制器管理，不能单独停转")
        elif ssd:
            down_tip = t("SSD 无需停转")
        elif idle:
            down_tip = t("硬盘已经停转")
        elif blockers:
            down_tip = t("硬盘正在使用，无法停转：") + "；".join(blockers)
        else:
            down_tip = t("让硬盘进入停转状态（需要盘未被挂载）")
        self.btn_spin_down.set_sensitive(down_enabled)
        self.btn_spin_down.set_tooltip_text(down_tip)

        up_enabled = can and can_spin and idle
        if not can:
            up_tip = t("需要 root 授权")
        elif raid or not can_spin:
            up_tip = t("该硬盘由 RAID/虚拟控制器管理，不能单独启动")
        elif not idle:
            up_tip = t("硬盘当前处于运行状态")
        else:
            up_tip = t("启动硬盘并等待其恢复响应")
        self.btn_spin_up.set_sensitive(up_enabled)
        self.btn_spin_up.set_tooltip_text(up_tip)

        remove_enabled = can and can_remove and not blockers
        if not can:
            remove_tip = t("需要 root 授权")
        elif raid or not can_remove:
            remove_tip = t("该硬盘由 RAID/虚拟控制器管理，不能单独热拔出")
        elif blockers:
            remove_tip = t("无法热拔出：") + "；".join(blockers)
        else:
            remove_tip = t("从内核安全移除设备，随后可物理拔出硬盘")
        self.btn_remove.set_sensitive(remove_enabled)
        self.btn_remove.set_tooltip_text(remove_tip)

    # ================================================================ 操作
    def _spin_down_selected(self) -> None:
        drive = self.drives_by_dev.get(self.selected_dev or "")
        if not drive:
            return
        self._run_action("spin_down", drive)

    def _spin_up_selected(self) -> None:
        drive = self.drives_by_dev.get(self.selected_dev or "")
        if not drive:
            return
        self._run_action("spin_up", drive)

    def _hot_remove_selected(self) -> None:
        drive = self.drives_by_dev.get(self.selected_dev or "")
        if not drive or self.busy:
            return
        if not drive.get("can_remove", True) or drive.get("hba_is_raid"):
            show_error(
                self,
                t("无法安全热拔出"),
                t("该硬盘由 RAID/虚拟控制器管理，请先在控制器固件中处理，不能对逻辑卷单独执行 SCSI 热移除。"),

            )
            return
        if drive.get("blockers"):
            show_error(
                self,
                t("无法安全热拔出"),
                t("该硬盘仍在使用中：\n") + "\n".join(f"• {item}" for item in drive["blockers"]),
            )
            return
        if (
            "power_saving_risk" not in drive
            and self.backend is not None
            and self.backend.can_control
            and (drive.get("protocol") or "").upper() == "SATA"
        ):
            # 先异步取一次 APM，再弹窗，保证 APM 选项与提示准确
            self._set_status(t("正在检测 APM 状态…"))
            self.backend.query_apm(
                drive.get("dev", ""),
                lambda ok, data, err: self._show_remove_dialog_after_apm(
                    drive, ok, data, err
                ),
            )
            return
        self._show_remove_dialog(drive)

    def _show_remove_dialog_after_apm(self, drive, ok, data, error) -> None:
        if ok and isinstance(data, dict):
            drive["apm"] = data.get("apm") or {}
            drive["spdn_cnt"] = data.get("spdn_cnt")
            drive["power_saving_risk"] = data.get("risk") or {}
        else:
            # 探测失败也要能继续，只是没有风险提示
            drive.setdefault("power_saving_risk", {})
        self._show_remove_dialog(drive)

    def _show_remove_dialog(self, drive: dict) -> None:
        dialog = HotRemoveDialog(self, drive)
        response = dialog.run()
        spin_first = dialog.get_spin_first()
        disable_apm = dialog.get_disable_apm()
        disconnect_phy = dialog.get_disconnect_phy()
        dialog.destroy()
        if response == Gtk.ResponseType.OK:
            self._run_action(
                "hot_remove",
                drive,
                {
                    "spin_first": spin_first,
                    "disable_apm_first": disable_apm,
                    "disconnect_phy": disconnect_phy,
                },
            )

    def _run_action(self, op: str, drive: dict, params: dict | None = None) -> None:
        if self.busy or not self.ready or self.backend is None:
            return
        if not self.backend.can_control:
            show_error(self, t("需要 root 授权"), t("该操作需要特权助手。请点击右上角菜单重新授权。"))
            return
        self.busy = True
        # 避免动作期间还有 smartctl 打开同一设备：移出队列并丢弃在途结果。
        dev = drive.get("dev", "")
        if dev:
            self.smart_queue = deque(d for d in self.smart_queue if d != dev)
            self._action_lock[dev] = op
        self.refresh_button.set_sensitive(False)
        self.detail_refresh.set_sensitive(False)
        self.spinner.show()
        self.spinner.start()
        self._update_action_buttons()
        labels = {"spin_down": t("正在停转"), "spin_up": t("正在启动"), "hot_remove": t("正在安全移除")}
        self._set_status("{0} {1}…".format(labels.get(op, t("正在执行")), dev))

        def callback(ok, data, error):
            self.busy = False
            self._action_lock.pop(dev, None)
            self.spinner.stop()
            self.spinner.hide()
            self.refresh_button.set_sensitive(True)
            self.detail_refresh.set_sensitive(True)
            if ok and isinstance(data, dict):
                message = data.get("message") or t("操作完成")
                warning = data.get("warning")
                diagnosis = data.get("phy_diagnosis") or ""
                if warning:
                    message += "\n" + warning
                self._show_toast(message, "warn" if warning else "ok", 7)
                # PHY 未断开时，把诊断信息明确展示出来，便于定位拓扑问题
                if diagnosis and not data.get("phy_disabled"):
                    show_error(
                        self,
                        t("未能断开端口链路"),
                        t("设备已移除，但没能断开该盘的 SAS 链路，控制器仍可能重新探测它。"),
                        diagnosis,
                    )
            else:
                self._set_status(t("操作失败"))
                show_error(self, t("操作失败"), error or t("未知错误"))
            self.refresh(silent=True)

        if op == "spin_down":
            self.backend.spin_down(dev, callback)
        elif op == "spin_up":
            self.backend.spin_up(dev, callback)
        elif op == "hot_remove":
            self.backend.hot_remove(
                dev,
                bool((params or {}).get("spin_first", True)),
                callback,
                bool((params or {}).get("disable_apm_first", False)),
                bool((params or {}).get("disconnect_phy", True)),
            )

    def _update_restore_item(self) -> None:
        """菜单里显示当前有多少个端口被断开，方便用户知道该不该点。"""
        item = getattr(self, "restore_item", None)
        if item is None:
            return
        count = len(getattr(self, "disabled_phys", []) or [])
        base = t("恢复已断开的端口链路")
        # Gtk.ModelButton 没有 set_text，label 是构造属性
        try:
            item.set_label(f"{base}（{count}）" if count else base)
        except AttributeError:
            item.set_property("text", f"{base}（{count}）" if count else base)
        item.set_sensitive(bool(count))

    def _restore_phy(self) -> None:
        if not self.ready or self.busy or self.backend is None:
            return
        if not self.backend.can_control:
            show_error(self, t("需要 root 授权"), t("恢复端口链路需要 root 权限。"))
            return
        self.busy = True
        self.spinner.show()
        self.spinner.start()
        self._set_status(t("正在恢复端口链路…"))

        def callback(ok, data, error):
            self.busy = False
            self.spinner.stop()
            self.spinner.hide()
            if ok and isinstance(data, dict):
                self._show_toast(data.get("message") or t("端口链路已恢复"), "ok", 5)
            else:
                show_error(self, t("恢复端口链路失败"), error or t("未知错误"))
            self.refresh(silent=True)

        self.backend.restore_phy(callback)

    def _rescan_bus(self) -> None:
        if not self.ready or self.busy or self.backend is None:
            return
        if not self.backend.can_control:
            show_error(self, t("需要 root 授权"), t("重新扫描 SCSI 总线需要 root 权限。"))
            return
        self.busy = True
        self.spinner.show()
        self.spinner.start()
        self._set_status(t("正在重新扫描 SCSI 总线…"))
        self._update_action_buttons()

        def callback(ok, data, error):
            self.busy = False
            self.spinner.stop()
            self.spinner.hide()
            if ok and isinstance(data, dict):
                self._show_toast(data.get("message") or t("总线扫描完成"), "ok", 4)
            else:
                self._set_status(t("总线扫描失败"))
                show_error(self, t("总线扫描失败"), error or t("未知错误"))
            self.refresh(silent=True)

        self.backend.rescan(callback)

    def _reset_demo(self) -> None:
        provider = getattr(self.backend, "provider", None)
        reset = getattr(provider, "reset", None)
        if callable(reset):
            reset()
            self._show_toast(t("演示数据已重置"), "info", 3)
            self.refresh(silent=True)

    # ================================================================ 其它
    def _set_status(self, text: str) -> None:
        self.status_label.set_text(text)

    def _update_connection(self) -> None:
        if not self.ready or self.backend is None:
            text, color = t("未连接"), _palette()["muted"]
        elif self.backend.mode == "demo":
            text, color = t("演示模式"), _palette()["green"]
        elif self.backend.mode == "readonly" or not self.backend.can_control:
            text, color = t("只读模式"), _palette()["amber"]
        elif self.backend.mode == "privileged":
            text, color = t("特权助手已连接"), _palette()["green"]
        else:
            text, color = t("本机 root"), _palette()["green"]
        self.conn_label.set_markup(
            f'<span foreground="{color}">●</span>  {GLib.markup_escape_text(text)}'
        )

    def _show_banner(self, message: str, action_label: str, callback) -> None:
        self.banner_label.set_text(message)
        self.banner_action.set_label(action_label)
        self.banner_action.show()
        self._banner_callback = callback
        self.banner_revealer.set_reveal_child(True)

    def _on_banner_action(self, _button) -> None:
        callback = self._banner_callback
        if callback:
            callback()

    def _show_toast(self, message: str, kind: str = "info", seconds: int = 4) -> None:
        if self._toast_source:
            GLib.source_remove(self._toast_source)
            self._toast_source = None
        context = self.toast_label.get_style_context()
        for name in ("toast-ok", "toast-err", "toast-info", "toast-warn"):
            context.remove_class(name)
        context.add_class(f"toast-{kind}")
        self.toast_label.set_text(message)
        self.toast_revealer.set_reveal_child(True)
        self._toast_source = GLib.timeout_add_seconds(max(1, seconds), self._hide_toast)

    def _hide_toast(self) -> bool:
        self.toast_revealer.set_reveal_child(False)
        self._toast_source = None
        return False

    def _on_auto_refresh_toggled(self, check: Gtk.CheckButton) -> None:
        self.auto_refresh = bool(check.get_active())
        self.interval_combo.set_sensitive(self.auto_refresh)
        self.settings["auto_refresh"] = self.auto_refresh
        save_settings(self.settings)
        self._show_toast(
            t("自动刷新已{0}", t("开启") if self.auto_refresh else t("关闭")),
            "info",
            3,
        )

    def _on_interval_changed(self, combo: Gtk.ComboBoxText) -> None:
        active = combo.get_active_id()
        if not active:
            return
        seconds = int(active)
        if seconds in ALLOWED_INTERVALS:
            self.auto_refresh_seconds = seconds
            self.settings["auto_refresh_seconds"] = seconds
            save_settings(self.settings)
            self._show_toast(t("自动刷新间隔：{0} 秒", seconds), "info", 3)

    def _on_language_changed(self, combo: Gtk.ComboBoxText) -> None:
        code = combo.get_active_id()
        if not code:
            return
        target = i18n.detect_system_language() if code == "auto" else code
        if target == i18n.get_language() and code == self.settings.get("language"):
            return
        self.settings["language"] = code
        save_settings(self.settings)
        i18n.set_language(code)
        self._rebuild_ui_for_language()

    def _rebuild_ui_for_language(self) -> None:
        """切换语言后重建界面（GTK 文案无法热替换，重建最可靠）。"""
        try:
            self.get_application().reload_window(self)
        except Exception:
            # 兜底：提示用户重启
            show_info(
                self,
                t("语言已切换"),
                t("界面将在下次启动时使用新语言。"),
            )

    def _on_wake_toggled(self, check: Gtk.CheckButton) -> None:
        self.wake_on_smart = bool(check.get_active())
        self.settings["wake_on_smart"] = self.wake_on_smart
        save_settings(self.settings)
        self._show_toast(
            t("读取 SMART 时将唤醒休眠硬盘") if self.wake_on_smart else t("读取 SMART 时不会唤醒休眠硬盘"),
            "info",
            4,
        )

    def _auto_tick(self) -> bool:
        if (
            self.auto_refresh
            and self.ready
            and not self.busy
            and not self.refreshing
            and self._last_snapshot_mono
            and time.monotonic() - self._last_snapshot_mono >= self.auto_refresh_seconds
        ):
            self.refresh(silent=True)
        return True

    # ================================================================ 诊断
    def _diagnostics_text(self) -> str:
        snapshot = self.snapshot or {}
        drives = []
        for drive in snapshot.get("drives") or []:
            smart = drive.get("smart") or {}
            drives.append(
                {
                    "dev": drive.get("dev"),
                    "hctl": drive.get("hctl"),
                    "model": drive.get("model"),
                    "serial": drive.get("serial"),
                    "size": drive.get("size_human"),
                    "protocol": drive.get("protocol"),
                    "power_mode": fmt.power_mode(drive),
                    "health": fmt.health(drive)[1],
                    "smart_available": smart.get("available"),
                    "smart_passed": smart.get("passed"),
                    "temperature_c": smart.get("temperature_c"),
                    "power_on_hours": smart.get("power_on_hours"),
                    "mountpoints": drive.get("mountpoints"),
                    "blockers": drive.get("blockers"),
                    "controller": drive.get("hba_model"),
                    "can_spin": drive.get("can_spin"),
                    "can_remove": drive.get("can_remove"),
                    "error": drive.get("smart_error"),
                }
            )
        data = {
            "app": APP_NAME,
            "version": __version__,
            "generated": iso_now(),
            "mode": self.mode,
            "backend": self.backend.label if self.backend else None,
            "root": os.geteuid() == 0,
            "display": os.environ.get("DISPLAY"),
            "session": os.environ.get("XDG_SESSION_TYPE"),
            "settings": self.settings,
            "hba": snapshot.get("hba"),
            "hbas": snapshot.get("hbas"),
            "other_controllers": snapshot.get("other_controllers"),
            "pci_ids_path": snapshot.get("pci_ids_path"),
            "sas_topology": snapshot.get("sas_topology"),
            "disabled_phys": snapshot.get("disabled_phys") or [],
            "tools": snapshot.get("tools"),
            "system": snapshot.get("system"),
            "drives": drives,
            "last_error": self._last_error,
        }
        return json.dumps(data, ensure_ascii=False, indent=2)

    def _show_diagnostics(self) -> None:
        dialog = DiagnosticsDialog(self, self._diagnostics_text())
        dialog.run()
        dialog.destroy()

    def _show_about(self) -> None:
        show_about(self)

    # ================================================================ 生命周期
    def _on_delete_event(self, _widget, _event) -> bool:
        if self.busy:
            dialog = Gtk.MessageDialog(
                transient_for=self,
                modal=True,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.YES_NO,
                text=t("操作正在进行"),
            )
            dialog.format_secondary_text(
                t("硬盘控制命令还没有结束。此时退出可能中断操作，确定要退出吗？")
            )
            response = dialog.run()
            dialog.destroy()
            if response != Gtk.ResponseType.YES:
                return True
        return False

    def _on_destroy(self, _widget) -> None:
        if self._closing:
            return
        self._closing = True
        if self._auto_source:
            GLib.source_remove(self._auto_source)
            self._auto_source = None
        width, height = self.get_size()
        if width > 200 and height > 200:
            self.settings["window_width"] = width
            self.settings["window_height"] = height
        self.settings["paned_position"] = self.paned.get_position()
        self.settings["theme"] = self.theme_name
        self.settings["auto_refresh"] = self.auto_refresh
        self.settings["auto_refresh_seconds"] = self.auto_refresh_seconds
        self.settings["wake_on_smart"] = self.wake_on_smart
        save_settings(self.settings)
        if self.backend is not None:
            self.backend.close()
            self.backend = None
