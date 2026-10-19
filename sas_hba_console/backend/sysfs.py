"""通过 sysfs 枚举 SAS / SCSI / RAID 控制器与直连硬盘。

不绑定具体厂商或型号：任何 PCI 存储控制器（class 0100/0104/0107/0180）
都会被发现，型号优先取 board_name，其次取系统 pci.ids。
全部函数都接受 ``root`` 参数（默认 "/"），方便用合成 sysfs 目录做单元测试。
"""

from __future__ import annotations

import os
import re
import time
from typing import Iterable

from ..i18n import t
from . import pci_db
from ..util import (
    exists,
    find_tool,
    human_size,
    listdir,
    pcie_link,
    read_int,
    read_text,
    run_cmd,
    strip_hex,
    unique,
)

# 系统关键挂载点：这些位置所在的盘禁止停转/热拔出。
CRITICAL_MOUNTPOINTS = {"/", "/boot", "/boot/efi", "/usr", "/var", "/etc"}


def _sys(root: str, *parts: str) -> str:
    return os.path.join(root, "sys", *parts)


def _proc(root: str, *parts: str) -> str:
    return os.path.join(root, "proc", *parts)


def _unescape_mount(value: str) -> str:
    # /proc/mounts 使用八进制转义空格、制表符等
    out = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 3 < len(value) and value[i + 1 : i + 4].isdigit():
            try:
                out.append(chr(int(value[i + 1 : i + 4], 8)))
                i += 4
                continue
            except ValueError:
                pass
        out.append(ch)
        i += 1
    return "".join(out)


def read_mount_table(root: str = "/") -> list[dict]:
    """解析 /proc/mounts。"""
    entries: list[dict] = []
    path = _proc(root, "mounts")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                fields = line.split()
                if len(fields) < 3:
                    continue
                entries.append(
                    {
                        "source": _unescape_mount(fields[0]),
                        "mountpoint": _unescape_mount(fields[1]),
                        "fstype": fields[2],
                        "options": fields[3] if len(fields) > 3 else "",
                    }
                )
    except OSError:
        return []
    return entries


def read_swap_table(root: str = "/") -> list[dict]:
    entries: list[dict] = []
    path = _proc(root, "swaps")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            next(fh, None)  # 表头
            for line in fh:
                fields = line.split()
                if len(fields) >= 2:
                    entries.append(
                        {
                            "source": _unescape_mount(fields[0]),
                            "type": fields[1],
                            "size_kb": fields[2] if len(fields) > 2 else "",
                        }
                    )
    except OSError:
        return []
    return entries


def _block_dir(root: str, name: str) -> str:
    # /sys/block 在所有支持的内核上都存在；/sys/class/block 在部分精简容器中可能缺失。
    return _sys(root, "block", name)


def block_dependents(root: str, name: str) -> set[str]:
    """返回该磁盘及其分区、dm/md 上层设备的短名集合。"""
    result: set[str] = {name}
    queue = [name]
    while queue:
        current = queue.pop()
        base = _block_dir(root, current)
        # 分区：/sys/block/sdb/sdb1
        for child in listdir(base):
            child_path = os.path.join(base, child)
            if (
                child.startswith(current)
                and child != current
                and exists(os.path.join(child_path, "partition"))
                and child not in result
            ):
                result.add(child)
                queue.append(child)
        # 上层：LVM / dm-crypt / mdraid
        for holder in listdir(os.path.join(base, "holders")):
            if holder not in result:
                result.add(holder)
                queue.append(holder)
    return result


def _source_basename(source: str) -> str:
    """把 /dev/mapper/vg-lv 之类的挂载源解析成内核块设备短名。"""
    try:
        resolved = os.path.realpath(source)
    except OSError:
        resolved = source
    base = os.path.basename(resolved)
    if base and base != os.path.basename(source):
        return base
    return os.path.basename(source)


