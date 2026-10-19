"""PCI 设备名称与 HBA 型号识别。

优先读取系统 pci.ids（Debian/Ubuntu 的 pciutils 包提供），
因此新卡即使没有内置进程序，也能显示厂商与设备名称。
pci.ids 缺失时退回内置的常见 LSI/Broadcom 条目。
"""

from __future__ import annotations

import os
import re
PCI_IDS_PATHS = (
    "/usr/share/misc/pci.ids",
    "/usr/share/hwdata/pci.ids",
    "/usr/share/pci.ids",
    "/var/lib/pciutils/pci.ids",
)

# 内置兜底：仅覆盖常见的 LSI/Broadcom SAS 控制器。
LSI_VENDOR = "0x1000"
LSI_DEVICE_NAMES = {
    "0x0014": "MegaRAID Tri-Mode SAS3516",
    "0x0015": "MegaRAID Tri-Mode SAS3416",
    "0x0016": "MegaRAID Tri-Mode SAS3508",
    "0x0017": "MegaRAID Tri-Mode SAS3408",
    "0x001b": "MegaRAID Tri-Mode SAS3504",
    "0x001c": "MegaRAID Tri-Mode SAS3404",
    "0x0050": "SAS1064 PCI-X Fusion-MPT SAS",
    "0x0054": "SAS1068 PCI-X Fusion-MPT SAS",
    "0x0055": "SAS1068 PCI-X Fusion-MPT SAS",
    "0x0056": "SAS1064ET PCI-Express Fusion-MPT SAS",
    "0x0058": "SAS1068E PCI-Express Fusion-MPT SAS",
    "0x005a": "SAS1066E PCI-Express Fusion-MPT SAS",
    "0x005d": "MegaRAID SAS-3 3108 [Invader]",
    "0x005f": "MegaRAID SAS-3 3008 [Fury]",
    "0x0060": "MegaRAID SAS 1078",
    "0x0062": "SAS1078 PCI-Express Fusion-MPT SAS",
    "0x0064": "SAS2116 PCI-Express Fusion-MPT SAS-2 [Meteor]",
    "0x0065": "SAS2116 PCI-Express Fusion-MPT SAS-2 [Meteor]",
    "0x006e": "SAS2308 PCI-Express Fusion-MPT SAS-2",
    "0x0070": "SAS2004 PCI-Express Fusion-MPT SAS-2 [Spitfire]",
    "0x0071": "MR SAS HBA 2004",
    "0x0072": "SAS2008 PCI-Express Fusion-MPT SAS-2 [Falcon]",
    "0x0073": "MegaRAID SAS 2008 [Falcon]",
    "0x0074": "SAS2108 PCI-Express Fusion-MPT SAS-2 [Liberator]",
    "0x0079": "MegaRAID SAS 2108 [Liberator]",
    "0x0080": "SAS2208 PCI-Express Fusion-MPT SAS-2",
    "0x0081": "SAS2208 PCI-Express Fusion-MPT SAS-2",
    "0x0082": "SAS2208 PCI-Express Fusion-MPT SAS-2",
    "0x0083": "SAS2208 PCI-Express Fusion-MPT SAS-2",
    "0x0084": "SAS2208 PCI-Express Fusion-MPT SAS-2",
    "0x0085": "SAS2208 PCI-Express Fusion-MPT SAS-2",
    "0x0086": "SAS2308 PCI-Express Fusion-MPT SAS-2",
    "0x0087": "SAS2308 PCI-Express Fusion-MPT SAS-2",
    "0x0090": "SAS3108 PCI-Express Fusion-MPT SAS-3",
    "0x0091": "SAS3108 PCI-Express Fusion-MPT SAS-3",
    "0x0094": "SAS3108 PCI-Express Fusion-MPT SAS-3",
    "0x0095": "SAS3108 PCI-Express Fusion-MPT SAS-3",
    "0x0096": "SAS3004 PCI-Express Fusion-MPT SAS-3",
    "0x0097": "SAS3008 PCI-Express Fusion-MPT SAS-3",
    "0x00a5": "Fusion-MPT 24GSAS/PCIe SAS40xx/41xx",
    "0x00ab": "SAS3516 Fusion-MPT Tri-Mode RAID On Chip (ROC)",
    "0x00ac": "SAS3416 Fusion-MPT Tri-Mode I/O Controller Chip (IOC)",
    "0x00ae": "SAS3508 Fusion-MPT Tri-Mode RAID On Chip (ROC)",
    "0x00af": "SAS3408 Fusion-MPT Tri-Mode I/O Controller Chip (IOC)",
    "0x00b3": "Fusion-MPT 24G SAS/PCIe SAS50xx/SAS51xx",
    "0x00b5": "Fusion-MPT 24G SAS/PCIe SAS50xx/SAS51xx",
    "0x00be": "SAS3504 Fusion-MPT Tri-Mode RAID On Chip (ROC)",
    "0x00bf": "SAS3404 Fusion-MPT Tri-Mode I/O Controller Chip (IOC)",
    "0x00c0": "SAS3324 PCI-Express Fusion-MPT SAS-3",
    "0x00c1": "SAS3324 PCI-Express Fusion-MPT SAS-3",
    "0x00c2": "SAS3324 PCI-Express Fusion-MPT SAS-3",
    "0x00c3": "SAS3324 PCI-Express Fusion-MPT SAS-3",
    "0x00c4": "SAS3224 PCI-Express Fusion-MPT SAS-3",
    "0x00c5": "SAS3316 PCI-Express Fusion-MPT SAS-3",
    "0x00c6": "SAS3316 PCI-Express Fusion-MPT SAS-3",
    "0x00c7": "SAS3316 PCI-Express Fusion-MPT SAS-3",
    "0x00c8": "SAS3316 PCI-Express Fusion-MPT SAS-3",
    "0x00c9": "SAS3216 PCI-Express Fusion-MPT SAS-3",
    "0x00ce": "MegaRAID SAS-3 3316 [Intruder]",
    "0x00cf": "MegaRAID SAS-3 3324 [Intruder]",
    "0x00d0": "SAS3716 Fusion-MPT Tri-Mode RAID Controller Chip (ROC)",
    "0x00d1": "SAS3616 Fusion-MPT Tri-Mode I/O Controller Chip (IOC)",
    "0x00d3": "MegaRAID Tri-Mode SAS3716W",
    "0x00e1": "Fusion-MPT 12GSAS/PCIe SAS39xx",
    "0x00e2": "Fusion-MPT 12GSAS/PCIe Secure SAS39xx",
    "0x00e5": "Fusion-MPT 12GSAS/PCIe SAS38xx",
    "0x00e6": "Fusion-MPT 12GSAS/PCIe Secure SAS38xx",
    "0x10e1": "MegaRAID 12GSAS/PCIe SAS39xx",
    "0x10e2": "MegaRAID 12GSAS/PCIe Secure SAS39xx",
    "0x10e5": "MegaRAID 12GSAS/PCIe SAS38xx",
    "0x10e6": "MegaRAID 12GSAS/PCIe Secure SAS38xx",
}

