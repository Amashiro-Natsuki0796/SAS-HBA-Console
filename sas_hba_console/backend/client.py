"""通过 pkexec 启动特权助手，并用 JSON 行协议通信。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Callable

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib  # noqa: E402

from .. import HELPER_PATH, LEGACY_HELPER_PATH, __version__  # noqa: E402
from ..util import find_tool, tail_lines  # noqa: E402

AUTH_TIMEOUT_SECONDS = 180
STDERR_LIMIT = 8192
Callback = Callable[[bool, object | None, str | None], None]


def classify_start_error(stderr: str, returncode: int | None = None) -> str:
    text = (stderr or "").lower()
    if "no authentication agent" in text:
        return (
            "未找到 polkit 认证代理，无法弹出授权窗口。\n"
            "请安装并启动 polkit 认证代理（如 lxpolkit / polkit-gnome），"
            "或以 root 身份运行本程序。"
        )
    if "no such file" in text or "not found" in text:
        return "找不到特权助手程序，安装可能不完整。请重新安装 sas-hba-console。"
    if returncode == 127 and not text:
        return "找不到特权助手程序，安装可能不完整。请重新安装 sas-hba-console。"
    if "not authorized" in text or "dismissed" in text or returncode in (126, 127):
        return "未获得 root 授权。已取消密码输入，或认证失败；可点击“重新授权”重试。"
    if "cannot open display" in text:
        return "无法打开图形显示，pkexec 未能启动授权对话框。"
    detail = tail_lines(stderr, 6)
    if detail:
        return f"特权助手启动失败：{detail}"
    return "特权助手启动失败，未收到 root 授权。"


class HelperClient:
    """管理 pkexec 子进程、请求 id 与超时。"""

    def __init__(
        self,
        command: list[str] | None = None,
        env: dict | None = None,
        pkexec: str | None = None,
    ):
        self._explicit_command = list(command) if command else None
        self._env = env
        self._pkexec = pkexec or find_tool("pkexec")
        self.proc: subprocess.Popen | None = None
        self._stdout_buffer = ""
        self._stdin_fd: int | None = None
        self._channels: list = []
        self.connected = False
        self.hello: dict = {}
        self._next_id = 0
        self._pending: dict[int, tuple[Callback | None, int | None]] = {}
        self._stdout_watch: int | None = None
        self._stderr_watch: int | None = None
        self._child_watch: int | None = None
        self._auth_timeout_id: int | None = None
        self._stderr = ""
        self._on_ready: Callable[[dict], None] | None = None
        self._on_error: Callable[[str], None] | None = None
        self._on_disconnect: Callable[[str], None] | None = None
        self._closing = False

    # ---------------------------------------------------------------- 启动
    def build_command(self) -> list[str]:
        if self._explicit_command:
            return list(self._explicit_command)
        if not self._pkexec:
            raise RuntimeError(
                "未安装 pkexec（polkit），无法请求 root 授权。\n"
                "请执行：apt install pkexec"
            )
        if os.path.exists(HELPER_PATH):
            return [self._pkexec, HELPER_PATH]
        if os.path.exists(LEGACY_HELPER_PATH):
            return [self._pkexec, LEGACY_HELPER_PATH]
        # 源码运行：使用带 sys.path 设置的开发助手脚本，pkexec 会清理 PYTHONPATH。
        project_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        dev_helper = os.path.join(project_root, "scripts", "sas-hba-helper-dev")
        if os.path.exists(dev_helper):
            return [self._pkexec, dev_helper]
        return [self._pkexec, sys.executable, "-m", "sas_hba_console.helper"]

    def start(
        self,
        on_ready: Callable[[dict], None],
        on_error: Callable[[str], None],
        on_disconnect: Callable[[str], None] | None = None,
    ) -> None:
        self._on_ready = on_ready
        self._on_error = on_error
        self._on_disconnect = on_disconnect
        try:
            command = self.build_command()
        except RuntimeError as exc:
            GLib.idle_add(on_error, str(exc))
            return

        env = dict(os.environ)
        if self._env:
            env.update(self._env)
        try:
            self.proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
                bufsize=0,
                env=env,
                close_fds=True,
            )
        except (OSError, ValueError) as exc:
            GLib.idle_add(on_error, f"无法启动特权助手：{exc}")
            return

        assert self.proc.stdout and self.proc.stderr and self.proc.stdin
        # 用非阻塞 fd 读取：避免在 GLib 回调里因半行数据而阻塞主循环。
        self._stdin_fd = self.proc.stdin.fileno()
        for stream in (self.proc.stdout, self.proc.stderr):
            os.set_blocking(stream.fileno(), False)
        self._stdout_watch = self._watch_fd(
            self.proc.stdout.fileno(), self._on_stdout
        )
        self._stderr_watch = self._watch_fd(
            self.proc.stderr.fileno(), self._on_stderr
        )
        try:
            self._child_watch = GLib.child_watch_add(
                GLib.PRIORITY_DEFAULT, self.proc.pid, self._on_child_exit
            )
        except GLib.Error:
            self._child_watch = None
        self._auth_timeout_id = GLib.timeout_add_seconds(
            AUTH_TIMEOUT_SECONDS, self._on_auth_timeout
        )

    # ---------------------------------------------------------------- IO
    def _watch_fd(self, fd: int, callback) -> int:
        """兼容旧版 PyGObject：优先直接 watch fd，失败则包一层 IOChannel。"""
        condition = GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR
        try:
            return GLib.io_add_watch(fd, GLib.PRIORITY_DEFAULT, condition, callback)
        except TypeError:
            channel = GLib.IOChannel.unix_new(fd)
            self._channels.append(channel)
            return GLib.io_add_watch(
                channel,
                GLib.PRIORITY_DEFAULT,
                condition,
                lambda _channel, cond: callback(fd, cond),
            )

    def _on_stdout(self, fd, condition) -> bool:
        try:
            while True:
                try:
                    chunk = os.read(fd, 65536)
                except BlockingIOError:
                    break
                if not chunk:
                    return False
                self._stdout_buffer += chunk.decode("utf-8", "replace")
            while "\n" in self._stdout_buffer:
                line, self._stdout_buffer = self._stdout_buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(payload, dict):
                    continue
                if payload.get("event") == "hello":
                    self.connected = True
                    self.hello = payload
                    if self._auth_timeout_id:
                        GLib.source_remove(self._auth_timeout_id)
                        self._auth_timeout_id = None
                    if self._on_ready:
                        self._on_ready(payload)
                    continue
                self._dispatch_response(payload)
        except OSError:
            self._stdout_watch = None
            return False
        if condition & (GLib.IO_HUP | GLib.IO_ERR):
            self._stdout_watch = None
            return False
        return True

    def _on_stderr(self, fd, condition) -> bool:
        try:
            while True:
                try:
                    chunk = os.read(fd, 8192)
                except BlockingIOError:
                    break
                if not chunk:
                    return False
                self._stderr = (self._stderr + chunk.decode("utf-8", "replace"))[
                    -STDERR_LIMIT:
                ]
        except OSError:
            self._stderr_watch = None
            return False
        if condition & (GLib.IO_HUP | GLib.IO_ERR):
            self._stderr_watch = None
            return False
        return True

    def _dispatch_response(self, payload: dict) -> None:
        req_id = payload.get("id")
        entry = self._pending.pop(req_id, None)
        if not entry:
            return
        callback, timeout_id = entry
        if timeout_id:
            GLib.source_remove(timeout_id)
        if callback:
            callback(
                bool(payload.get("ok")),
                payload.get("data"),
                payload.get("error"),
            )

    def _on_child_exit(self, pid: int, status: int) -> None:
        # GLib 在回调返回后自动移除 child watch，这里只清空记录。
        self._child_watch = None
        code = os.waitstatus_to_exitcode(status) if hasattr(os, "waitstatus_to_exitcode") else status
        if not self.connected:
            if self._auth_timeout_id:
                GLib.source_remove(self._auth_timeout_id)
                self._auth_timeout_id = None
            error = classify_start_error(self._stderr, code)
            if self._on_error:
                self._on_error(error)
        elif not self._closing:
            message = "特权助手已退出，硬件控制已断开。请点击“重新授权”。"
            detail = tail_lines(self._stderr, 3)
            if detail:
                message += f"\n{detail}"
            if self._on_disconnect:
                self._on_disconnect(message)
        self._fail_pending("特权助手已退出")

    def _on_auth_timeout(self) -> bool:
        self._auth_timeout_id = None
        if not self.connected:
            message = "等待 root 授权超时。请检查授权对话框，或点击“重新授权”。"
            self._terminate()
            if self._on_error:
                self._on_error(message)
        return False

    # ---------------------------------------------------------------- 请求
    def call(
        self,
        op: str,
        params: dict | None = None,
        callback: Callback | None = None,
        timeout: float = 30.0,
    ) -> None:
        if not self.connected or not self.proc or self._stdin_fd is None:
            if callback:
                callback(False, None, "特权助手尚未连接")
            return
        self._next_id += 1
        req_id = self._next_id
        payload = {"id": req_id, "op": op}
        if params:
            payload.update(params)
        timeout_id = GLib.timeout_add(
            max(1000, int(timeout * 1000)), self._on_request_timeout, req_id
        )
        self._pending[req_id] = (callback, timeout_id)
        data = (
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        try:
            written = 0
            while written < len(data):
                written += os.write(self._stdin_fd, data[written:])
        except (BrokenPipeError, OSError) as exc:
            entry = self._pending.pop(req_id, None)
            if entry:
                cb, tid = entry
                if tid:
                    GLib.source_remove(tid)
                if cb:
                    cb(False, None, f"无法向特权助手发送请求：{exc}")

    def _on_request_timeout(self, req_id: int) -> bool:
        entry = self._pending.pop(req_id, None)
        if entry and entry[0]:
            entry[0](False, None, "请求超时，请刷新后重试")
        return False

    def _fail_pending(self, message: str) -> None:
        pending = list(self._pending.values())
        self._pending.clear()
        for callback, timeout_id in pending:
            if timeout_id:
                GLib.source_remove(timeout_id)
            if callback:
                callback(False, None, message)

    # ---------------------------------------------------------------- 关闭
    def _terminate(self) -> None:
        if not self.proc:
            return
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
        except OSError:
            pass

    def close(self) -> None:
        self._closing = True
        self._fail_pending("程序正在退出")
        if self._auth_timeout_id:
            GLib.source_remove(self._auth_timeout_id)
            self._auth_timeout_id = None
        if self.proc:
            # 由 Python 的 file 对象负责关闭 fd，避免重复 close 触发
            # "Bad file descriptor"。
            self._stdin_fd = None
            try:
                if self.proc.stdin is not None:
                    self.proc.stdin.close()
            except OSError:
                pass
            # 给助手 2 秒完成收尾；随后强制终止。
            GLib.timeout_add(2000, self._terminate)
        for attribute in ("_stdout_watch", "_stderr_watch", "_child_watch"):
            source_id = getattr(self, attribute, None)
            if source_id:
                try:
                    GLib.source_remove(source_id)
                except (GLib.Error, ValueError):
                    pass
                setattr(self, attribute, None)

    @property
    def stderr_text(self) -> str:
        return self._stderr


def version() -> str:
    return __version__