def usage_for_disk(root: str, name: str) -> dict:
    """计算磁盘的挂载、交换、LVM/RAID 占用情况。"""
    dependents = block_dependents(root, name)
    mounts = read_mount_table(root)
    swaps = read_swap_table(root)

    used_mounts = []
    for entry in mounts:
        if _source_basename(entry["source"]) in dependents:
            used_mounts.append(entry)

    used_swaps = [
        swap for swap in swaps if _source_basename(swap["source"]) in dependents
    ]

    holders: list[str] = []
    for dep in sorted(dependents):
        holders.extend(listdir(os.path.join(_block_dir(root, dep), "holders")))
    holders = unique(holders)

    mountpoints = unique(entry["mountpoint"] for entry in used_mounts)
    critical = [
        mp for mp in mountpoints if mp in CRITICAL_MOUNTPOINTS or mp == "/"
    ]

    blockers: list[str] = []
    if critical:
        blockers.append("系统关键文件系统正在使用：" + "、".join(critical))
    other_mounts = [mp for mp in mountpoints if mp not in critical]
    if other_mounts:
        blockers.append("已挂载：" + "、".join(other_mounts))
    if used_swaps:
        blockers.append("被用作交换分区")
    if holders:
        blockers.append("被 LVM / RAID / 加密层占用：" + "、".join(holders))

    return {
        "mountpoints": mountpoints,
        "swaps": [swap["source"] for swap in used_swaps],
        "holders": holders,
        "is_system": bool(critical),
        "blockers": blockers,
        "removable": not blockers,
    }


def _read_host_attr(root: str, host: str, attr: str) -> str:
    return read_text(os.path.join(_sys(root, "class", "scsi_host", host), attr))


BDF_RE = re.compile(r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]")


def _pci_class(code: str) -> str:
    text = (code or "").strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    text = text.zfill(6)
    return "0x" + text[:4]


def _pci_short(pci_address: str) -> str:
    parts = pci_address.split(":")
    if len(parts) >= 3:
        return ":".join(parts[-2:])
    return pci_address


def _driver_of(path: str) -> str:
    link = os.path.join(path, "driver")
    if not exists(link):
        return ""
    try:
        return os.path.basename(os.path.realpath(link))
    except OSError:
        return ""


def _module_version(root: str, driver: str) -> str:
    if not driver:
        return ""
    for parts in (("version",), ("parameters", "version")):
        value = read_text(os.path.join(_sys(root, "module", driver), *parts))
        if value:
            return value
    return ""


def _pci_dir(root: str, pci_address: str) -> str:
    return os.path.join(_sys(root, "bus", "pci", "devices"), pci_address)


def scsi_hosts(root: str = "/") -> list[dict]:
    """枚举所有 SCSI 主机，并关联到背后的 PCI 设备。

    只返回存储控制器（PCI class 0100/0104/0107/0180）或驱动已知的主机，
    从而排除 USB 读卡器、虚拟磁带等无关设备。
    """
    base = _sys(root, "class", "scsi_host")
    hosts: list[dict] = []
    for host in listdir(base):
        host_dir = os.path.join(base, host)
        proc_name = read_text(os.path.join(host_dir, "proc_name"))
        device_link = os.path.join(host_dir, "device")
        try:
            real = os.path.realpath(device_link)
        except OSError:
            real = ""
        matches = BDF_RE.findall(real)
        pci_address = matches[-1] if matches else ""
        pci_path = _pci_dir(root, pci_address) if pci_address else ""
        driver = _driver_of(pci_path) if pci_path else ""
        if not driver and pci_path:
            driver = _driver_of(real)
        driver = driver or proc_name
        if pci_path and exists(pci_path):
            class_code = _pci_class(
                read_text(os.path.join(pci_path, "class"))
            )
        else:
            class_code = "0x0100"
        # 存储控制器或已知 HBA 驱动才保留
        if not pci_db.is_storage_controller(class_code) and driver not in pci_db.HBA_DRIVERS:
            continue
        hosts.append(
            {
                "id": host,
                "proc_name": proc_name,
                "pci_address": pci_address,
                "pci_path": pci_path,
                "class_code": class_code,
                "driver": driver,
            }
        )
    hosts.sort(key=lambda item: int(item["id"][4:]) if item["id"][4:].isdigit() else 0)
    return hosts


