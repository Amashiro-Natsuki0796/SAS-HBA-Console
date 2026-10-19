"""硬盘启停与安全热拔出。

所有命令都以参数数组执行，绝不经过 shell；设备名必须匹配 /dev/sdX。
上层（provider/helper）负责重新枚举并确认设备仍属于 HBA。
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Callable, Sequence

from ..i18n import t
from ..util import find_tool, listdir, read_text, run_cmd, strip_hex, tail_lines
from .smart import query_power_state

DEVICE_RE = re.compile(r"^/dev/sd[a-z]{1,4}$")
DELETE_WAIT_SECONDS = 8.0

# 停转确认：7.2K 机械盘完整转停通常需要 5~15 秒。
STANDBY_WAIT_SECONDS = 30.0
STANDBY_POLL_SECONDS = 1.0


class ControlError(Exception):
    """可安全展示给用户的控制失败原因。"""


Runner = Callable[..., object]  # 兼容 util.run_cmd，测试时可注入


def validate_device(dev: str) -> str:
    if not isinstance(dev, str) or not DEVICE_RE.match(dev):
        raise ControlError(t("非法的设备路径：{0!r}", dev))
    name = dev[5:]
    if "/" in name or name in (".", ".."):
        raise ControlError(t("非法的设备路径"))
    return name


def ensure_idle(drive: dict, action: str) -> None:
    """停转/拔出前必须确认没有挂载、交换、LVM/RAID 占用。"""
    blockers = drive.get("blockers") or []
    if blockers:
        raise ControlError(
            t("该硬盘正在被使用，无法{0}：", action) + "\n• " + "\n• ".join(blockers)
        )


def _attempt_commands(
    candidates: Sequence[Sequence[str]],
    timeout: float,
    runner: Runner,
    tool_finder: Callable[[str], str | None] = find_tool,
) -> tuple[bool, list[dict]]:
    attempts: list[dict] = []
    available = 0
    for candidate in candidates:
        tool = tool_finder(candidate[0])
        if not tool:
            attempts.append(
                {
                    "cmd": " ".join(candidate),
                    "ok": False,
                    "skipped": True,
                    "error": t("未安装 {0}", candidate[0]),
                }
            )
            continue
        available += 1
        cmd = [tool] + list(candidate[1:])
        result = runner(cmd, timeout=timeout)
        attempts.append(
            {
                "cmd": " ".join(cmd),
                "ok": result.ok,
                "returncode": result.returncode,
                "stderr": tail_lines(result.stderr, 4),
                "error": result.error,
            }
        )
        if result.ok:
            return True, attempts
    if available == 0:
        raise ControlError(
            t("缺少控制工具，请安装 smartmontools / sdparm / sg3-utils")
        )
    return False, attempts


def sg_node_for(drive: dict, sys_root: str = "/") -> str | None:
    """优先返回 /dev/sgX：sdparm/sg_start 通过块设备下发停止指令时
    可能被块层立刻重新唤醒，SGI 通用节点更可靠。"""
    name = os.path.basename(drive.get("dev") or "")
    if not name:
        return None
    base = os.path.join(sys_root, "sys", "block", name, "device", "scsi_generic")
    try:
        entries = sorted(os.listdir(base))
    except OSError:
        return None
    for entry in entries:
        if entry.startswith("sg") and entry[2:].isdigit():
            # 优先真实 /dev 节点；测试/救援环境可回退到 <sys_root>/dev
            for candidate in (f"/dev/{entry}", os.path.join(sys_root, "dev", entry)):
                if os.path.exists(candidate):
                    return candidate
    return None


def _spin_down_commands(drive: dict, dev: str) -> list[list[str]]:
    protocol = (drive.get("protocol") or "").upper()
    smart_protocol = ((drive.get("smart") or {}).get("protocol") or "").upper()
    is_ata = protocol == "SATA" or smart_protocol == "ATA"
    scsi_dev = sg_node_for(drive) or dev
    if is_ata:
        return [
            ["smartctl", "-s", "standby,now", dev],
            ["smartctl", "-d", "sat", "-s", "standby,now", dev],
            ["sdparm", "--command=stop", scsi_dev],
        ]
    return [
        ["sdparm", "--command=stop", scsi_dev],
        ["sg_start", "--stop", scsi_dev],
        ["smartctl", "-s", "standby,now", dev],
    ]


def _spin_up_commands(drive: dict, dev: str) -> list[list[str]]:
    protocol = (drive.get("protocol") or "").upper()
    smart_protocol = ((drive.get("smart") or {}).get("protocol") or "").upper()
    is_ata = protocol == "SATA" or smart_protocol == "ATA"
    scsi_dev = sg_node_for(drive) or dev
    if is_ata:
        return [
            ["smartctl", "-s", "standby,off", dev],
            ["smartctl", "-d", "sat", "-s", "standby,off", dev],
            ["sdparm", "--command=start", scsi_dev],
        ]
    return [
        ["sdparm", "--command=start", scsi_dev],
        ["sg_start", "--start", scsi_dev],
        ["smartctl", "-s", "standby,off", dev],
    ]



# ---------------------------------------------------------------------------
# 自动省电（APM / EPC）与自动停转检测
# ---------------------------------------------------------------------------

# APM 电平含义（ATA 规范）：1..127 允许自动停转，128..254 只做磁头停放，
# 0 表示 APM 关闭。因此只有 1..127 才需要担心硬盘自行起转/停转。
APM_SPIN_DOWN_MIN = 1
APM_SPIN_DOWN_MAX = 127
APM_OFF = 0


def read_apm(dev: str, runner: Runner = run_cmd, tool_finder=find_tool) -> dict:
    """读取 ATA 硬盘的 APM 电平。

    返回 {"supported": bool, "level": int|None, "raw": str, "error": str}。
    level <= 127 表示允许硬盘自行停转（笔记本盘常见），这是"移除后又起转"
    类现象的根源：盘会按自身固件策略重新起转。
    """
    tool = tool_finder("smartctl")
    if not tool:
        return {"supported": False, "level": None, "raw": "", "error": t("未安装 smartctl")}
    result = runner([tool, "-g", "apm", "-j", dev], timeout=20)
    payload: dict = {}
    try:
        payload = json.loads(result.stdout) if result.stdout.strip() else {}
    except (json.JSONDecodeError, TypeError):
        payload = {}
    apm = payload.get("ata_apm") or {}
    if not apm or apm.get("enabled") is False and not apm.get("level"):
        # 老版本 smartctl 只给文本，尝试解析
        text = (result.stdout or "") + " " + (result.stderr or "")
        match = re.search(r"APM level is:\s*(\d+)", text)
        enabled = "APM feature is:" in text and "Unavailable" not in text
        return {
            "supported": enabled,
            "level": int(match.group(1)) if match else None,
            "raw": text.strip()[:200],
            "error": "" if enabled else t("该硬盘不支持或已禁用 APM"),
        }
    level = apm.get("level")
    try:
        level = int(level) if level is not None else None
    except (TypeError, ValueError):
        level = None
    return {
        "supported": True,
        "level": level,
        "enabled": bool(apm.get("enabled", level is not None)),
        "raw": json.dumps(apm, ensure_ascii=False),
        "error": "",
    }


def read_spin_down_count(drive: dict, sys_root: str = "/") -> int | None:
    """读取内核记录的自动停转次数（/sys/class/ata_device/devX.Y/spdn_cnt）。

    该计数只增不减，可用来确认硬盘是否在按 APM 策略自行停转。
    """
    name = os.path.basename(drive.get("dev") or "")
    if not name:
        return None
    # 通过 scsi_device 的真实路径找到对应的 ataN 端口，再定位 ata_device
    device_dir = os.path.join(sys_root, "sys", "block", name, "device")
    try:
        real = os.path.realpath(device_dir)
    except OSError:
        return None
    # 真实路径形如 .../ata1/host0/target0:0:0/0:0:0:0
    match = re.search(r"ata(\d+)", real)
    if not match:
        return None
    port = match.group(1)
    base = os.path.join(sys_root, "sys", "class", "ata_device")
    try:
        entries = sorted(os.listdir(base))
    except OSError:
        return None
    for entry in entries:
        if not entry.startswith(f"dev{port}."):
            continue
        value = read_text(os.path.join(base, entry, "spdn_cnt"))
        if value:
            try:
                return int(value)
            except ValueError:
                continue
    return None


def power_saving_risk(
    drive: dict,
    apm: dict | None = None,
    spdn_cnt: int | None = None,
) -> dict:
    """判断硬盘是否存在“自行起转/自行停转”的省电策略。

    返回 {"risky": bool, "reason": str, "level": str}。
    """
    protocol = (drive.get("protocol") or "").upper()
    smart_protocol = ((drive.get("smart") or {}).get("protocol") or "").upper()
    is_ata = protocol == "SATA" or smart_protocol == "ATA"
    if not is_ata:
        return {"risky": False, "reason": "", "level": "ok"}

    apm = apm or read_apm(drive.get("dev") or "")
    level = apm.get("level")
    reasons = []
    if level is not None and APM_SPIN_DOWN_MIN <= level <= APM_SPIN_DOWN_MAX:
        reasons.append(t("APM 电平 {0}，允许硬盘自行停转", level))
    if spdn_cnt:
        reasons.append(t("内核已记录 {0} 次自动停转", spdn_cnt))
    if not reasons:
        return {"risky": False, "reason": "", "level": "ok"}
    return {
        "risky": True,
        "reason": "；".join(reasons)
        + "。"
        + t("这类省电策略会让硬盘在移除前后自行起转，建议先关闭 APM 再操作"),
        "level": "warn",
    }


def disable_apm(
    dev: str,
    runner: Runner = run_cmd,
    tool_finder=find_tool,
) -> dict:
    """关闭 APM，阻止硬盘自行停转/起转。"""
    tool = tool_finder("smartctl")
    if not tool:
        raise ControlError(t("未安装 smartctl，无法关闭 APM"))
    result = runner([tool, "-g", "apm", "-s", "apm,off", dev], timeout=30)
    if not result.ok:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        raise ControlError(
            t("关闭 APM 失败：{0}", detail[-1] if detail else t("命令失败"))
        )
    return {"ok": True, "message": t("已关闭 APM，硬盘不会自行停转")}


def spin_down(
    drive: dict,
    runner: Runner = run_cmd,
    settle: float = 1.0,
    state_probe: Callable[[str], str] | None = None,
    tool_finder: Callable[[str], str | None] = find_tool,
    verify: bool = True,
) -> dict:
    dev = drive.get("dev") or ""
    validate_device(dev)
    ensure_idle(drive, t("停转"))

    ok, attempts = _attempt_commands(
        _spin_down_commands(drive, dev),
        timeout=30,
        runner=runner,
        tool_finder=tool_finder,
    )
    if not ok:
        detail = attempts[-1].get("stderr") or attempts[-1].get("error") or t("命令失败")
        raise ControlError(t("停转命令执行失败：{0}", detail))

    if not verify:
        return {
            "ok": True,
            "message": t("已发送停转指令"),
            "warning": "",
            "power_mode": "unknown",
            "attempts": attempts,
        }

    if settle:
        time.sleep(settle)
    probe = state_probe or query_power_state
    state = probe(dev)
    if state in ("active",):
        warning = t("命令已被接受，但硬盘仍在运行；该盘可能忽略停转指令")
    elif state == "unknown":
        warning = t("命令已被接受，但无法确认当前电源状态")
    else:
        warning = ""
    return {
        "ok": True,
        "message": t("已发送停转指令") + (t("（状态待确认）") if warning else t("，硬盘已停转")),
        "warning": warning,
        "power_mode": state,
        "attempts": attempts,
    }


def spin_up(
    drive: dict,
    runner: Runner = run_cmd,
    wait_seconds: float = 25.0,
    state_probe: Callable[[str], str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    tool_finder: Callable[[str], str | None] = find_tool,
) -> dict:
    dev = drive.get("dev") or ""
    validate_device(dev)
    ok, attempts = _attempt_commands(
        _spin_up_commands(drive, dev),
        timeout=45,
        runner=runner,
        tool_finder=tool_finder,
    )
    if not ok:
        detail = attempts[-1].get("stderr") or attempts[-1].get("error") or t("命令失败")
        raise ControlError(t("启动命令执行失败：{0}", detail))

    probe = state_probe or query_power_state
    state = probe(dev)
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while state != "active" and time.monotonic() < deadline:
        sleep(1.5)
        state = probe(dev)
    if state == "active":
        return {
            "ok": True,
            "message": t("硬盘已启动并恢复响应"),
            "warning": "",
            "power_mode": state,
            "attempts": attempts,
        }
    if state in ("standby", "sleep"):
        raise ControlError(t("启动命令已执行，但硬盘仍处于停转/休眠状态"))
    return {
        "ok": True,
        "message": t("启动指令已发送"),
        "warning": t("暂时无法确认电源状态，请稍后刷新 SMART"),
        "power_mode": state,
        "attempts": attempts,
    }


def wait_for_standby(
    dev: str,
    timeout: float = STANDBY_WAIT_SECONDS,
    probe: Callable[[str], str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    poll: float = STANDBY_POLL_SECONDS,
) -> str:
    """轮询确认硬盘真正进入 standby/sleep。

    ``probe`` 必须是不会唤醒硬盘的查询（默认 smartctl -n standby）。
    返回最终观察到的电源状态。
    """
    probe = probe or query_power_state
    deadline = time.monotonic() + max(0.0, timeout)
    state = probe(dev)
    while state not in ("standby", "sleep", "missing") and time.monotonic() < deadline:
        sleep(poll)
        state = probe(dev)
    return state



# ---------------------------------------------------------------------------
# SAS PHY 控制：断开链路才能让 SATA 盘真正断电
# ---------------------------------------------------------------------------


def _end_device_paths(device_dir: str) -> list[tuple[str, str]]:
    """从盘的 sysfs 路径中找出 ``end_device-<h>:<p>`` 祖先目录。

    真机路径::

        /sys/devices/.../host0/port-0:14/end_device-0:14/target0:0:14/0:0:14:0
                                              ^^^^^^^^^^^^^^^^^^ 这里就是 rphy 设备

    返回 [(end_device 目录, "h:p"), ...]，可能多个（字面路径与 realpath）。
    """
    found: list[tuple[str, str]] = []
    for path in (
        os.path.abspath(device_dir),
        os.path.abspath(os.path.realpath(device_dir)),
    ):
        match = re.search(r"(.*?/end_device-(\d+:\d+))(/|$)", path)
        if match:
            entry = (match.group(1), match.group(2))
            if entry not in found:
                found.append(entry)
    return found


def find_sas_phy(drive: dict, sys_root: str = "/") -> str | None:
    """为硬盘找到对应的 SAS PHY（用于禁用链路）。

    关键事实（容易搞错，已按内核源码确认）：

      * ``phy_identifier`` 由 ``rphy_attrs`` 注册（scsi_transport_sas.c 的
        ``SETUP_RPORT_ATTRIBUTE(rphy_phy_identifier)``），属于 **rphy 设备**，
        其 class 是 ``sas_device``；``sas_end_device`` 类的属性只有
        ``end_dev_*``（ready_led_meaning / tlr_* 等），**没有 phy_identifier**；
      * scsi_device（``/sys/block/sdX/device``）上也没有该属性；
      * rphy 设备本身就是路径中的 ``.../port-0:14/end_device-0:14`` 目录。

    因此正确做法是：从盘路径取出 ``end_device-<h>:<p>`` 目录，直接读该目录的
    ``phy_identifier``，再定位 ``/sys/class/sas_phy/phy-<h>:<id>``。
    """
    name = os.path.basename(drive.get("dev") or "")
    if not name:
        return None
    device_dir = os.path.join(sys_root, "sys", "block", name, "device")
    if not os.path.exists(device_dir):
        return None

    phys = _end_device_paths(device_dir)
    if not phys:
        return None

    phy_base = os.path.join(sys_root, "sys", "class", "sas_phy")

    for end_dir, end_id in phys:
        host = end_id.split(":")[0]
        # 依次尝试：rphy 设备目录本体 → sas_device 类 → sas_end_device 类
        candidates = [
            os.path.join(end_dir, "phy_identifier"),
            os.path.join(sys_root, "sys", "class", "sas_device",
                         f"end_device-{end_id}", "phy_identifier"),
            os.path.join(sys_root, "sys", "class", "sas_end_device",
                         f"end_device-{end_id}", "phy_identifier"),
        ]
        phy_id = None
        for candidate in candidates:
            value = strip_hex(read_text(candidate))
            if value is not None and value.isdigit():
                phy_id = value
                break
        if phy_id is not None:
            path = os.path.join(phy_base, f"phy-{host}:{phy_id}")
            if os.path.exists(os.path.join(path, "enable")):
                return path

        # 兜底：port_identifier（宽端口/扩展器）
        for candidate in (
            os.path.join(end_dir, "port_identifier"),
            os.path.join(sys_root, "sys", "class", "sas_device",
                         f"end_device-{end_id}", "port_identifier"),
        ):
            value = strip_hex(read_text(candidate))
            if value is not None and value.isdigit():
                path = os.path.join(phy_base, f"phy-{host}:{value}")
                if os.path.exists(os.path.join(path, "enable")):
                    return path

        # 最后兜底：end_device 的端口号本身
        path = os.path.join(phy_base, f"phy-{host}:{end_id.split(':')[1]}")
        if os.path.exists(os.path.join(path, "enable")):
            return path
    return None


def phy_enabled(phy_path: str, sys_root: str = "/") -> bool | None:
    value = read_text(os.path.join(phy_path, "enable"))
    if not value:
        return None
    return value.strip() in ("1", "Y", "y")


def set_phy_enabled(
    phy_path: str,
    enable: bool,
    sys_root: str = "/",
) -> dict:
    """启用/禁用 SAS PHY。

    禁用会下发 SMP_PHY_CONTROL_DISABLE，物理断开该端口链路，
    SATA 盘随之断电并保持停转，也不会再被控制器重新发现。
    """
    target = os.path.join(phy_path, "enable")
    if not os.path.exists(target):
        raise ControlError(t("该 PHY 不支持链路控制（缺少 {0}）", target))
    try:
        with open(target, "w", encoding="ascii") as fh:
            fh.write("1" if enable else "0")
    except OSError as exc:
        raise ControlError(
            t("设置 PHY 链路状态失败：{0}", exc.strerror or exc)
        ) from exc
    return {
        "ok": True,
        "phy": phy_path,
        "enabled": enable,
        "message": t("已断开端口链路") if not enable else t("已恢复端口链路"),
    }


def disable_phy_for_drive(
    drive: dict, sys_root: str = "/"
) -> dict | None:
    """移除设备后禁用其 SAS PHY，避免控制器重新发现并让盘起转。"""
    phy = find_sas_phy(drive, sys_root)
    if not phy:
        return None
    try:
        return set_phy_enabled(phy, False, sys_root)
    except ControlError:
        return None



DISABLED_PHY_LOG = os.path.join(
    os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"),
    "sas-hba-console",
    "disabled-phy.txt",
)


def remember_disabled_phy(phy_path: str, label: str = "") -> None:
    """记录被禁用的 PHY，便于之后一键恢复。

    格式：每行 ``<phy 路径>\t<说明>``；说明里保存插槽/型号，
    便于在界面上告诉用户"哪个位置被断开了"。
    """
    try:
        os.makedirs(os.path.dirname(DISABLED_PHY_LOG), exist_ok=True)
        existing: dict[str, str] = {}
        if os.path.exists(DISABLED_PHY_LOG):
            with open(DISABLED_PHY_LOG, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split("\t", 1)
                    existing[parts[0]] = parts[1] if len(parts) > 1 else ""
        existing[phy_path] = label or existing.get(phy_path, "")
        entries = [
            {"phy": path, "label": text, "disabled_at": int(time.time())}
            for path, text in sorted(existing.items())
        ]
        _persist_entries(entries)
    except OSError:
        pass


def list_disabled_phys() -> list[str]:
    return [item["phy"] for item in list_disabled_phy_entries()]


def list_disabled_phy_entries() -> list[dict]:
    """返回被禁用 PHY 的详细信息：[{phy, label}]。"""
    entries: list[dict] = []
    try:
        with open(DISABLED_PHY_LOG, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                parts = line.split("\t")
                entries.append(
                    {
                        "phy": parts[0],
                        "label": parts[1] if len(parts) > 1 else "",
                        "disabled_at": int(parts[2])
                        if len(parts) > 2 and parts[2].strip().isdigit()
                        else 0,
                    }
                )
    except OSError:
        return []
    return entries



def _persist_entries(entries: list[dict]) -> None:
    try:
        os.makedirs(os.path.dirname(DISABLED_PHY_LOG), exist_ok=True)
        with open(DISABLED_PHY_LOG, "w", encoding="utf-8") as fh:
            for entry in entries:
                fh.write(
                    f"{entry['phy']}\t{entry.get('label', '')}"
                    f"\t{entry.get('disabled_at', '')}\n"
                )
    except OSError:
        pass

def enable_all_disabled_phys(sys_root: str = "/") -> dict:
    """重新启用此前被禁用的 PHY，让硬盘重新上线。"""
    restored = []
    failed = []
    stale = []
    entries = list_disabled_phy_entries()
    for entry in entries:
        phy = entry["phy"]
        if not os.path.exists(os.path.join(phy, "enable")):
            # 记录里的 PHY 已不存在（换卡/重启），清掉即可，不算失败
            stale.append(phy)
            continue
        try:
            set_phy_enabled(phy, True, sys_root)
            restored.append(phy)
        except ControlError:
            failed.append(phy)
    if restored or stale:
        removed = set(restored) | set(stale)
        _persist_entries([e for e in entries if e["phy"] not in removed])
    return {
        "ok": bool(restored) or not failed,
        "restored": restored,
        "failed": failed,
        "message": t("已恢复 {0} 个端口链路，硬盘将重新上线", len(restored))
        if restored
        else t("没有需要恢复的端口链路"),
    }


def diagnose_phy(drive: dict, sys_root: str = "/") -> str:
    """PHY 未匹配时，返回可操作的诊断信息（如实报告读到了什么）。"""
    from .. import __version__

    name = os.path.basename(drive.get("dev") or "")
    device_dir = os.path.join(sys_root, "sys", "block", name, "device")
    phy_base = os.path.join(sys_root, "sys", "class", "sas_phy")
    phys = listdir(phy_base)
    end_devs = listdir(os.path.join(sys_root, "sys", "class", "sas_end_device"))
    sas_devs = listdir(os.path.join(sys_root, "sys", "class", "sas_device"))

    lines = [
        t("未找到可控制的 SAS PHY，无法断开链路；控制器可能随即重新探测该盘并使其起转。"),
        f"版本 {__version__}",
        t("诊断：sas_phy {0} 个；sas_end_device {1} 个；sas_device {2} 个。",
          len(phys), len(end_devs), len(sas_devs)),
    ]

    if not phys:
        lines.append(
            t("系统中没有 /sys/class/sas_phy，说明该盘可能不是经由 SAS 控制器连接"
              "（例如直接接在主板 SATA 口），此时不存在可断开的链路。")
        )
        return "\n".join(lines)

    # 该盘的 end_device 路径与读到的 phy_identifier（排查的关键）
    if not os.path.exists(device_dir):
        lines.append(t("设备节点已不存在：{0}", device_dir))
        return "\n".join(lines)

    end_paths = _end_device_paths(device_dir)
    if not end_paths:
        lines.append(
            t("该盘路径中没有 end_device-<h>:<p>：{0}", os.path.realpath(device_dir))
        )
    for end_dir, end_id in end_paths:
        lines.append(t("end_device 目录：{0}", end_dir))
        for candidate in (
            os.path.join(end_dir, "phy_identifier"),
            os.path.join(sys_root, "sys", "class", "sas_device",
                         f"end_device-{end_id}", "phy_identifier"),
            os.path.join(sys_root, "sys", "class", "sas_end_device",
                         f"end_device-{end_id}", "phy_identifier"),
        ):
            value = read_text(candidate)
            lines.append(
                f"  {candidate} = {value!r}" if value is not None
                else f"  {candidate} = (不存在)"
            )

    lines.append(t("可用 PHY：{0}", "、".join(phys[:12])))
    return "\n".join(lines)


def hot_remove(
    drive: dict,
    sys_root: str = "/",
    spin_first: bool = True,
    runner: Runner = run_cmd,
    wait_seconds: float = DELETE_WAIT_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    tool_finder: Callable[[str], str | None] = find_tool,
    state_probe: Callable[[str], str] | None = None,
    standby_timeout: float = STANDBY_WAIT_SECONDS,
    require_standby: bool = True,
    apm_probe: Callable[[str], dict] | None = None,
    disable_apm_first: bool = False,
    spdn_probe: Callable[[dict], int | None] | None = None,
    disconnect_phy: bool = True,
) -> dict:
    """安全移除 SCSI 设备，使其停转后可物理拔出。

    关于顺序（这是整个功能的关键，且与直觉相反）：

    1. 内核在 ``delete`` 时（``sd_remove`` -> ``sd_shutdown``）**必然**执行
       ``scsi_autopm_get_device()``（runtime-PM 唤醒）和 ``sd_sync_cache()``
       （SYNCHRONIZE CACHE）。所以 delete **之前**必须先完成两件事：
       刷缓存、以及让盘停转；否则 delete 会把盘唤醒。
    2. 但 ``delete`` 本身仍会发 SYNCHRONIZE CACHE。若此时链路还在，
       这条命令会**唤醒刚停转的盘**。
    3. SAS 控制器还会周期性地重新发现该端口，发 "Power-on or device reset"。

    因此正确顺序是::

        刷缓存 -> 发 STANDBY（盘停转）-> 断开 SAS PHY 链路 -> 写 delete

    先断链再 delete：链路已断，delete 时的 SYNCHRONIZE CACHE 送不到盘，
    不会把它唤醒；控制器也无法再发现它。这才是"停转后不再起转"的成因。
    """
    dev = drive.get("dev") or ""
    name = validate_device(dev)
    ensure_idle(drive, t("安全移除"))

    delete_path = os.path.join(sys_root, "sys", "block", name, "device", "delete")
    block_path = os.path.join(sys_root, "sys", "block", name)
    if not os.path.exists(block_path):
        raise ControlError(t("找不到块设备 /sys/block/{0}", name))
    if not os.path.exists(delete_path):
        raise ControlError(
            t("该设备不支持 SCSI 热移除（缺少 {0}）", delete_path)
        )

    # sg 节点与 PHY 路径都必须在设备消失前取好
    sg_dev = sg_node_for(drive, sys_root)
    target = sg_dev or dev
    phy_path = find_sas_phy(drive, sys_root) if disconnect_phy else None

    probe = state_probe or query_power_state
    apm_note = ""
    stop_warning = ""
    stop_message = ""
    standby_state = ""
    attempts: list[dict] = []
    phy_result = None
    phy_note = ""

    # ---- 1) 刷缓存：此时链路还在，属正常 IO，未涉及停转 ----
    try:
        os.sync()
    except OSError:
        pass
    blockdev = tool_finder("blockdev")
    if blockdev:
        flush_result = runner([blockdev, "--flushbufs", dev], timeout=20)
        if not flush_result.ok:
            raise ControlError(
                t("刷新设备缓存失败：{0}", tail_lines(flush_result.stderr, 3))
            )

    # ---- 2) APM 处理与停转（链路仍在，命令可以送达） ----
    if spin_first:
        read_apm_fn = apm_probe or read_apm
        read_spdn_fn = spdn_probe or read_spin_down_count
        try:
            apm = read_apm_fn(target)
        except Exception:
            apm = {"level": None, "supported": False}
        try:
            spdn = read_spdn_fn(drive)
        except Exception:
            spdn = None
        risk = power_saving_risk(drive, apm=apm, spdn_cnt=spdn)
        if risk.get("risky"):
            if disable_apm_first:
                try:
                    disable_apm(target, runner=runner, tool_finder=tool_finder)
                    apm_note = t("已关闭 APM，硬盘不会自行重新起转")
                except ControlError as exc:
                    apm_note = t("关闭 APM 失败：{0}", exc) + "\n" + (
                        risk.get("reason") or ""
                    )
            else:
                apm_note = risk.get("reason") or ""

        ok, attempts = _attempt_commands(
            _spin_down_commands(drive, target),
            timeout=20,
            runner=runner,
            tool_finder=tool_finder,
        )
        if not ok:
            stop_warning = t(
                "停转指令下发失败（将继续移除）：{0}",
                attempts[-1].get("stderr")
                or attempts[-1].get("error")
                or t("命令失败"),
            )
        else:
            stop_message = t("已停转")
            # 等盘真正停住再断链，避免链路断开时盘还在转
            standby_state = wait_for_standby(
                target,
                timeout=max(1.0, float(standby_timeout)),
                probe=probe,
                sleep=sleep,
            )
            if standby_state not in ("standby", "sleep", "missing"):
                stop_warning = t(
                    "硬盘未在限定时间内确认停转（当前：{0}）；将继续移除", standby_state
                )
                stop_message = ""

    # ---- 3) 先断开 PHY 链路：此后内核的 SYNCHRONIZE CACHE 送不到盘 ----
    if disconnect_phy:
        try:
            if phy_path:
                phy_result = set_phy_enabled(phy_path, False, sys_root)
                remember_disabled_phy(
                    phy_path,
                    " / ".join(
                        item
                        for item in (
                            drive.get("position") or "",
                            drive.get("model") or "",
                            f"phy {os.path.basename(phy_path)}",
                        )
                        if item
                    ),
                )
                phy_note = t("已断开端口链路")
            else:
                phy_note = diagnose_phy(drive, sys_root)
        except ControlError as exc:
            phy_note = t("断开端口链路失败：{0}", exc)

    # ---- 4) 移除设备 ----
    # 断链本身可能已让内核移除整个端口（port_remove -> sd_remove），
    # 此时 delete 节点已消失，属正常结果而非失败。
    vanished = not os.path.exists(block_path)
    if not vanished:
        try:
            with open(delete_path, "w", encoding="ascii") as fh:
                fh.write("1\n")
        except FileNotFoundError:
            # 与内核的端口移除竞争：设备已消失，视为成功
            vanished = True
        except OSError as exc:
            raise ControlError(
                t("写入 SCSI delete 失败：{0}", exc.strerror or exc)
            ) from exc

    deadline = time.monotonic() + max(1.0, wait_seconds)
    while not vanished and time.monotonic() < deadline:
        if not os.path.exists(block_path):
            vanished = True
            break
        sleep(0.25)
    if not vanished:
        raise ControlError(
            t("已写入 SCSI delete，但设备节点仍然存在；请查看 dmesg 后再试")
        )

    message = t("{0} 已从系统安全移除，现在可以拔出硬盘", dev)
    if stop_message:
        message = t("{0} 已停转并安全移除，现在可以拔出硬盘", dev)

    warnings = [
        item
        for item in (stop_warning, apm_note, phy_note if not phy_result else "")
        if item
    ]

    return {
        "ok": True,
        "message": message,
        "warning": "\n".join(warnings),
        "standby_confirmed": bool(stop_message) or standby_state in ("standby", "sleep"),
        "standby_state": standby_state,
        "stops_before_removal": True,
        "phy_disabled": bool(phy_result and phy_result.get("ok")),
        "phy_path": phy_path or "",
        "phy_note": phy_note,
        "phy_diagnosis": "" if phy_result else diagnose_phy(drive, sys_root),
        "flushed": True,
        "spin_first": spin_first,
        "attempts": attempts,
    }