# board_name 前缀 -> (芯片, 9207/9211/9300 等营销系列, 是否 RAID)
BOARD_FAMILIES = {
    "SAS2004": ("SAS2004", "9211", False),
    "SAS2008": ("SAS2008", "9211", False),
    "SAS2116": ("SAS2116", "9205", False),
    "SAS2208": ("SAS2208", "9271", True),
    "SAS2308": ("SAS2308", "9207", False),
    "SAS3004": ("SAS3004", "9300", False),
    "SAS3008": ("SAS3008", "9300", False),
    "SAS3108": ("SAS3108", "", True),
    "SAS3216": ("SAS3216", "", True),
    "SAS3224": ("SAS3224", "", True),
    "SAS3316": ("SAS3316", "", True),
    "SAS3324": ("SAS3324", "", True),
    "SAS3408": ("SAS3408", "9400", False),
    "SAS3416": ("SAS3416", "9400", False),
    "SAS3508": ("SAS3508", "9500", False),
    "SAS3516": ("SAS3516", "9500", False),
    "SAS3616": ("SAS3616", "9600", False),
    "SAS3716": ("SAS3716", "9600", True),
}

# 端口形态：8i / 8e / 4i4e / 16i 等
PORT_SHAPE_RE = re.compile(r"^\d+(?:i(?:\d+e)?|e)$")