def all_hba_host_ids(root: str = "/") -> list[str]:
    return [host["id"] for host in scsi_hosts(root)]


def host_to_hba(root: str = "/") -> dict[str, dict]:
    return {hba["pci_address"]: hba for hba in find_hbas(root, enrich=False)}


def mpt3sas_host_ids(root: str = "/") -> list[str]:
    """兼容旧接口：只返回 mpt3sas 主机。"""
    return [h["id"] for h in scsi_hosts(root) if h["proc_name"] == "mpt3sas"]


def _lspci_link_speed(pci_address: str) -> tuple[str, str]:
    """sysfs 缺少链路信息时，从 lspci -vv 读取 LnkSta。"""
    tool = find_tool("lspci")
    if not tool or not pci_address:
        return "", ""
    result = run_cmd([tool, "-s", pci_address, "-vv"], timeout=5)
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("LnkSta:"):
            speed = ""
            width = ""
            for part in line.replace("LnkSta:", "").split(","):
                part = part.strip()
                if part.startswith("Speed"):
                    speed = part.split(None, 1)[-1].strip()
                elif part.startswith("Width"):
                    width = part.split(None, 1)[-1].replace("x", "").strip()
            return speed, width
    return "", ""


_SAS3FLASH_CACHE: dict = {"time": 0.0, "data": {}}


def _sas3flash_info(timeout: float = 10.0) -> dict:
    """可选：解析 sas3flash -list，补充固件/BIOS/SAS 地址（缓存 5 分钟）。"""
    tool = find_tool("sas3flash")
    if not tool:
        return {}
    now = time.monotonic()
    if now - _SAS3FLASH_CACHE["time"] < 300 and _SAS3FLASH_CACHE["data"]:
        return dict(_SAS3FLASH_CACHE["data"])
    result = run_cmd([tool, "-list"], timeout=timeout)
    if result.timed_out or not result.stdout:
        return {}
    wanted = {
        "sas address": "sas_address",
        "firmware revision": "firmware_version",
        "bios version": "bios_version",
        "nvdata version": "nvdata_version",
        "chip revision": "chip_revision",
        "board name": "board_name",
        "board assembly": "board_assembly",
        "board tracer number": "board_tracer",
        "temperature": "temperature_raw",
    }
    parsed: dict = {}
    for line in result.stdout.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key in wanted and value:
            parsed[wanted[key]] = value
    _SAS3FLASH_CACHE.update(time=now, data=dict(parsed))
    return parsed


def _host_detail(root: str, host: dict) -> dict:
    host_id = host["id"]
    return {
        "id": host_id,
        "pci_address": host["pci_address"],
        "proc_name": host["proc_name"],
        "sas_address": strip_hex(_read_host_attr(root, host_id, "host_sas_address")),
        "firmware_version": _read_host_attr(root, host_id, "version_fw"),
        "bios_version": _read_host_attr(root, host_id, "version_bios"),
        "mpi_version": _read_host_attr(root, host_id, "version_mpi"),
        "nvdata_version": _read_host_attr(
            root, host_id, "version_nvdata_persistent"
        ),
        "board_name": _read_host_attr(root, host_id, "board_name"),
        "board_assembly": _read_host_attr(root, host_id, "board_assembly"),
        "board_tracer": _read_host_attr(root, host_id, "board_tracer"),
        "fw_queue_depth": read_text(
            os.path.join(_sys(root, "class", "scsi_host", host_id), "fw_queue_depth")
        ),
    }


