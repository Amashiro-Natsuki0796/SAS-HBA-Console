"""smartctl 调用与 SMART JSON 解析。"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from ..i18n import t
from ..util import find_tool, first_line, iso_now, run_cmd, tail_lines

_VERSION_CACHE: dict[str, tuple[float, dict]] = {}
_VERSION_TTL = 60.0


def _to_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _extract_attributes(payload: dict) -> list[dict]:
    table = (payload.get("ata_smart_attributes") or {}).get("table") or []
    attributes = []
    for row in table:
        if not isinstance(row, dict):
            continue
        raw = row.get("raw") or {}
        attributes.append(
            {
                "id": _to_int(row.get("id")),
                "name": row.get("name") or "",
                "value": _to_int(row.get("value")),
                "worst": _to_int(row.get("worst")),
                "thresh": _to_int(row.get("thresh")),
                "when_failed": row.get("when_failed") or "",
                "raw_value": raw.get("value"),
                "raw_string": raw.get("string") or "",
                "prefailure": bool((row.get("flags") or {}).get("prefailure")),
            }
        )
    attributes.sort(key=lambda item: item.get("id") or 0)
    return attributes


def _extract_scsi_counters(payload: dict) -> dict:
    counters = {}
    log = payload.get("scsi_error_counter_log") or {}
    for direction in ("read", "write", "verify"):
        entry = log.get(direction) or {}
        if not entry:
            continue
        counters[direction] = {
            "corrected": _to_int(entry.get("total_errors_corrected")),
            "uncorrected": _to_int(entry.get("total_uncorrected_errors")),
            "gigabytes": entry.get("gigabytes_processed"),
        }
    counters["grown_defects"] = _to_int(payload.get("scsi_grown_defect_list"))
    start_stop = payload.get("scsi_start_stop_cycle_counter") or {}
    if start_stop:
        counters["start_stop_cycles"] = _to_int(
            start_stop.get("accumulated_start_stop_cycles")
        )
    return counters


def _messages(payload: dict) -> list[dict]:
    raw = (payload.get("smartctl") or {}).get("messages") or []
    result = []
    for item in raw:
        if isinstance(item, dict):
            result.append(
                {
                    "string": item.get("string") or "",
                    "severity": item.get("severity") or "information",
                }
            )
    return result


def _error_reason(payload: dict, messages: list[dict]) -> str:
    for message in messages:
        if message.get("severity") in ("error", "warning"):
            return message.get("string") or ""
    for key in ("smartctl", "device"):
        section = payload.get(key)
        if isinstance(section, dict):
            for message in section.get("messages") or []:
                if isinstance(message, dict) and message.get("severity") == "error":
                    return message.get("string") or ""
    return ""


def parse_smart_json(payload: dict, returncode: int = 0, stderr: str = "") -> dict:
    """把 smartctl --json 输出转成界面使用的紧凑结构。"""
    if not isinstance(payload, dict):
        return {
            "available": False,
            "error": t("smartctl 未返回有效 JSON"),
            "stderr": stderr,
            "exit_status": returncode,
            "timestamp": iso_now(),
        }

    messages = _messages(payload)
    device = payload.get("device") or {}
    protocol = (device.get("protocol") or "").upper()
    power_mode = (payload.get("power_mode") or "").lower()
    standby = power_mode in ("standby", "sleep") or (
        returncode == 2
        and any("standby" in message["string"].lower() for message in messages)
    )
    if not power_mode and standby:
        power_mode = "standby"

    failed_attrs = [
        attr
        for attr in _extract_attributes(payload)
        if attr.get("when_failed") or (
            attr.get("prefailure")
            and attr.get("value") is not None
            and attr.get("thresh") is not None
            and attr["value"] <= attr["thresh"]
        )
    ]
    smart_status = payload.get("smart_status") or {}
    passed = smart_status.get("passed") if "passed" in smart_status else None
    if failed_attrs:
        passed = False
    if passed is None and returncode == 0 and protocol in ("ATA", "SCSI"):
        passed = True

    temperature = _to_int((payload.get("temperature") or {}).get("current"))
    scsi = _extract_scsi_counters(payload)
    reason = _error_reason(payload, messages)
    has_device = bool(device.get("name") or device.get("info_name"))

    available = bool(has_device and (protocol or payload.get("model_name")))
    if standby:
        available = False
        reason = t("硬盘处于待机/停转状态")
    elif returncode == 2:
        available = False
        reason = reason or t("无法读取 SMART（设备处于待机状态或打开失败）")

    if not power_mode:
        power_mode = "active" if available else "unknown"

    return {
        "available": available,
        "standby": standby,
        "power_mode": power_mode,
        "error": None if available else (reason or t("未能读取 SMART 数据")),
        "protocol": protocol,
        "model": payload.get("model_name")
        or payload.get("scsi_model_name")
        or payload.get("product")
        or "",
        "vendor": payload.get("scsi_vendor") or payload.get("vendor") or "",
        "serial": payload.get("serial_number") or "",
        "firmware": payload.get("firmware_version") or payload.get("revision") or "",
        "capacity_bytes": _to_int((payload.get("user_capacity") or {}).get("bytes")),
        "passed": passed,
        "temperature_c": temperature,
        "power_on_hours": _to_int((payload.get("power_on_time") or {}).get("hours")),
        "power_cycle_count": _to_int(payload.get("power_cycle_count")),
        "rotation_rate": _to_int(payload.get("rotation_rate")),
        "attributes": _extract_attributes(payload),
        "failed_attributes": [attr["id"] for attr in failed_attrs],
        "scsi": scsi,
        "messages": messages,
        "exit_status": returncode,
        "stderr": tail_lines(stderr, 6),
        "timestamp": iso_now(),
        "raw": payload,
    }


def read_smart(
    dev: str,
    wake: bool = False,
    timeout: float = 30.0,
    runner=run_cmd,
) -> dict:
    """读取单块硬盘的 SMART。默认使用 -n standby，避免唤醒休眠硬盘。"""
    tool = find_tool("smartctl")
    if not tool:
        return {
            "available": False,
            "error": t("未安装 smartmontools，请执行：apt install smartmontools"),
            "command": "",
            "timestamp": iso_now(),
        }

    base = [tool, "-a", "-j"]
    if not wake:
        base += ["-n", "standby"]
    cmd = base + [dev]
    result = runner(cmd, timeout=timeout)
    payload: dict = {}
    try:
        payload = json.loads(result.stdout) if result.stdout.strip() else {}
    except json.JSONDecodeError:
        payload = {}

    # 极少数 U 盘/SATA 桥接盒需要显式 -d sat；仅在自动探测失败时重试。
    if not payload:
        lowered = (result.stderr + result.stdout).lower()
        if "unknown device type" in lowered or "unable to detect device type" in lowered:
            cmd = base + ["-d", "sat", dev]
            result = runner(cmd, timeout=timeout)
            try:
                payload = json.loads(result.stdout) if result.stdout.strip() else {}
            except json.JSONDecodeError:
                payload = {}

    data = parse_smart_json(payload, result.returncode, result.stderr)
    data["command"] = " ".join(cmd)
    data["duration"] = round(result.duration, 2)
    if result.timed_out:
        data["available"] = False
        data["error"] = f"读取 SMART 超时（{timeout:.0f} 秒）"
    elif not payload and not data.get("error"):
        data["error"] = (
            tail_lines(result.stderr or result.stdout, 4)
            or f"smartctl 退出码 {result.returncode}"
        )
    return data


def query_power_state(dev: str, timeout: float = 10.0) -> str:
    """不唤醒硬盘地查询电源状态：active / standby / sleep / unknown。"""
    tool = find_tool("smartctl")
    if not tool:
        return "unknown"
    # 设备不存在时 smartctl 的报错文本里可能含 "standby"，会造成误判为已停转。
    if not os.path.exists(dev):
        return "missing"
    result = run_cmd([tool, "-i", "-n", "standby", "-j", dev], timeout=timeout)
    payload: dict = {}
    try:
        payload = json.loads(result.stdout) if result.stdout.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    mode = str(payload.get("power_mode") or "").lower()
    if mode in ("active", "standby", "sleep"):
        return mode
    text = (result.stdout + " " + result.stderr).lower()
    # 明确失败的信息优先判定为 unknown/missing，不要从错误文本里猜状态
    for needle in ("no such device", "not found", "cannot open", "permission denied"):
        if needle in text:
            return "unknown"
    if "device is in standby" in text or "device is in sleep" in text:
        return "standby" if "standby" in text else "sleep"
    if "standby" in text and "failed" not in text:
        return "standby"
    if "sleep" in text and "failed" not in text:
        return "sleep"
    if result.returncode == 0:
        return "active"
    return "unknown"


def tool_versions() -> dict:
    """返回工具可用性与版本，带 60 秒缓存。"""
    now = time.monotonic()
    cached = _VERSION_CACHE.get("tools")
    if cached and now - cached[0] < _VERSION_TTL:
        return cached[1]

    tools: dict[str, dict] = {}
    for name, args in (
        ("smartctl", ["--version"]),
        ("sdparm", ["--version"]),
        ("sg_start", ["--version"]),
    ):
        path = find_tool(name)
        if not path:
            tools[name] = {"available": False, "path": "", "version": ""}
            continue
        result = run_cmd([path] + args, timeout=5)
        tools[name] = {
            "available": True,
            "path": path,
            "version": first_line(result.stdout or result.stderr),
            "error": result.error,
        }

    for name in ("sas3flash", "blockdev", "lspci", "lsblk"):
        path = find_tool(name)
        tools[name] = {"available": bool(path), "path": path or "", "version": ""}

    _VERSION_CACHE["tools"] = (now, tools)
    return tools


def clear_tool_cache() -> None:
    _VERSION_CACHE.clear()
