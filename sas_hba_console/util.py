"""通用工具：安全读 sysfs、格式化、外部命令执行。

本模块不依赖 GTK，可被特权助手、测试和诊断工具复用。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence

# pkexec 会把 PATH 重置为固定值；开发态从终端启动时 PATH 可能缺少 sbin。
TOOL_DIRS = (
    "/usr/local/sbin",
    "/usr/local/bin",
    "/usr/sbin",
    "/usr/bin",
    "/sbin",
    "/bin",
)

# 允许覆盖工具路径，便于无 root 环境测试与打包验证。
TOOL_ENV = {
    "smartctl": "SAS_HBA_SMARTCTL",
    "sdparm": "SAS_HBA_SDPARM",
    "sg_start": "SAS_HBA_SG_START",
    "sas3flash": "SAS_HBA_SAS3FLASH",
    "blockdev": "SAS_HBA_BLOCKDEV",
}


def read_text(path: str, default: str = "") -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except (OSError, ValueError):
        return default


def read_int(path: str, default: int | None = None) -> int | None:
    text = read_text(path)
    if not text:
        return default
    try:
        return int(text, 0)
    except ValueError:
        try:
            return int(float(text))
        except ValueError:
            return default


def read_float(path: str, default: float | None = None) -> float | None:
    text = read_text(path)
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def listdir(path: str) -> list[str]:
    try:
        return sorted(os.listdir(path))
    except OSError:
        return []


def exists(path: str) -> bool:
    try:
        return os.path.exists(path)
    except OSError:
        return False


def strip_hex(value: str | None) -> str:
    """去掉 0x 前缀并转小写；空值返回空串。"""
    if not value:
        return ""
    value = value.strip()
    if value.lower().startswith("0x"):
        value = value[2:]
    return value.lower()


def human_size(num_bytes: int | float | None) -> str:
    if num_bytes is None:
        return "—"
    try:
        size = float(num_bytes)
    except (TypeError, ValueError):
        return "—"
    if size <= 0:
        return "—"
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    idx = 0
    while size >= 1000.0 and idx < len(units) - 1:
        size /= 1000.0
        idx += 1
    if idx == 0:
        return f"{int(size)} {units[idx]}"
    return f"{size:.2f} {units[idx]}"


def human_hours(hours: int | float | None) -> str:
    if hours is None:
        return "—"
    try:
        hours = int(hours)
    except (TypeError, ValueError):
        return "—"
    if hours < 0:
        return "—"
    return f"{hours:,} h"


def pcie_link(speed: str | None, width: str | None) -> str:
    speed = (speed or "").replace(" PCIe", "").strip()
    width = (width or "").strip()
    if not speed and not width:
        return "—"
    if speed and width:
        return f"{speed} ×{width}"
    return speed or f"×{width}"


def format_timestamp(ts: float | None = None) -> str:
    dt = datetime.fromtimestamp(ts if ts is not None else time.time())
    return dt.strftime("%H:%M:%S")


def iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def find_tool(name: str) -> str | None:
    """查找外部工具，兼容 pkexec 的受限 PATH。"""
    env_override = TOOL_ENV.get(name)
    if env_override:
        candidate = os.environ.get(env_override) or os.environ.get(
            env_override.replace("SAS_HBA_", "LSI9207_", 1)
        )
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    found = shutil.which(name)
    if found:
        return found
    for directory in TOOL_DIRS:
        candidate = os.path.join(directory, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


@dataclass
class RunResult:
    cmd: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    error: str = ""
    duration: float = 0.0

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.error

    def text(self) -> str:
        return (self.stdout + "\n" + self.stderr).strip()

    def as_dict(self) -> dict:
        return {
            "cmd": self.cmd,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timed_out": self.timed_out,
            "error": self.error,
            "duration": round(self.duration, 3),
        }


def run_cmd(
    cmd: Sequence[str],
    timeout: float = 20.0,
    env: dict | None = None,
) -> RunResult:
    """执行外部命令（不使用 shell，避免注入）。"""
    args = [str(part) for part in cmd]
    started = time.monotonic()
    run_env = dict(os.environ)
    run_env.setdefault("PATH", ":".join(TOOL_DIRS))
    # 固定 locale，便于解析英文输出；smartctl JSON 不受影响。
    run_env.setdefault("LC_ALL", "C")
    run_env.setdefault("LANG", "C")
    if env:
        run_env.update(env)
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=max(1.0, timeout),
            env=run_env,
            stdin=subprocess.DEVNULL,
        )
        return RunResult(
            cmd=args,
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            duration=time.monotonic() - started,
        )
    except subprocess.TimeoutExpired as exc:
        return RunResult(
            cmd=args,
            returncode=-1,
            stdout=(exc.stdout or "") if isinstance(exc.stdout, str) else "",
            stderr=(exc.stderr or "") if isinstance(exc.stderr, str) else "",
            timed_out=True,
            error=f"命令超时（{timeout:.0f} 秒）",
            duration=time.monotonic() - started,
        )
    except FileNotFoundError:
        return RunResult(
            cmd=args,
            returncode=-1,
            error=f"找不到命令：{args[0]}",
            duration=time.monotonic() - started,
        )
    except OSError as exc:
        return RunResult(
            cmd=args,
            returncode=-1,
            error=f"无法执行命令：{exc}",
            duration=time.monotonic() - started,
        )


def first_line(text: str) -> str:
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def tail_lines(text: str, limit: int = 8) -> str:
    lines = [line for line in (text or "").splitlines() if line.strip()]
    return "\n".join(lines[-limit:])


def unique(items: Iterable) -> list:
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