def _make_hba(root: str, pci_address: str, hosts: list[dict]) -> dict:
    details = [_host_detail(root, host) for host in hosts]
    pci_path = _pci_dir(root, pci_address) if pci_address else ""
    exists_pci = bool(pci_path) and exists(pci_path)
    vendor = read_text(os.path.join(pci_path, "vendor")).lower() if exists_pci else ""
    device = read_text(os.path.join(pci_path, "device")).lower() if exists_pci else ""
    class_code = (
        _pci_class(read_text(os.path.join(pci_path, "class")))
        if exists_pci
        else hosts[0]["class_code"]
    )
    driver = hosts[0]["driver"]
    board_name = next(
        (
            d["board_name"]
            for d in details
            if d["board_name"] and d["board_name"] not in ("0", "unknown")
        ),
        "",
    )
    info = pci_db.classify(vendor, device, driver, board_name, class_code)
    firmware = next((d["firmware_version"] for d in details if d["firmware_version"]), "")
    bios = next((d["bios_version"] for d in details if d["bios_version"]), "")
    mpi = next((d["mpi_version"] for d in details if d["mpi_version"]), "")
    nvdata = next((d["nvdata_version"] for d in details if d["nvdata_version"]), "")
    sas_address = next((d["sas_address"] for d in details if d["sas_address"]), "")

    speed = read_text(os.path.join(pci_path, "current_link_speed")) if exists_pci else ""
    width = read_text(os.path.join(pci_path, "current_link_width")) if exists_pci else ""
    max_speed = read_text(os.path.join(pci_path, "max_link_speed")) if exists_pci else ""
    max_width = read_text(os.path.join(pci_path, "max_link_width")) if exists_pci else ""
    if exists_pci and (not speed or not width):
        lspci_speed, lspci_width = _lspci_link_speed(pci_address)
        speed = speed or lspci_speed
        width = width or lspci_width

    kind = info["kind"]
    is_raid = info["is_raid"]
    is_virtual = kind == "虚拟 SCSI" or driver.startswith(("virtio", "vmw_"))
    supports_control = not is_raid and not is_virtual
    capabilities = {
        "smart": True,
        "spin": supports_control,
        "remove": supports_control,
        "rescan": bool(details),
    }

    warnings: list[str] = []
    if is_raid:
        warnings.append(
            "RAID 控制器：这里列出的是逻辑卷。物理盘 SMART 需要厂商工具或 "
            "smartctl -d megaraid,N，单盘停转/热拔出不可用。"
        )
    if driver and driver not in pci_db.HBA_DRIVERS and not info.get("pci_device_name"):
        warnings.append(
            f"驱动 {driver} 未在内置列表中，按通用 SCSI 控制器显示；"
            "SMART/电源控制能力取决于驱动。"
        )
    if not firmware and driver in ("mpt3sas", "mpt2sas"):
        warnings.append("未能通过 sysfs 读取固件版本；可安装 sas3flash 补充")
    if not exists_pci:
        warnings.append("该主机没有关联 PCI 设备（可能是虚拟/调试控制器）")

    return {
        "pci_address": pci_address or f"host:{details[0]['id']}",
        "pci_short": _pci_short(pci_address) if pci_address else f"host:{details[0]['id']}",
        "vendor_id": vendor,
        "device_id": device,
        "subsystem_vendor": read_text(os.path.join(pci_path, "subsystem_vendor"))
        if exists_pci
        else "",
        "subsystem_device": read_text(os.path.join(pci_path, "subsystem_device"))
        if exists_pci
        else "",
        "class_code": class_code,
        "class_name": info.get("class_name") or pci_db.STORAGE_CLASSES.get(class_code, ""),
        "vendor_name": info.get("vendor_name", ""),
        "pci_device_name": info.get("pci_device_name", ""),
        "model": info["model"],
        "chip": info["chip"],
        "kind": kind,
        "family": info.get("family", ""),
        "confidence": info.get("confidence", "low"),
        "is_raid": is_raid,
        "is_virtual": is_virtual,
        "board_name": board_name,
        "driver": driver,
        "driver_version": _module_version(root, driver),
        "firmware_version": firmware,
        "bios_version": bios,
        "mpi_version": mpi,
        "nvdata_version": nvdata,
        "sas_address": sas_address,
        "sas_addresses": unique(d["sas_address"] for d in details if d["sas_address"]),
        "pcie_current_speed": speed,
        "pcie_current_width": width,
        "pcie_max_speed": max_speed,
        "pcie_max_width": max_width,
        "pcie_link": pcie_link(speed, width),
        "hosts": details,
        "host_ids": [d["id"] for d in details],
        "temperature_c": None,
        "it_mode": kind == "SAS HBA",
        "capabilities": capabilities,
        "warnings": warnings,
        "info_source": "sysfs+board" if info.get("confidence") == "high" else (
            "sysfs+pci.ids" if info.get("pci_device_name") else "sysfs"
        ),
    }


