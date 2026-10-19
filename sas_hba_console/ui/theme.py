"""主题、CSS 与图标资源。默认深色。"""

from __future__ import annotations

import os
from string import Template

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gtk  # noqa: E402

DARK = {
    "bg": "#0c1016",
    "bg_header": "#080b10",
    "surface": "#141a21",
    "surface_alt": "#1a222c",
    "surface_hover": "#212b37",
    "border": "#2a3542",
    "border_soft": "#202935",
    "text": "#e6edf3",
    "muted": "#8b98a8",
    "faint": "#5c6b7a",
    "accent": "#58a6ff",
    "accent_strong": "#1f6feb",
    "green": "#3fb950",
    "green_soft": "rgba(63, 185, 80, 0.14)",
    "amber": "#d29922",
    "amber_soft": "rgba(210, 153, 34, 0.16)",
    "red": "#f85149",
    "red_warn": "#ff9c8a",
    "red_soft": "rgba(248, 81, 73, 0.15)",
    "selection": "#1f3a5f",
    "overlay": "rgba(5, 8, 12, 0.86)",
    "shadow": "rgba(0, 0, 0, 0.45)",
    "scroll": "#394656",
    "row_alt": "#111820",
    "white": "#ffffff",
}

LIGHT = {
    "bg": "#f2f5f8",
    "bg_header": "#ffffff",
    "surface": "#ffffff",
    "surface_alt": "#f4f7fa",
    "surface_hover": "#eaeef3",
    "border": "#d4dce4",
    "border_soft": "#e4e9ef",
    "text": "#1b232c",
    "muted": "#5f6c7b",
    "faint": "#8b98a8",
    "accent": "#0969da",
    "accent_strong": "#0969da",
    "green": "#1a7f37",
    "green_soft": "rgba(26, 127, 55, 0.10)",
    "amber": "#9a6700",
    "amber_soft": "rgba(154, 103, 0, 0.12)",
    "red": "#cf222e",
    "red_warn": "#d1242f",
    "red_soft": "rgba(207, 34, 46, 0.10)",
    "selection": "#d7ebff",
    "overlay": "rgba(244, 247, 250, 0.90)",
    "shadow": "rgba(31, 35, 40, 0.15)",
    "scroll": "#b6c2cf",
    "row_alt": "#f7f9fc",
    "white": "#ffffff",
}