STORAGE_CLASSES = {
    "0x0100": "SCSI 控制器",
    "0x0104": "RAID 控制器",
    "0x0107": "SAS 控制器",
    "0x0180": "其他存储控制器",
}

HBA_DRIVERS = {
    "mpt3sas": ("SAS HBA", "Broadcom/LSI Fusion-MPT"),
    "mpt2sas": ("SAS HBA", "Broadcom/LSI Fusion-MPT"),
    "mptsas": ("SAS HBA", "Broadcom/LSI Fusion-MPT"),
    "mptspi": ("SCSI HBA", "Broadcom/LSI Fusion-MPT"),
    "mptfc": ("SCSI HBA", "Broadcom/LSI Fusion-MPT"),
    "megaraid_sas": ("RAID", "Broadcom MegaRAID"),
    "isci": ("SAS HBA", "Intel C600"),
    "aacraid": ("RAID", "Adaptec"),
    "smartpqi": ("SmartRAID", "Microchip SmartPQI"),
    "hpsa": ("RAID", "HPE Smart Array"),
    "arcmsr": ("RAID", "Areca"),
    "pm80xx": ("SAS HBA", "PMC-Sierra"),
    "mvsas": ("SAS HBA", "Marvell"),
    "hisi_sas": ("SAS HBA", "HiSilicon"),
    "qla2xxx": ("FC HBA", "QLogic"),
    "lpfc": ("FC HBA", "Emulex"),
    "snic": ("FC HBA", "Cisco"),
    "virtio_scsi": ("虚拟 SCSI", "Virtio"),
    "vmw_pvscsi": ("虚拟 SCSI", "VMware"),
    "sym53c8xx": ("SCSI HBA", "Symbios"),
    "aic7xxx": ("SCSI HBA", "Adaptec"),
}

_CACHE: dict = {"mtime": None, "vendors": {}, "devices": {}, "path": ""}


def _find_pci_ids() -> str:
    for path in PCI_IDS_PATHS:
        if os.path.isfile(path):
            return path
    return ""


def _parse_pci_ids(path: str) -> tuple[dict, dict]:
    vendors: dict[str, str] = {}
    devices: dict[tuple[str, str], str] = {}
    vendor = ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip() or line.startswith("#"):
                    continue
                if line.startswith("\t\t"):
                    continue  # 子系统 ID 暂不使用
                if line.startswith("\t"):
                    parts = line[1:].split(None, 1)
                    if len(parts) == 2 and re.fullmatch(r"[0-9a-fA-F]{4}", parts[0]):
                        devices[(vendor, parts[0].lower())] = parts[1].strip()
                    continue
                parts = line.split(None, 1)
                if len(parts) == 2 and re.fullmatch(r"[0-9a-fA-F]{4}", parts[0]):
                    vendor = parts[0].lower()
                    vendors[vendor] = parts[1].strip()
    except OSError:
        return {}, {}
    return vendors, devices


def _load() -> tuple[dict, dict]:
    path = _find_pci_ids()
    try:
        mtime = os.path.getmtime(path) if path else 0
    except OSError:
        mtime = 0
    if _CACHE["mtime"] == mtime and _CACHE["path"] == path:
        return _CACHE["vendors"], _CACHE["devices"]
    vendors, devices = _parse_pci_ids(path) if path else ({}, {})
    _CACHE.update(mtime=mtime, vendors=vendors, devices=devices, path=path)
    return vendors, devices


def reload() -> None:
    _CACHE.update(mtime=None, vendors={}, devices={}, path="")


def _hex4(value: str | int | None) -> str:
    if value is None:
        return ""
    if isinstance(value, int):
        return f"{value:04x}"
    text = str(value).strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    return text.zfill(4)[-4:]


def vendor_name(vendor_id: str) -> str:
    vendors, _ = _load()
    key = _hex4(vendor_id)
    if key in vendors:
        return vendors[key]
    if key == "1000":
        return "Broadcom / LSI"
    return ""


def device_name(vendor_id: str, device_id: str) -> str:
    vendors, devices = _load()
    vkey, dkey = _hex4(vendor_id), _hex4(device_id)
    name = devices.get((vkey, dkey), "")
    if name:
        return name
    if vkey == "1000":
        return LSI_DEVICE_NAMES.get(f"0x{dkey}", "")
    return ""