def find_hbas(root: str = "/", enrich: bool = True) -> list[dict]:
    """查找所有 SAS/SCSI/RAID 控制器（不再限于 LSI 9207）。"""
    grouped: dict[str, list[dict]] = {}
    for host in scsi_hosts(root):
        key = host["pci_address"] or f"host:{host['id']}"
        grouped.setdefault(key, []).append(host)

    hbas = [_make_hba(root, key, hosts) for key, hosts in grouped.items()]

    # 单卡时才用 sas3flash 补充，避免多卡时信息串台。
    if enrich and len(hbas) == 1 and hbas[0]["vendor_id"] == pci_db.LSI_VENDOR:
        hba = hbas[0]
        extra = _sas3flash_info()
        if extra:
            for key in (
                "firmware_version",
                "bios_version",
                "nvdata_version",
                "sas_address",
                "board_name",
                "board_assembly",
                "board_tracer",
                "chip_revision",
            ):
                value = extra.get(key)
                if value and not hba.get(key):
                    hba[key] = value
            temp = extra.get("temperature_raw")
            if temp:
                match = re.search(r"(-?\d+)", temp)
                if match:
                    hba["temperature_c"] = int(match.group(1))
            hba["info_source"] += "+sas3flash"

    # 排序：有盘/直通卡优先，然后按 PCI 地址，保证默认选中的是主 HBA。
    hbas.sort(
        key=lambda item: (
            item["is_virtual"],
            item["is_raid"],
            item["pci_address"].startswith("host:"),
            item["pci_address"],
        )
    )
    for index, hba in enumerate(hbas, start=1):
        hba["index"] = index
    return hbas


def _scsi_devices_for_hosts(root: str, hosts: Iterable[str]) -> list[tuple[str, str]]:
    """返回 (hctl, host) 列表。"""
    host_set = set(hosts)
    found: list[tuple[str, str]] = []
    for hctl in listdir(_sys(root, "class", "scsi_device")):
        parts = hctl.split(":")
        if len(parts) != 4 or parts[0] not in {h[4:] for h in host_set}:
            continue
        found.append((hctl, "host" + parts[0]))
    return found