CSS = Template(
    """
window, .app-bg {
    background-color: $bg;
    color: $text;
    font-family: "Ubuntu", "Ubuntu Sans", "Noto Sans CJK SC", "Source Han Sans SC", "Droid Sans Fallback", "DejaVu Sans", sans-serif;
    font-size: 10.5pt;
}
headerbar, headerbar:backdrop {
    background-color: $bg_header;
    background-image: none;
    border-bottom: 1px solid $border;
    box-shadow: none;
    min-height: 50px;
    padding: 0 8px;
}
headerbar .title {
    color: $text;
    font-weight: 700;
    font-size: 11pt;
}
headerbar .subtitle {
    color: $muted;
    font-size: 8.5pt;
}
headerbar button {
    background: transparent;
    background-image: none;
    border: 1px solid transparent;
    border-radius: 7px;
    padding: 6px 7px;
    color: $muted;
    box-shadow: none;
}
headerbar button:hover {
    background-color: $surface_hover;
    color: $text;
}
headerbar button:active {
    background-color: $selection;
    color: $text;
}
.card {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: 12px;
    padding: 14px 16px;
}
.card-tight {
    padding: 12px;
}
.hba-title {
    font-size: 17pt;
    font-weight: 700;
    color: $text;
}
.hba-sub {
    color: $muted;
    font-size: 9.5pt;
}
.section-title {
    font-size: 11.5pt;
    font-weight: 700;
    color: $text;
}
.section-hint {
    color: $muted;
    font-size: 9pt;
}
.stat-label {
    color: $muted;
    font-size: 8.5pt;
}
.stat-value {
    color: $text;
    font-size: 10pt;
    font-weight: 600;
}
.stat-value-mono {
    color: $text;
    font-family: "Ubuntu Mono", "DejaVu Sans Mono", "Noto Sans Mono CJK SC", monospace;
    font-size: 9.5pt;
    font-weight: 500;
}
.mono {
    font-family: "Ubuntu Mono", "DejaVu Sans Mono", "Noto Sans Mono CJK SC", monospace;
}
.muted {
    color: $muted;
}
.text-ok {
    color: $green;
}
.text-warn {
    color: $red_warn;
}
.text-err {
    color: $red;
}
.text-muted {
    color: $muted;
}
.faint {
    color: $faint;
}
.tiny {
    font-size: 8.5pt;
}
.tools-ok {
    color: $muted;
    font-size: 8.5pt;
}
.tools-bad {
    color: $red;
    font-size: 8.5pt;
}
.bold {
    font-weight: 700;
}
.pill {
    border-radius: 99px;
    padding: 2px 10px;
    font-size: 8.8pt;
    font-weight: 600;
    background-color: $surface_alt;
    color: $muted;
    border: 1px solid transparent;
}
.pill-ok {
    background-color: $green_soft;
    color: $green;
    border-color: $green_soft;
}
.pill-warn {
    background-color: $amber_soft;
    color: $amber;
    border-color: $amber_soft;
}
.pill-err {
    background-color: $red_soft;
    color: $red;
    border-color: $red_soft;
}
.pill-info {
    background-color: rgba(88, 166, 255, 0.13);
    color: $accent;
    border-color: rgba(88, 166, 255, 0.22);
}
.pill-idle {
    background-color: $surface_alt;
    color: $muted;
    border-color: $border;
}
.pill-row-dot {
    background: transparent;
    border: none;
    box-shadow: none;
    padding: 0;
    font-size: 10pt;
}
.hba-icon-box {
    background-color: $surface_alt;
    border: 1px solid $border;
    border-radius: 12px;
    padding: 10px;
}
.drive-table {
    background-color: transparent;
}
.drive-table:selected,
.drive-table:selected:focus {
    background-color: $selection;
}
.drive-table:selected .row-title,
.drive-table:selected .row-sub {
    color: $text;
}
.drive-table cell {
    padding: 1px 2px;
}

treeview.drive-table.view {
    background-color: transparent;
    color: $text;
    border: none;
}
treeview.drive-table.view:hover {
    background-color: $surface_hover;
}
treeview.drive-table.view:selected {
    background-color: $selection;
    color: $text;
}
treeview.drive-table.view:selected:focus {
    background-color: $selection;
}
treeview.drive-table header button {
    background-color: transparent;
    background-image: none;
    border: none;
    border-bottom: 1px solid $border;
    border-radius: 0;
    box-shadow: none;
    color: $muted;
    font-size: 8.6pt;
    font-weight: 600;
    padding: 7px 8px;
}
treeview.drive-table header button:hover {
    color: $text;
    background-color: $surface_alt;
}
.detail-panel {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: 12px;
}
.detail-title {
    font-size: 15pt;
    font-weight: 700;
    color: $text;
}
.detail-dev {
    font-family: "Ubuntu Mono", "DejaVu Sans Mono", monospace;
    font-size: 10pt;
    color: $accent;
    font-weight: 600;
}
.detail-blocker {
    background-color: $amber_soft;
    border-radius: 8px;
    padding: 8px 10px;
    color: $amber;
    font-size: 9pt;
}
.detail-note {
    background-color: $surface_alt;
    border-radius: 8px;
    padding: 9px 10px;
    color: $muted;
    font-size: 9pt;
}
.kv-label {
    color: $muted;
    font-size: 8.7pt;
}
.kv-value {
    color: $text;
    font-size: 9.5pt;
    font-weight: 500;
}
.notebook, notebook {
    background-color: transparent;
    border: none;
}
notebook > header {
    background-color: transparent;
    border: none;
}
notebook > header tabs {
    background-color: transparent;
}
notebook tab {
    background-color: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    padding: 6px 10px;
    color: $muted;
    font-size: 9pt;
}
notebook tab:hover {
    color: $text;
}
notebook tab:checked {
    color: $text;
    border-bottom: 2px solid $accent;
}
notebook stack {
    background-color: transparent;
}
textview, textview text {
    background-color: $surface_alt;
    color: $text;
    font-family: "Ubuntu Mono", "DejaVu Sans Mono", monospace;
    font-size: 9pt;
}
entry, spinbutton {
    background-color: $surface_alt;
    color: $text;
    border: 1px solid $border;
    border-radius: 7px;
}
button {
    background-image: none;
    box-shadow: none;
}
.action-button {
    background-color: $surface_alt;
    border: 1px solid $border;
    border-radius: 8px;
    padding: 7px 14px;
    color: $text;
    font-weight: 600;
}
.action-button:hover {
    background-color: $surface_hover;
    border-color: $faint;
}
.action-button:disabled {
    color: $faint;
    background-color: $surface_alt;
    border-color: $border_soft;
}
.primary-button {
    background-color: $accent_strong;
    border: 1px solid $accent_strong;
    border-radius: 8px;
    padding: 7px 15px;
    color: $white;
    font-weight: 600;
}
.primary-button:hover {
    background-color: $accent;
    border-color: $accent;
}
.danger-button {
    background-color: $red_soft;
    border: 1px solid rgba(248, 81, 73, 0.35);
    border-radius: 8px;
    padding: 7px 14px;
    color: $red;
    font-weight: 600;
}
.danger-button:hover {
    background-color: rgba(248, 81, 73, 0.24);
}
.danger-button:disabled {
    color: $faint;
    border-color: $border_soft;
    background-color: $surface_alt;
}
.ghost-button {
    background: transparent;
    border: 1px solid $border;
    border-radius: 7px;
    padding: 5px 10px;
    color: $muted;
    font-size: 9pt;
}
.ghost-button:hover {
    color: $text;
    background-color: $surface_hover;
}
.statusbar {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: 9px;
    padding: 6px 12px;
    color: $muted;
    font-size: 8.8pt;
}
.status-dot {
    font-size: 9pt;
}
.toast {
    border-radius: 9px;
    padding: 9px 14px;
    font-size: 9.5pt;
    font-weight: 500;
    border: 1px solid transparent;
}
.toast-ok {
    background-color: $green_soft;
    color: $green;
    border-color: $green_soft;
}
.toast-err {
    background-color: $red_soft;
    color: $red;
    border-color: $red_soft;
}
.toast-info {
    background-color: rgba(88, 166, 255, 0.12);
    color: $accent;
    border-color: rgba(88, 166, 255, 0.22);
}
.toast-warn {
    background-color: $amber_soft;
    color: $amber;
    border-color: $amber_soft;
}
.auth-overlay {
    background-color: $overlay;
}
.auth-card {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: 14px;
    padding: 28px 34px;
}
.auth-title {
    font-size: 15pt;
    font-weight: 700;
    color: $text;
}
.empty-title {
    font-size: 13pt;
    font-weight: 600;
    color: $text;
}
.empty-hint {
    color: $muted;
    font-size: 9.5pt;
}
.warning-banner {
    background-color: $amber_soft;
    border: 1px solid rgba(210, 153, 34, 0.30);
    border-radius: 10px;
    padding: 10px 14px;
    color: $amber;
}
.error-banner {
    background-color: $red_soft;
    border: 1px solid rgba(248, 81, 73, 0.32);
    border-radius: 10px;
    padding: 10px 14px;
    color: $red;
}
.row-title {
    font-weight: 600;
    color: $text;
}
.row-sub {
    color: $muted;
    font-size: 8.8pt;
}
.dialog, dialog {
    background-color: $surface;
    color: $text;
}
.dialog .titlebar, dialog .titlebar {
    background-color: $surface;
    color: $text;
}
.dialog button, dialog button {
    background-color: $surface_alt;
    border: 1px solid $border;
    border-radius: 7px;
    color: $text;
    padding: 6px 14px;
}
.dialog button:hover, dialog button:hover {
    background-color: $surface_hover;
}
checkbutton {
    color: $text;
}
checkbutton check {
    background-color: $surface_alt;
    border: 1px solid $border;
    border-radius: 4px;
}
checkbutton check:checked {
    background-color: $accent_strong;
    border-color: $accent_strong;
    color: $white;
}
scrollbar {
    background-color: transparent;
}
scrollbar slider {
    background-color: $scroll;
    border-radius: 6px;
    min-width: 8px;
    min-height: 8px;
}
scrollbar slider:hover {
    background-color: $faint;
}
tooltip, tooltip.background {
    background-color: $surface_alt;
    background-image: none;
    color: $text;
    border: 1px solid $border;
    border-radius: 6px;
    box-shadow: none;
}
tooltip label {
    color: $text;
    background-color: transparent;
}
menubutton popover, popover {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: 10px;
}
popover modelbutton {
    color: $text;
    padding: 7px 12px;
}
popover modelbutton:hover {
    background-color: $surface_hover;
}
separator {
    background-color: $border;
    min-height: 1px;
    min-width: 1px;
}
"""
)

