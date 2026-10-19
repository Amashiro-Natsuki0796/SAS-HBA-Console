"""界面展示用的派生字段与健康判定。"""

from __future__ import annotations

from . import theme
from ..i18n import t

# 与坏块/坏道直接相关的 ATA 属性 ID
BAD_BLOCK_ATTRS = {
    5: "reallocated",
    187: "reported_uncorrect",
    196: "reallocated_events",
    197: "pending",
    198: "uncorrectable",
}

# 温度阈值（摄氏度）
TEMP_WARN = 50
TEMP_ERR = 58

# 需要关注的属性：ID -> (显示名, 单位)
_ATTR_INFO = {
    5: ("已重映射扇区数", "个"),
    187: ("报告的不可纠正错误", "次"),
    196: ("重映射事件次数", "次"),
    197: ("待映射扇区数", "个"),
    198: ("无法纠正扇区数", "个"),
    199: ("UDMA CRC 错误", "次"),
    194: ("温度", "°C"),
    190: ("气流温度", "°C"),
    202: ("SSD 剩余寿命", "%"),
    231: ("SSD 剩余寿命", "%"),
}


def _color(kind: str) -> str:
    palette = theme.LIGHT if theme.current_theme() == "light" else theme.DARK
    return {
        "ok": palette["green"],
        # 警告与异常都标红：警告用较亮的红，异常用主红。
        "warn": palette.get("red_warn", palette["red"]),
        "err": palette["red"],
        "idle": palette["muted"],
        "muted": palette["muted"],
    }.get(kind, palette["muted"])


def protocol_label(drive: dict) -> str:
    smart = drive.get("smart") or {}
    protocol = (smart.get("protocol") or "").upper()
    if protocol == "ATA":
        return "SATA"
    if protocol == "SCSI":
        return "SAS"
    if protocol == "NVME":
        return "NVMe"
    return drive.get("protocol") or "—"


def power_mode(drive: dict) -> str:
    smart = drive.get("smart") or {}
    mode = (smart.get("power_mode") or drive.get("power_mode") or "unknown").lower()
    if mode in ("standby", "sleep", "active", "idle"):
        return mode
    return "unknown"


def power_label(drive: dict) -> str:
    return {
        "active": t("运行"),
        "idle": t("空闲"),
        "standby": t("已停转"),
        "sleep": t("睡眠"),
    }.get(power_mode(drive), "—")


def temperature_text(drive: dict) -> str:
    smart = drive.get("smart") or {}
    temp = smart.get("temperature_c")
    if temp is None:
        return "—"
    return t("{0} °C", int(temp))


# ---------------------------------------------------------------------------
# 坏块统计
# ---------------------------------------------------------------------------


def _attr_table(smart: dict) -> dict[int, dict]:
    table: dict[int, dict] = {}
    for attr in smart.get("attributes") or []:
        try:
            table[int(attr.get("id") or 0)] = attr
        except (TypeError, ValueError):
            continue
    return table