def enumerate_drives(
    root: str = "/",
    hosts: Iterable[str] | None = None,
    hbas: list[dict] | None = None,
) -> list[dict]:
    """枚举所有已识别 HBA 下的 SCSI/SAS/SATA 整盘。"""
    if hbas is None:
        hbas = find_hbas(root, enrich=False)
    if hosts is None:
        hosts = [host_id for hba in hbas for host_id in hba["host_ids"]]
    host_list = list(hosts)
    if not host_list:
        return []

    host_map: dict[str, dict] = {}
    for hba in hbas:
        for host_id in hba["host_ids"]:
            host_map[host_id] = hba

    drives: list[dict] = []
    seen: set[str] = set()
    for hctl, host in _scsi_devices_for_hosts(root, host_list):
        device_dir = os.path.join(_sys(root, "class", "scsi_device", hctl), "device")
        for name in listdir(os.path.join(device_dir, "block")):
            if name in seen:
                continue
            block = _block_dir(root, name)
            if not exists(block):
                continue
            if exists(os.path.join(block, "partition")):
                continue
            seen.add(name)

            dev_path = f"/dev/{name}"
            vendor = read_text(os.path.join(block, "device", "vendor"))
            model = read_text(os.path.join(block, "device", "model"))
            firmware = read_text(os.path.join(block, "device", "rev"))
            serial = read_text(os.path.join(block, "device", "serial"))
            wwid = read_text(os.path.join(block, "device", "wwid")) or read_text(
                os.path.join(block, "wwid")
            )
            sas_address = strip_hex(
                read_text(os.path.join(block, "device", "sas_address"))
            )
            enclosure = strip_hex(
                read_text(os.path.join(block, "device", "enclosure_identifier"))
            )
            bay = read_text(os.path.join(block, "device", "bay_identifier"))
            phy = read_text(os.path.join(block, "device", "phy_identifier"))

            sectors = read_int(os.path.join(block, "size"), 0) or 0
            size_bytes = sectors * 512
            rotational = read_int(os.path.join(block, "queue", "rotational"), 1)
            state = read_text(os.path.join(block, "device", "state"), "unknown")
            removable = read_int(os.path.join(block, "removable"), 0) == 1

            if vendor.upper() == "ATA":
                protocol = "SATA"
            elif sas_address:
                protocol = "SAS"
            else:
                protocol = "SCSI"

            usage = usage_for_disk(root, name)
            position = t("直连")
            if enclosure and enclosure not in ("0", "0000000000000000"):
                position = f"ENCL {enclosure[-6:]}"
                if bay and bay != "0":
                    position += " / " + t("槽 {0}", bay)
            elif bay and bay != "0":
                position = t("槽 {0}", bay)

            hba = host_map.get(host, {})
            caps = hba.get("capabilities") or {}
            can_spin = bool(caps.get("spin")) and not hba.get("is_raid", False)
            can_remove = bool(caps.get("remove")) and not hba.get("is_raid", False)

            drives.append(
                {
                    "dev": dev_path,
                    "name": name,
                    "hctl": hctl,
                    "host": host,
                    "host_id": int(host[4:]) if host[4:].isdigit() else -1,
                    "hba_pci": hba.get("pci_address", ""),
                    "hba_model": hba.get("model", ""),
                    "hba_kind": hba.get("kind", ""),
                    "hba_is_raid": bool(hba.get("is_raid")),
                    "can_spin": can_spin,
                    "can_remove": can_remove,
                    "vendor": vendor,
                    "model": model or vendor or "未知硬盘",
                    "firmware": firmware,
                    "serial": serial,
                    "wwid": wwid,
                    "sas_address": sas_address,
                    "enclosure": enclosure,
                    "bay": bay,
                    "phy": phy,
                    "position": position,
                    "size_bytes": size_bytes,
                    "size_human": human_size(size_bytes),
                    "rotational": bool(rotational),
                    "media": "HDD" if rotational else "SSD",
                    "protocol": protocol,
                    "state": state,
                    "removable": removable,
                    "queue_depth": read_int(os.path.join(block, "device", "queue_depth")),
                    "smart": None,
                    "smart_error": None,
                    "smart_loading": False,
                    "power_mode": "unknown",
                    **usage,
                }
            )
    drives.sort(key=lambda item: item["name"])
    return drives


def rescan_hosts(root: str = "/") -> dict:
    """触发所有 HBA 的 SCSI 总线重扫，用于识别热插入的硬盘。"""
    hosts = all_hba_host_ids(root)
    scanned = []
    errors = []
    for host in hosts:
        path = os.path.join(_sys(root, "class", "scsi_host", host), "scan")
        try:
            with open(path, "w", encoding="ascii") as fh:
                fh.write("- - -\n")
            scanned.append(host)
        except OSError as exc:
            errors.append(f"{host}: {exc.strerror or exc}")
    return {"scanned": scanned, "errors": errors}


