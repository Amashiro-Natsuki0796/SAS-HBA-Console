"""Gtk.Application 入口。"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gio", "2.0")
from gi.repository import Gdk, Gio, Gtk  # noqa: E402

from .. import APP_ID  # noqa: E402
from .window import MainWindow  # noqa: E402


class ConsoleApp(Gtk.Application):
    def __init__(self, mode: str = "auto", sys_root: str = "/"):
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.NON_UNIQUE,
        )
        self.mode = mode
        self.sys_root = sys_root
        self._window: MainWindow | None = None
        self._rebuilding = False

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        Gdk.set_program_class("SasHbaConsole")

    def do_activate(self) -> None:
        if self._window is None:
            self._window = MainWindow(self, self.mode, self.sys_root)
            self._window.connect("destroy", self._on_window_destroy)
        self._window.present()

    def reload_window(self, old_window) -> None:
        """切换语言后重建主窗口，保留控制器/硬盘选择。

        注意顺序：先建立新窗口，再销毁旧窗口。否则旧窗口的 destroy
        会触发 ``_on_window_destroy`` 把 ``self._window`` 清空并退出应用。
        """
        selection = getattr(old_window, "selected_dev", None)
        hba_filter = getattr(old_window, "hba_filter", None)
        size = old_window.get_size()
        was_maximized = old_window.is_maximized()

        self._rebuilding = True
        new_window = MainWindow(self, self.mode, self.sys_root)
        new_window.connect("destroy", self._on_window_destroy)
        if hba_filter:
            new_window.hba_filter = hba_filter
        if selection:
            new_window._pending_select = selection
        if size and size[0] > 200:
            new_window.resize(*size)
        self._window = new_window
        new_window.present()
        if was_maximized:
            new_window.maximize()

        # 旧窗口已无引用，安全销毁
        old_window.destroy()
        self._rebuilding = False

    def _on_window_destroy(self, window) -> None:
        if getattr(self, "_rebuilding", False):
            return  # 语言切换期间忽略旧窗口的销毁信号
        if window is not self._window:
            return
        self._window = None
        self.quit()

    def do_shutdown(self) -> None:
        if self._window is not None:
            self._window.destroy()
            self._window = None
        Gtk.Application.do_shutdown(self)