def chip_from_name(name: str) -> str:
    if not name:
        return ""
    match = re.search(r"\b(53c\d{4}|53C\d{4})\b", name)
    if match:
        return match.group(1).lower()
    match = re.search(r"\b(SAS\d{4})\b", name, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    match = re.search(r"\bSAS[- ]?\d[ .-]?(\d{4})\b", name, re.IGNORECASE)
    if match:
        return f"SAS{match.group(1)}"
    match = re.search(r"\bSAS(4[01]xx|5[01]xx|3[89]xx)\b", name, re.IGNORECASE)
    if match:
        return "SAS" + match.group(1)
    return ""


def classify(
    vendor_id: str,
    device_id: str,
    driver: str = "",
    board_name: str = "",
    pci_class: str = "",
) -> dict:
    """返回型号、芯片、类型等展示字段。

    优先级：board_name（板卡实际型号）> pci.ids 设备名 > 驱动信息。
    """
    vendor_id = (vendor_id or "").lower()
    device_id = (device_id or "").lower()
    driver = (driver or "").strip()
    board_name = (board_name or "").strip()
    vendor = vendor_name(vendor_id)
    std_name = device_name(vendor_id, device_id)

    chip = ""
    model = ""
    kind = ""
    family = ""
    confidence = "low"

    # 1) 板卡 board_name：mpt3sas/mpt2sas 会暴露真实板卡名，如 SAS2308-8i。
    if board_name:
        prefix_match = re.match(r"^(SAS\d{4})\b", board_name, re.IGNORECASE)
        prefix = prefix_match.group(1).upper() if prefix_match else ""
        if prefix in BOARD_FAMILIES:
            chip, family, is_raid = BOARD_FAMILIES[prefix]
            suffix = board_name[len(prefix) :].strip("- ").strip()
            if not is_raid and family and PORT_SHAPE_RE.match(suffix):
                model = f"LSI SAS {family}-{suffix}"
                confidence = "high"
            elif is_raid:
                model = f"Broadcom {board_name}"
                confidence = "high"
            else:
                model = f"Broadcom {board_name}"
                confidence = "medium"
        elif board_name.lower().startswith("megaraid"):
            model = f"Broadcom {board_name}"
            kind = "RAID"
            confidence = "high"
        else:
            model = f"Broadcom {board_name}"
            confidence = "medium"

    # 2) pci.ids 设备名（未收录的卡也能显示正确名称）
    if std_name:
        chip = chip or chip_from_name(std_name)
        if not model:
            if std_name.lower().startswith(("megaraid", "mega raid")):
                model = f"Broadcom {std_name}"
            elif vendor:
                model = f"{vendor} {std_name}"
            else:
                model = std_name
            confidence = "medium"

    # 3) 驱动兜底
    driver_info = HBA_DRIVERS.get(driver)
    if driver_info:
        driver_kind, driver_family = driver_info
        kind = kind or driver_kind
        family = family or driver_family
    if not model:
        if driver_info:
            model = f"{driver_family} 控制器 ({vendor_id}:{device_id})"
        else:
            model = f"{vendor or '存储控制器'} {vendor_id}:{device_id}"
        confidence = "low"

    if not kind:
        lowered = f"{std_name} {board_name}".lower()
        if "megaraid" in lowered or "raid" in lowered:
            kind = "RAID"
        elif "sas" in lowered:
            kind = "SAS HBA"
        else:
            kind = "SCSI HBA"

    if not chip:
        chip = chip_from_name(board_name)

    return {
        "model": model,
        "chip": chip or "—",
        "kind": kind,
        "family": family,
        "confidence": confidence,
        "vendor_name": vendor or ("Broadcom / LSI" if vendor_id == "0x1000" else ""),
        "pci_device_name": std_name,
        "class_name": STORAGE_CLASSES.get(pci_class, ""),
        "is_raid": kind in ("RAID", "SmartRAID"),
    }


def is_storage_controller(pci_class: str) -> bool:
    return pci_class in STORAGE_CLASSES


def pci_ids_path() -> str:
    return _find_pci_ids()