def pci_summary(root: str = "/") -> list[dict]:
    """诊断用：列出所有 PCI 存储控制器（包括驱动未加载/未识别的）。"""
    pci_base = _sys(root, "bus", "pci", "devices")
    recognized = {
        hba["pci_address"]: hba
        for hba in find_hbas(root, enrich=False)
    }
    result = []
    for pci_name in listdir(pci_base):
        pci_path = os.path.join(pci_base, pci_name)
        class_code = _pci_class(read_text(os.path.join(pci_path, "class")))
        if not pci_db.is_storage_controller(class_code):
            continue
        vendor = read_text(os.path.join(pci_path, "vendor")).lower()
        device = read_text(os.path.join(pci_path, "device")).lower()
        driver = _driver_of(pci_path)
        info = pci_db.classify(vendor, device, driver, "", class_code)
        hba = recognized.get(pci_name)
        result.append(
            {
                "pci_address": pci_name,
                "vendor_id": vendor,
                "device_id": device,
                "subsystem_device": read_text(
                    os.path.join(pci_path, "subsystem_device")
                ),
                "driver": driver,
                "class_code": class_code,
                "class_name": info.get("class_name", ""),
                "model": hba["model"] if hba else info["model"],
                "chip": hba["chip"] if hba else info["chip"],
                "kind": hba["kind"] if hba else info["kind"],
                "recognized": bool(hba),
            }
        )
    result.sort(key=lambda item: item["pci_address"])
    return result


def sas_topology(root: str = "/") -> dict:
    """导出 SAS 拓扑，便于定位"哪块盘对应哪个 PHY"。"""
    base = _sys(root, "class", "sas_phy")
    phys = []
    for entry in listdir(base):
        phy_path = os.path.join(base, entry)
        forms = [os.path.abspath(phy_path)]
        try:
            forms.append(os.path.realpath(phy_path))
        except OSError:
            pass
        links = {}
        for form in list(forms):
            for child in listdir(form):
                child_path = os.path.join(form, child)
                if os.path.islink(child_path):
                    try:
                        links[child] = os.readlink(child_path)
                    except OSError:
                        pass
        phys.append(
            {
                "name": entry,
                "real": forms[-1],
                "enable": read_text(os.path.join(phy_path, "enable")),
                "links": links,
            }
        )

    drives = []
    for name in listdir(_sys(root, "block")):
        device_dir = os.path.join(_sys(root, "block", name), "device")
        if not exists(device_dir):
            continue
        literal = os.path.abspath(device_dir)
        try:
            real = os.path.realpath(device_dir)
        except OSError:
            real = ""
        markers = re.findall(
            r"(?:end_device|port)-\d+:\d+", f"{literal} {real}"
        )
        drives.append(
            {
                "dev": f"/dev/{name}",
                "literal": literal,
                "real": real,
                "sas_address": strip_hex(
                    read_text(os.path.join(device_dir, "sas_address"))
                ),
                "markers": unique(markers),
            }
        )

    return {
        "phys": phys,
        "drives": drives,
        # phy_identifier 属于 rphy 设备（class sas_device）；
        # sas_end_device 类只有 end_dev_* 属性，读不到 phy_identifier。
        "sas_end_devices": listdir(_sys(root, "class", "sas_end_device")),
        "sas_devices": [
            {
                "name": entry,
                "phy_identifier": read_text(
                    os.path.join(_sys(root, "class", "sas_device"), entry, "phy_identifier")
                ),
                "port_identifier": read_text(
                    os.path.join(_sys(root, "class", "sas_device"), entry, "port_identifier")
                ),
            }
            for entry in listdir(_sys(root, "class", "sas_device"))
        ],
        "sas_ports": listdir(_sys(root, "class", "sas_port")),
    }