_current_provider: Gtk.CssProvider | None = None
_theme_name = "dark"


def _assets_dir() -> str:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    project_root = os.path.dirname(here)
    prefix_candidates = []
    # 标准安装位置：<prefix>/lib/python3/dist-packages/sas_hba_console
    marker = os.sep + os.path.join("lib", "python3", "")
    if marker in here:
        prefix = here.split(marker, 1)[0]
        prefix_candidates = [
            os.path.join(prefix, "share", "sas-hba-console", "assets"),
            os.path.join(prefix, "share", "sas-hba-console"),
        ]
    candidates = [
        os.path.join(here, "assets"),
        os.path.join(project_root, "assets"),
        os.environ.get("SAS_HBA_ASSETS", ""),
        os.environ.get("LSI9207_ASSETS", ""),
        "/usr/local/share/sas-hba-console/assets",
        "/usr/local/share/sas-hba-console",
        "/usr/share/sas-hba-console/assets",
        "/usr/share/sas-hba-console",
    ] + prefix_candidates
    icon = "sas-hba-console.svg"
    legacy_icon = "lsi9207-console.svg"
    existing = []
    for candidate in candidates:
        if candidate and os.path.isdir(candidate):
            existing.append(candidate)
            if os.path.isfile(os.path.join(candidate, icon)) or os.path.isfile(
                os.path.join(candidate, legacy_icon)
            ):
                return candidate
    if existing:
        return existing[0]
    return "/usr/share/sas-hba-console"