def _as_int(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _raw_value(attr: dict | None) -> int | None:
    """从 raw_value 提取整数，兼容 "8 (8 个待映射扇区)" 这类文本。"""
    if not attr:
        return None
    raw = attr.get("raw_value")
    if isinstance(raw, str):
        digits = "".join(ch if ch.isdigit() else " " for ch in raw).split()
        return int(digits[0]) if digits else None
    return _as_int(raw)


def bad_blocks(drive: dict) -> dict:
    """汇总坏块数量。

    ATA：已重映射(5) + 待映射(197) + 无法纠正(198)
    SAS：增长缺陷列表；NVMe：介质错误
    """
    smart = drive.get("smart") or {}
    protocol = (smart.get("protocol") or "").upper()
    table = _attr_table(smart)
    result = {
        "kind": "",
        "reallocated": None,
        "pending": None,
        "uncorrectable": None,
        "grown": None,
        "total": None,
        "level": "unknown",
    }

    if protocol == "ATA" or table:
        reallocated = _raw_value(table.get(5))
        pending = _raw_value(table.get(197))
        uncorrectable = _raw_value(table.get(198))
        if any(v is not None for v in (reallocated, pending, uncorrectable)):
            reallocated = reallocated or 0
            pending = pending or 0
            uncorrectable = uncorrectable or 0
            total = reallocated + pending + uncorrectable
            result.update(
                kind="ATA",
                reallocated=reallocated,
                pending=pending,
                uncorrectable=uncorrectable,
                total=total,
                level="ok" if total == 0 else ("err" if total >= 16 else "warn"),
            )
            return result

    scsi = smart.get("scsi") or {}
    grown = _as_int(scsi.get("grown_defects"))
    if grown is not None:
        result.update(
            kind="SAS",
            grown=grown,
            total=grown,
            level="ok" if grown == 0 else ("err" if grown >= 16 else "warn"),
        )
        return result

    nvme = (smart.get("raw") or {}).get("nvme_smart_health_information_log") or {}
    media_errors = _as_int(nvme.get("media_errors"))
    if media_errors is not None:
        result.update(
            kind="NVMe",
            total=media_errors,
            level="ok" if media_errors == 0 else ("err" if media_errors >= 16 else "warn"),
        )
    return result


def bad_blocks_text(drive: dict) -> str:
    data = bad_blocks(drive)
    if data["total"] is None:
        return "—"
    total = data["total"]
    if total == 0:
        return t("{0} 个", 0)
    suffix = t("异常") if data["level"] == "err" else t("警告")
    return t("{0} 个（{1}）", total, suffix)


def bad_blocks_color(drive: dict) -> str:
    level = bad_blocks(drive)["level"]
    if level in ("warn", "err"):
        return _color(level)
    return _color("ok") if level == "ok" else _color("muted")


def bad_block_lines(drive: dict) -> list[tuple[str, str, str]]:
    """返回 (项目, 数值, 级别)，用于健康信息页逐项展示。"""
    data = bad_blocks(drive)
    rows: list[tuple[str, str, str]] = []
    smart = drive.get("smart") or {}
    if data["kind"] == "ATA":
        for label, key in (
            ("已重映射扇区（Reallocated）", "reallocated"),
            ("待映射扇区（Pending）", "pending"),
            ("无法纠正扇区（Uncorrectable）", "uncorrectable"),
        ):
            value = data.get(key)
            if value is None:
                continue
            rows.append((t(label), t("{0} 个", value), "warn" if value else "ok"))
    elif data["kind"] == "SAS":
        grown = data.get("grown")
        if grown is not None:
            rows.append(
                (
                    t("增长缺陷列表（Grown defects）"),
                    t("{0} 个", grown),
                    "warn" if grown else "ok",
                )
            )
        read_entry = (smart.get("scsi") or {}).get("read") or {}
        write_entry = (smart.get("scsi") or {}).get("write") or {}
        uncorrected = sum(
            _as_int(entry.get("uncorrected")) or 0
            for entry in (read_entry, write_entry)
        )
        rows.append(
            (
                t("未纠正的读/写错误"),
                t("{0} 次", uncorrected),
                "warn" if uncorrected else "ok",
            )
        )
    elif data["kind"] == "NVMe":
        rows.append(
            (
                t("介质错误（Media errors）"),
                t("{0} 个", data["total"]),
                "warn" if data["total"] else "ok",
            )
        )
    return rows


def attribute_status(attr: dict, drive: dict | None = None) -> tuple[str, str]:
    """判定单个 SMART 属性是否需要标红，返回 (级别, 原因)。"""
    try:
        attr_id = int(attr.get("id") or 0)
    except (TypeError, ValueError):
        return "ok", ""
    when_failed = (attr.get("when_failed") or "").strip()
    value = _as_int(attr.get("value"))
    thresh = _as_int(attr.get("thresh"))
    raw = _raw_value(attr)
    prefailure = bool(attr.get("prefailure"))
    label, unit = _ATTR_INFO.get(attr_id, (attr.get("name") or "", ""))

    if when_failed:
        return "err", t("SMART 已标记为失败（{0}）", when_failed)

    # 关键坏块指标：非 0 即标红，并给出具体数量
    if attr_id in BAD_BLOCK_ATTRS and raw:
        if attr_id == 187:
            return "warn", t("报告了 {0} 次不可纠正错误", raw)
        return ("warn", t("{0} {1} {2}", t(label), raw, t(unit)).strip())

    # 温度
    if attr_id in (190, 194) and raw is not None:
        if raw >= TEMP_ERR:
            return "err", t("温度 {0} °C，已超过 {1} °C", raw, TEMP_ERR)
        if raw >= TEMP_WARN:
            return "warn", t("温度 {0} °C，偏高", raw)

    # SSD 剩余寿命
    if attr_id in (202, 231) and value is not None:
        if value <= 5:
            return "err", t("剩余寿命仅 {0}%", value)
        if value <= 15:
            return "warn", t("剩余寿命 {0}%，建议关注", value)

    # 接口 CRC 错误
    if attr_id == 199 and raw:
        return "warn", t("UDMA CRC 错误 {0} 次（检查线缆/背板）", raw)

    # 通用 pre-failure 阈值判断
    if prefailure and value is not None and thresh is not None:
        if value <= thresh:
            return "err", t("当前值 {0} 已低于阈值 {1}", value, thresh)
        if thresh > 0 and value <= thresh + 5:
            return "warn", t("当前值 {0} 接近阈值 {1}", value, thresh)

    return "ok", ""


def health(drive: dict) -> tuple[str, str]:
    """返回 (状态键, 文案)。状态键：ok/warn/err/idle/unknown/loading。"""
    smart = drive.get("smart")
    if not smart:
        if drive.get("smart_error"):
            return "unknown", t("不可用")
        if power_mode(drive) in ("standby", "sleep"):
            return "idle", t("待机")
        return "loading", t("查询中")
    if smart.get("standby") or power_mode(drive) in ("standby", "sleep"):
        return "idle", t("待机")
    if not smart.get("available", True):
        return "unknown", t("不可用")
    if smart.get("passed") is False:
        return "err", t("异常")

    blocks = bad_blocks(drive)
    attrs = _attr_table(smart)
    crc = _raw_value(attrs.get(199)) or 0
    temp = smart.get("temperature_c") or 0
    if blocks["level"] == "err":
        return "err", t("坏块异常（{0} 个）", blocks["total"])
    if blocks["level"] == "warn":
        return "warn", t("有坏块（{0} 个）", blocks["total"])
    if crc:
        return "warn", t("接口错误")
    if temp and int(temp) >= TEMP_WARN:
        return "warn", t("高温")
    for attr in smart.get("attributes") or []:
        if attribute_status(attr, drive)[0] != "ok":
            return "warn", t("指标异常")
    if smart.get("passed") is True:
        return "ok", t("正常")
    return "unknown", t("未知")


def health_color(drive: dict) -> str:
    return _color(health(drive)[0])


def dot_color(drive: dict) -> str:
    state = health(drive)[0]
    if state == "loading":
        return _color("muted")
    return _color(state)


def health_counts(drives: list[dict]) -> dict[str, int]:
    counts = {"ok": 0, "warn": 0, "err": 0, "idle": 0, "unknown": 0, "loading": 0}
    for drive in drives:
        key = health(drive)[0]
        counts[key] = counts.get(key, 0) + 1
    return counts


def drives_summary(drives: list[dict]) -> tuple[str, str]:
    """HBA 卡片右侧的总体状态 (文案, pill 类别)。"""
    if not drives:
        return t("等待硬盘"), "pill-idle"
    counts = health_counts(drives)
    if counts["err"]:
        return t("存在异常"), "pill-err"
    if counts["warn"]:
        return t("需要关注"), "pill-warn"
    if counts["ok"] == len(drives):
        return t("运行正常"), "pill-ok"
    if counts["loading"] and not (counts["ok"] or counts["warn"]):
        return t("读取中"), "pill-idle"
    if counts["idle"] > 0 and not (counts["ok"] or counts["warn"]):
        return t("硬盘待机"), "pill-idle"
    return t("运行正常"), "pill-ok"


def rotation_text(drive: dict) -> str:
    if drive.get("media") == "SSD":
        return "SSD"
    smart = drive.get("smart") or {}
    rpm = smart.get("rotation_rate")
    if rpm:
        return t("{0} RPM", int(rpm))
    return "HDD" if drive.get("rotational") else "SSD"


def attribute_rows(drive: dict) -> list[list]:
    """属性表行：ID、名称、当前、最差、阈值、原始值、状态、颜色、提示。"""
    smart = drive.get("smart") or {}
    rows = []
    for attr in smart.get("attributes") or []:
        level, reason = attribute_status(attr, drive)
        status_text = {"ok": "", "warn": t("警告"), "err": t("异常")}.get(level, "")
        color = _color(level) if level != "ok" else _color("muted")
        raw_text = attr.get("raw_string") or str(attr.get("raw_value") or "")
        tooltip = (
            t("ID {0} · {1}", attr.get("id"), attr.get("name") or "")
            + "\n"
            + t(
                "当前 {0} / 最差 {1} / 阈值 {2}",
                attr.get("value"),
                attr.get("worst"),
                attr.get("thresh"),
            )
            + "\n"
            + t("原始值：{0}", raw_text)
        )
        if reason:
            tooltip += "\n" + t("⚠ {0}", reason)
        rows.append(
            [
                str(attr.get("id") or ""),
                attr.get("name") or "",
                "" if attr.get("value") is None else str(attr.get("value")),
                "" if attr.get("worst") is None else str(attr.get("worst")),
                "" if attr.get("thresh") is None else str(attr.get("thresh")),
                raw_text,
                status_text,
                color,
                tooltip,
            ]
        )
    return rows


def scsi_rows(drive: dict) -> list[tuple[str, str]]:
    """兼容旧接口：只返回 (项目, 数值)。"""
    return [
        (label, value) for label, value, _level, _reason in scsi_rows_colored(drive)
    ]


def scsi_rows_colored(drive: dict) -> list[tuple[str, str, str, str]]:
    """SCSI 错误计数器，返回 (项目, 数值, 级别, 说明)。"""
    smart = drive.get("smart") or {}
    scsi = smart.get("scsi") or {}
    rows: list[tuple[str, str, str, str]] = []
    directions = {"read": t("读取"), "write": t("写入"), "verify": t("校验")}
    for key, label in directions.items():
        entry = scsi.get(key) or {}
        if not entry:
            continue
        corrected = _as_int(entry.get("corrected")) or 0
        uncorrected = _as_int(entry.get("uncorrected")) or 0
        level = "warn" if (uncorrected or corrected) else "ok"
        reason = ""
        if uncorrected:
            reason = t("{0} 次未纠正错误", uncorrected)
        elif corrected:
            reason = t("{0} 次已纠正错误", corrected)
        rows.append(
            (
                t("{0}：已纠正 / 未纠正", label),
                t("{0} / {1}", corrected, uncorrected),
                level,
                reason,
            )
        )
        if entry.get("gigabytes"):
            rows.append(
                (
                    t("{0}：累计处理量", label),
                    t("{0} GB", entry.get("gigabytes")),
                    "ok",
                    "",
                )
            )
    grown = _as_int(scsi.get("grown_defects"))
    if grown is not None:
        rows.append(
            (
                t("增长缺陷列表 (grown defects)"),
                str(grown),
                "warn" if grown else "ok",
                t("{0} 个坏块", grown) if grown else "",
            )
        )
    cycles = _as_int(scsi.get("start_stop_cycles"))
    if cycles is not None:
        rows.append((t("启停次数"), str(cycles), "ok", ""))
    return rows


def smart_messages(drive: dict) -> list[str]:
    smart = drive.get("smart") or {}
    messages = []
    for message in smart.get("messages") or []:
        text = (message.get("string") or "").strip()
        if text:
            messages.append(text)
    if smart.get("error") and smart["error"] not in messages:
        messages.insert(0, smart["error"])
    return messages
