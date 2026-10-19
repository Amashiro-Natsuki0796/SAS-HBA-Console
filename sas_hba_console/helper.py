"""特权助手：由 pkexec 以 root 启动，通过 stdin/stdout 的 JSON 行协议服务 GUI。

设计目标：
* GUI 本身不以 root 运行；
* 助手只暴露白名单操作，设备路径再次校验；
* 不经过 shell，不执行任意命令。
"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

from . import __version__
from .backend import control
from .backend.provider import LocalProvider, ProviderError

PROTOCOL_VERSION = 1
MAX_LINE = 1024 * 1024


def _send(payload: dict, lock: threading.Lock) -> bool:
    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    try:
        with lock:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
        return True
    except (BrokenPipeError, OSError):
        return False


def _error_text(exc: Exception) -> str:
    if isinstance(exc, (ProviderError, control.ControlError)):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"


def main() -> int:
    allow_nonroot = (
        os.environ.get("SAS_HBA_ALLOW_NONROOT")
        or os.environ.get("LSI9207_ALLOW_NONROOT")
    ) == "1"
    if os.geteuid() != 0 and not allow_nonroot:
        sys.stderr.write("sas-hba-helper 必须以 root 运行\n")
        return 1

    use_demo = (
        os.environ.get("SAS_HBA_HELPER_DEMO")
        or os.environ.get("LSI9207_HELPER_DEMO")
    ) == "1"
    if use_demo:
        from .backend.demo import DemoProvider

        provider = DemoProvider()
    else:
        provider = LocalProvider()

    write_lock = threading.Lock()
    executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="lsi9207")
    stop_event = threading.Event()

    hello = {
        "event": "hello",
        "protocol": PROTOCOL_VERSION,
        "version": __version__,
        "pid": os.getpid(),
        "euid": os.geteuid(),
        "demo": use_demo,
    }
    if not _send(hello, write_lock):
        return 1

    def handle(request: dict) -> None:
        req_id = request.get("id")
        op = request.get("op")
        try:
            if not isinstance(request, dict):
                raise ProviderError("请求格式错误")
            if op == "ping":
                data = {"pong": True, "version": __version__}
            elif op == "snapshot":
                data = provider.snapshot()
            elif op == "smart":
                dev = request.get("dev")
                if not isinstance(dev, str):
                    raise ProviderError("缺少设备路径")
                data = provider.smart(dev, wake=bool(request.get("wake", False)))
            elif op in ("spin_down", "spin_up", "hot_remove", "rescan", "query_apm", "restore_phy"):
                dev = request.get("dev", "")
                if op not in ("rescan", "restore_phy") and not isinstance(dev, str):
                    raise ProviderError("缺少设备路径")
                params = request.get("params") or {}
                if not isinstance(params, dict):
                    raise ProviderError("参数格式错误")
                data = provider.action(op, dev, params)
            else:
                raise ProviderError(f"不支持的操作：{op!r}")
            _send({"id": req_id, "ok": True, "data": data}, write_lock)
        except Exception as exc:  # 所有异常都回传给 GUI，避免助手崩溃
            _send(
                {
                    "id": req_id,
                    "ok": False,
                    "error": _error_text(exc),
                },
                write_lock,
            )
            if not isinstance(exc, (ProviderError, control.ControlError)):
                sys.stderr.write(traceback.format_exc())
                sys.stderr.flush()

    def shutdown(signum=None, frame=None):
        stop_event.set()
        try:
            sys.stdin.close()
        except OSError:
            pass

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    pending: list = []

    try:
        for line in sys.stdin:
            if len(line) > MAX_LINE:
                _send(
                    {"id": None, "ok": False, "error": "请求过大"},
                    write_lock,
                )
                continue
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                _send(
                    {"id": None, "ok": False, "error": "JSON 解析失败"},
                    write_lock,
                )
                continue
            if not isinstance(request, dict):
                _send(
                    {"id": None, "ok": False, "error": "请求必须是 JSON 对象"},
                    write_lock,
                )
                continue
            if request.get("op") == "shutdown":
                _send({"id": request.get("id"), "ok": True, "data": {"bye": True}}, write_lock)
                break
            pending.append(executor.submit(handle, request))
    finally:
        stop_event.set()
        # 已接收的请求要在进程退出前完成，否则 GUI 收不到响应。
        if pending:
            import concurrent.futures

            concurrent.futures.wait(pending, timeout=15)
            pending.clear()
        executor.shutdown(wait=False, cancel_futures=True)
    # GUI 已关闭或要求退出：立即结束，避免等待正在执行的 smartctl。
    try:
        sys.stdout.flush()
    except (BrokenPipeError, OSError):
        pass
    os._exit(0)


if __name__ == "__main__":
    raise SystemExit(main())