def asset_path(filename: str) -> str:
    return os.path.join(_assets_dir(), filename)


def apply_theme(theme: str) -> None:
    """切换深浅色主题。默认深色由窗口设置。"""
    global _current_provider, _theme_name
    _theme_name = "light" if theme == "light" else "dark"
    palette = LIGHT if _theme_name == "light" else DARK
    screen = Gdk.Screen.get_default()
    if screen is None:
        return
    provider = Gtk.CssProvider()
    css_text = CSS.substitute(palette)
    try:
        provider.load_from_data(css_text.encode("utf-8"))
    except TypeError:  # 旧版 PyGObject 只接受 str
        provider.load_from_data(css_text)
    if _current_provider is not None:
        Gtk.StyleContext.remove_provider_for_screen(screen, _current_provider)
    Gtk.StyleContext.add_provider_for_screen(
        screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_USER
    )
    _current_provider = provider
    settings = Gtk.Settings.get_default()
    if settings is not None:
        settings.set_property(
            "gtk-application-prefer-dark-theme", _theme_name == "dark"
        )


def current_theme() -> str:
    return _theme_name


def load_icon(size: int = 48) -> GdkPixbuf.Pixbuf | None:
    path = asset_path("sas-hba-console.svg")
    if not os.path.exists(path):
        return None
    try:
        return GdkPixbuf.Pixbuf.new_from_file_at_scale(path, size, size, True)
    except Exception:
        return None
