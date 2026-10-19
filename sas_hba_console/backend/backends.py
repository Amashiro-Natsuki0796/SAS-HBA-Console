"""GUI 使用的统一后端接口。

* InProcessBackend：演示模式，或程序已以 root 启动时直接访问硬件；
* HelperBackend：普通用户启动，通过 pkexec 特权助手访问硬件。
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib  # noqa: E402

from .client import HelperClient  # noqa: E402
from .demo import DemoProvider  # noqa: E402
from .provider import LocalProvider  # noqa: E402

Callback = Callable[[bool, object | None, str | None], None]


class InProcessBackend:
    mode = "local"
    can_control = False

    def __init__(self, provider, label: str, mode: str = "local"):
        self.provider = provider
        self.label = label
        self.mode = mode
        self.can_control = bool(getattr(provider, "can_control", mode == "demo"))
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="sas-hba-ui")

    def start(self, on_ready: Callable[[dict], None], on_error: Callable[[str], None]) -> None:
        hello = {
            "protocol": 1,
            "demo": self.mode == "demo",
            "euid": os.geteuid(),
            "mode": self.mode,
        }
        GLib.idle_add(on_ready, hello)

    def _submit(self, func, callback: Callback | None) -> None:
        def work():
            try:
                data = func()
                ok, error = True, None
            except Exception as exc:  # 统一转成用户可读错误
                data = None
                ok = False
                error = str(exc)
            if callback:
                GLib.idle_add(callback, ok, data, error)

        self._executor.submit(work)

    def snapshot(self, callback: Callback) -> None:
        self._submit(self.provider.snapshot, callback)

    def smart(self, dev: str, wake: bool, callback: Callback) -> None:
        self._submit(lambda: self.provider.smart(dev, wake=wake), callback)

    def spin_down(self, dev: str, callback: Callback) -> None:
        self._submit(lambda: self.provider.action("spin_down", dev), callback)

    def spin_up(self, dev: str, callback: Callback) -> None:
        self._submit(lambda: self.provider.action("spin_up", dev), callback)

    def hot_remove(
        self,
        dev: str,
        spin_first: bool,
        callback: Callback,
        disable_apm_first: bool = False,
        disconnect_phy: bool = True,
    ) -> None:
        self._submit(
            lambda: self.provider.action(
                "hot_remove",
                dev,
                {
                    "spin_first": spin_first,
                    "disable_apm_first": disable_apm_first,
                    "disconnect_phy": disconnect_phy,
                },
            ),
            callback,
        )

    def query_apm(self, dev: str, callback: Callback) -> None:
        self._submit(lambda: self.provider.action("query_apm", dev), callback)

    def restore_phy(self, callback: Callback) -> None:
        self._submit(lambda: self.provider.action("restore_phy", ""), callback)

    def rescan(self, callback: Callback) -> None:
        self._submit(lambda: self.provider.action("rescan", ""), callback)

    def close(self) -> None:
        try:
            self.provider.close()
        except Exception:
            pass
        self._executor.shutdown(wait=False, cancel_futures=True)


class HelperBackend:
    mode = "privileged"
    can_control = True

    def __init__(
        self,
        on_disconnect: Callable[[str], None] | None = None,
        client: HelperClient | None = None,
    ):
        self.label = "特权助手 · root"
        self._client = client or HelperClient()
        self._on_disconnect = on_disconnect

    def start(self, on_ready: Callable[[dict], None], on_error: Callable[[str], None]) -> None:
        self._client.start(on_ready, on_error, self._on_disconnect)

    def snapshot(self, callback: Callback) -> None:
        self._client.call("snapshot", callback=callback, timeout=60)

    def smart(self, dev: str, wake: bool, callback: Callback) -> None:
        self._client.call(
            "smart", {"dev": dev, "wake": wake}, callback=callback, timeout=75
        )

    def spin_down(self, dev: str, callback: Callback) -> None:
        self._client.call("spin_down", {"dev": dev}, callback=callback, timeout=120)

    def spin_up(self, dev: str, callback: Callback) -> None:
        self._client.call("spin_up", {"dev": dev}, callback=callback, timeout=120)

    def hot_remove(
        self,
        dev: str,
        spin_first: bool,
        callback: Callback,
        disable_apm_first: bool = False,
        disconnect_phy: bool = True,
    ) -> None:
        self._client.call(
            "hot_remove",
            {
                "dev": dev,
                "params": {
                    "spin_first": spin_first,
                    "disable_apm_first": disable_apm_first,
                    "disconnect_phy": disconnect_phy,
                },
            },
            callback=callback,
            timeout=180,
        )

    def query_apm(self, dev: str, callback: Callback) -> None:
        self._client.call("query_apm", {"dev": dev}, callback=callback, timeout=40)

    def restore_phy(self, callback: Callback) -> None:
        self._client.call("restore_phy", callback=callback, timeout=60)

    def rescan(self, callback: Callback) -> None:
        self._client.call("rescan", callback=callback, timeout=60)

    def close(self) -> None:
        self._client.close()

    @property
    def stderr_text(self) -> str:
        return self._client.stderr_text


def create_backend(
    mode: str,
    on_disconnect: Callable[[str], None] | None = None,
    sys_root: str = "/",
) -> InProcessBackend | HelperBackend:
    """mode: auto / demo / local / readonly / privileged。"""
    if mode == "demo":
        return InProcessBackend(DemoProvider(), "演示模式 · 模拟数据", "demo")
    if mode == "local":
        provider = LocalProvider(root=sys_root)
        label = "本机 root · 直接访问" if provider.can_control else "只读模式 · 未授权"
        return InProcessBackend(provider, label, "local")
    if mode == "readonly":
        provider = LocalProvider(root=sys_root, can_control=False)
        return InProcessBackend(provider, "只读模式 · 未授权", "readonly")
    if mode == "privileged":
        return HelperBackend(on_disconnect=on_disconnect)
    # auto
    if os.geteuid() == 0:
        provider = LocalProvider(root=sys_root)
        return InProcessBackend(provider, "本机 root · 直接访问", "local")
    return HelperBackend(on_disconnect=on_disconnect)
