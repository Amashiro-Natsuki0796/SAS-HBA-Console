"""演示模式：不接触真实硬件，用于预览界面、截图与无卡环境联调。"""

from __future__ import annotations

import copy
import time

from ..i18n import t
from ..util import human_size, iso_now
from . import control
from .provider import ProviderError


def _ata_attr(
    attr_id: int,
    name: str,
    value: int,
    worst: int,
    thresh: int,
    raw: int | str,
    raw_string: str | None = None,
    prefailure: bool = False,
) -> dict:
    return {
        "id": attr_id,
        "name": name,
        "value": value,
        "worst": worst,
        "thresh": thresh,
        "when_failed": "",
        "raw_value": raw,
        "raw_string": str(raw) if raw_string is None else raw_string,
        "prefailure": prefailure,
    }


def _smart_ata(
    model: str,
    serial: str,
    firmware: str,
    temp: int,
    hours: int,
    cycles: int,
    attrs: list[dict],
    passed: bool = True,
    rpm: int = 7200,
    capacity: int | None = None,
) -> dict:
    failed = [
        attr["id"]
        for attr in attrs
        if attr.get("when_failed")
        or (
            attr.get("prefailure")
            and attr.get("value") is not None
            and attr.get("thresh") is not None
            and attr["value"] <= attr["thresh"]
        )
    ]
    raw = {
        "json_format_version": [1, 0],
        "device": {
            "name": "/dev/sdX",
            "info_name": "/dev/sdX [SAT]",
            "type": "sat",
            "protocol": "ATA",
        },
        "model_name": model,
        "serial_number": serial,
        "firmware_version": firmware,
        "user_capacity": {"blocks": 0, "bytes": capacity or 0},
        "rotation_rate": rpm,
        "smart_status": {"passed": passed},
        "temperature": {"current": temp},
        "power_on_time": {"hours": hours},
        "power_cycle_count": cycles,
        "ata_smart_attributes": {"table": attrs},
    }
    return {
        "available": True,
        "standby": False,
        "power_mode": "active",
        "error": None,
        "protocol": "ATA",
        "model": model,
        "vendor": "ATA",
        "serial": serial,
        "firmware": firmware,
        "capacity_bytes": capacity,
        "passed": passed,
        "temperature_c": temp,
        "power_on_hours": hours,
        "power_cycle_count": cycles,
        "rotation_rate": rpm,
        "attributes": attrs,
        "failed_attributes": failed,
        "scsi": {},
        "messages": [],
        "exit_status": 0,
        "stderr": "",
        "timestamp": iso_now(),
        "raw": raw,
    }


def _smart_sas(
    model: str,
    serial: str,
    firmware: str,
    temp: int,
    hours: int,
    cycles: int,
    grown: int,
    capacity: int,
    rpm: int = 7200,
) -> dict:
    raw = {
        "json_format_version": [1, 0],
        "device": {
            "name": "/dev/sdX",
            "info_name": "/dev/sdX",
            "type": "scsi",
            "protocol": "SCSI",
        },
        "scsi_model_name": model,
        "scsi_vendor": "SEAGATE",
        "serial_number": serial,
        "firmware_version": firmware,
        "user_capacity": {"blocks": 0, "bytes": capacity},
        "rotation_rate": rpm,
        "smart_status": {"passed": True},
        "temperature": {"current": temp},
        "power_on_time": {"hours": hours},
        "power_cycle_count": cycles,
        "scsi_grown_defect_list": grown,
        "scsi_error_counter_log": {
            "read": {
                "total_errors_corrected": 0,
                "total_uncorrected_errors": 0,
                "gigabytes_processed": "412356.782",
            },
            "write": {
                "total_errors_corrected": 0,
                "total_uncorrected_errors": 0,
                "gigabytes_processed": "338112.104",
            },
            "verify": {
                "total_errors_corrected": 0,
                "total_uncorrected_errors": 0,
                "gigabytes_processed": "0.000",
            },
        },
        "scsi_start_stop_cycle_counter": {
            "accumulated_start_stop_cycles": cycles,
        },
    }
    return {
        "available": True,
        "standby": False,
        "power_mode": "active",
        "error": None,
        "protocol": "SCSI",
        "model": model,
        "vendor": "SEAGATE",
        "serial": serial,
        "firmware": firmware,
        "capacity_bytes": capacity,
        "passed": True,
        "temperature_c": temp,
        "power_on_hours": hours,
        "power_cycle_count": cycles,
        "rotation_rate": rpm,
        "attributes": [],
        "failed_attributes": [],
        "scsi": {
            "read": {"corrected": 0, "uncorrected": 0, "gigabytes": "412356.782"},
            "write": {"corrected": 0, "uncorrected": 0, "gigabytes": "338112.104"},
            "verify": {"corrected": 0, "uncorrected": 0, "gigabytes": "0.000"},
            "grown_defects": grown,
            "start_stop_cycles": cycles,
        },
        "messages": [],
        "exit_status": 0,
        "stderr": "",
        "timestamp": iso_now(),
        "raw": raw,
    }


def _standby_smart(model: str, serial: str, protocol: str = "ATA") -> dict:
    return {
        "available": False,
        "standby": True,
        "power_mode": "standby",
        "error": "硬盘处于待机/停转状态",
        "protocol": protocol,
        "model": model,
        "serial": serial,
        "temperature_c": None,
        "power_on_hours": None,
        "power_cycle_count": None,
        "attributes": [],
        "scsi": {},
        "messages": [
            {
                "string": "Device is in STANDBY mode, exit(2)",
                "severity": "information",
            }
        ],
        "exit_status": 2,
        "timestamp": iso_now(),
    }


class DemoProvider:
    """内存中的多控制器假硬件，字段与 LocalProvider 完全一致。"""

    demo = True

    def __init__(self):
        self.created = time.time()
        self._removed: set[str] = set()
        self._hbas = self._build_hbas()
        self._drives = self._build_drives()

    # ------------------------------------------------------------------ HBA
    def _hba(
        self,
        index: int,
        pci: str,
        device: str,
        class_code: str,
        board: str,
        chip: str,
        model: str,
        kind: str,
        driver: str,
        firmware: str,
        bios: str,
        sas: str,
        pcie: str = "8.0 GT/s ×8",
        is_raid: bool = False,
        warnings: list[str] | None = None,
    ) -> dict:
        return {
            "index": index,
            "pci_address": pci,
            "pci_short": pci.split(":", 1)[1] if ":" in pci else pci,
            "vendor_id": "0x1000",
            "device_id": device,
            "subsystem_vendor": "0x1000",
            "subsystem_device": "0x3020",
            "class_code": class_code,
            "class_name": "SAS 控制器" if class_code == "0x0107" else "RAID 控制器",
            "vendor_name": "Broadcom / LSI",
            "pci_device_name": board,
            "model": model,
            "chip": chip,
            "kind": kind,
            "family": "",
            "confidence": "high",
            "is_raid": is_raid,
            "is_virtual": False,
            "board_name": board,
            "driver": driver,
            "driver_version": "43.100.00.00",
            "firmware_version": firmware,
            "bios_version": bios,
            "mpi_version": "2.00.00.00",
            "nvdata_version": "14.00.00.00",
            "sas_address": sas,
            "sas_addresses": [sas],
            "pcie_current_speed": "8.0 GT/s",
            "pcie_current_width": "8",
            "pcie_max_speed": "8.0 GT/s",
            "pcie_max_width": "8",
            "pcie_link": pcie,
            "hosts": [
                {
                    "id": f"host{index}",
                    "pci_address": pci,
                    "proc_name": driver,
                    "sas_address": sas,
                    "firmware_version": firmware,
                    "bios_version": bios,
                    "mpi_version": "2.00.00.00",
                    "nvdata_version": "14.00.00.00",
                    "board_name": board,
                    "board_assembly": "Y3K12-04021",
                    "board_tracer": "SP12345678",
                    "fw_queue_depth": "1024",
                }
            ],
            "host_ids": [f"host{index}"],
            "temperature_c": None,
            "it_mode": not is_raid,
            "capabilities": {
                "smart": True,
                "spin": not is_raid,
                "remove": not is_raid,
                "rescan": True,
            },
            "warnings": warnings or [],
            "info_source": "demo",
            "drive_count": 0,
        }

    def _build_hbas(self) -> list[dict]:
        return [
            self._hba(
                1,
                "0000:03:00.0",
                "0x0087",
                "0x0107",
                "SAS2308-8i",
                "SAS2308",
                "LSI SAS 9207-8i",
                "SAS HBA",
                "mpt3sas",
                "20.00.07.00",
                "07.39.02.00",
                "500605b00a1b2c3d",
            ),
            self._hba(
                2,
                "0000:04:00.0",
                "0x0097",
                "0x0107",
                "SAS3008-8i",
                "SAS3008",
                "LSI SAS 9300-8i",
                "SAS HBA",
                "mpt3sas",
                "16.00.12.00",
                "08.37.00.00",
                "500605b00c3d4e5f",
            ),
            self._hba(
                3,
                "0000:05:00.0",
                "0x005d",
                "0x0104",
                "",
                "SAS3108",
                "Broadcom MegaRAID SAS-3 3108 [Invader]",
                "RAID",
                "megaraid_sas",
                "",
                "",
                "",
                warnings=[
                    "RAID 控制器：这里列出的是逻辑卷。物理盘 SMART 需要厂商工具或 "
                    "smartctl -d megaraid,N，单盘停转/热拔出不可用。"
                ],
                is_raid=True,
            ),
        ]

    # ------------------------------------------------------------------ 硬盘
    def _drive(
        self,
        hba: dict,
        name: str,
        hctl: str,
        vendor: str,
        model: str,
        serial: str,
        firmware: str,
        size_bytes: int,
        protocol: str,
        rotational: bool,
        position: str,
        sas_address: str,
        smart: dict | None,
        mountpoints: list[str] | None = None,
        power_mode: str = "active",
    ) -> dict:
        mountpoints = mountpoints or []
        blockers = []
        if mountpoints:
            blockers.append("已挂载：" + "、".join(mountpoints))
        can_spin = bool(hba["capabilities"]["spin"]) and not hba["is_raid"]
        can_remove = bool(hba["capabilities"]["remove"]) and not hba["is_raid"]
        return {
            "dev": f"/dev/{name}",
            "name": name,
            "hctl": hctl,
            "host": hba["host_ids"][0],
            "host_id": hba["index"],
            "hba_pci": hba["pci_address"],
            "hba_model": hba["model"],
            "hba_kind": hba["kind"],
            "hba_is_raid": hba["is_raid"],
            "can_spin": can_spin,
            "can_remove": can_remove,
            "vendor": vendor,
            "model": model,
            "firmware": firmware,
            "serial": serial,
            "wwid": "naa." + sas_address if sas_address else "",
            "sas_address": sas_address,
            "enclosure": "5000c500a1b2c3d5" if protocol == "SAS" else "",
            "bay": position.split()[-1] if "bay" in position.lower() or "槽" in position else "",
            "phy": "0",
            "position": position,
            "size_bytes": size_bytes,
            "size_human": human_size(size_bytes),
            "rotational": rotational,
            "media": "HDD" if rotational else "SSD",
            "protocol": protocol,
            "state": "running",
            "removable": not blockers,
            "queue_depth": 32 if protocol == "SAS" else 31,
            "smart": smart,
            "smart_error": None,
            "smart_loading": False,
            "power_mode": power_mode,
            "mountpoints": mountpoints,
            "swaps": [],
            "holders": [],
            "is_system": False,
            "blockers": blockers,
        }

    def _build_drives(self) -> list[dict]:
        hba1, hba2, raid = self._hbas
        failing_attrs = [
            _ata_attr(1, "Raw_Read_Error_Rate", 71, 60, 6, 1883212211, prefailure=True),
            _ata_attr(5, "Reallocated_Sector_Ct", 92, 92, 10, 8, "8 (8 个待映射扇区)", prefailure=True),
            _ata_attr(9, "Power_On_Hours", 44, 44, 0, 41230),
            _ata_attr(187, "Reported_Uncorrect", 88, 88, 0, 6),
            _ata_attr(190, "Airflow_Temperature_Cel", 51, 40, 45, 49),
            _ata_attr(194, "Temperature_Celsius", 51, 40, 0, 49),
            _ata_attr(197, "Current_Pending_Sector", 98, 98, 0, 2, "2", prefailure=True),
            _ata_attr(198, "Offline_Uncorrectable", 92, 92, 0, 6, "6", prefailure=True),
            _ata_attr(199, "UDMA_CRC_Error_Count", 200, 200, 0, 12),
            _ata_attr(241, "Total_LBAs_Written", 78, 78, 0, 98182300112),
        ]
        ssd_attrs = [
            _ata_attr(5, "Reallocated_Sector_Ct", 100, 100, 10, 0, prefailure=True),
            _ata_attr(9, "Power_On_Hours", 98, 98, 0, 3120),
            _ata_attr(194, "Temperature_Celsius", 68, 55, 0, 32),
            _ata_attr(199, "UDMA_CRC_Error_Count", 200, 200, 0, 0),
            _ata_attr(202, "Percent_Lifetime_Remain", 97, 97, 1, 97),
            _ata_attr(241, "Total_LBAs_Written", 99, 99, 0, 1288300112),
        ]
        return [
            self._drive(
                hba1, "sdb", "0:0:0:0", "SEAGATE", "ST8000NM0075", "ZA1A1A1A", "0004",
                8001563222016, "SAS", True, "ENCL b2c3d5 / " + t("槽 {0}", 1), "5000c500a1b2c3d4",
                _smart_sas("ST8000NM0075", "ZA1A1A1A", "0004", 36, 24310, 78, 0, 8001563222016),
            ),
            self._drive(
                hba1, "sdc", "0:0:1:0", "ATA", "WDS200T2R0A-00BH1", "22118A800123", "X61100WD",
                2000398934016, "SATA", False, t("直连"), "",
                _smart_ata("WDS200T2R0A-00BH1", "22118A800123", "X61100WD", 32, 3120, 19,
                           ssd_attrs, passed=True, rpm=0, capacity=2000398934016),
                mountpoints=["/srv/data"],
            ),
            self._drive(
                hba1, "sdd", "0:0:2:0", "ATA", "ST4000VN008-2DR166", "ZDH0ABCD", "SC60",
                4000787030016, "SATA", True, "ENCL b2c3d5 / " + t("槽 {0}", 3), "",
                _standby_smart("ST4000VN008-2DR166", "ZDH0ABCD"), power_mode="standby",
            ),
            self._drive(
                hba1, "sde", "0:0:3:0", "ATA", "HUH721010ALE600", "7LJ0FAIL", "LHGL0001",
                10000831348736, "SATA", True, "ENCL b2c3d5 / " + t("槽 {0}", 4), "",
                _smart_ata("HUH721010ALE600", "7LJ0FAIL", "LHGL0001", 49, 41230, 512,
                           failing_attrs, passed=False, rpm=7200, capacity=10000831348736),
            ),
            # 第二张卡：SAS3008 / 9300-8i
            self._drive(
                hba2, "sdf", "1:0:0:0", "SEAGATE", "ST1920FM0003", "Z4B0DEMO", "0001",
                1920383410176, "SAS", False, "ENCL d4e5f6 / " + t("槽 {0}", 0), "5000c500c3d4e5f0",
                _smart_sas("ST1920FM0003", "Z4B0DEMO", "0001", 34, 1200, 22, 0, 1920383410176, rpm=0),
            ),
            self._drive(
                hba2, "sdg", "1:0:1:0", "ATA", "WD40EFRX-68N32N0", "WD-WCC7DEMO", "82.00A82",
                4000787030016, "SATA", True, t("直连"), "",
                _smart_ata("WD40EFRX-68N32N0", "WD-WCC7DEMO", "82.00A82", 38, 9021, 64, [
                    _ata_attr(5, "Reallocated_Sector_Ct", 100, 100, 10, 0),
                    _ata_attr(9, "Power_On_Hours", 90, 90, 0, 9021),
                    _ata_attr(194, "Temperature_Celsius", 62, 50, 0, 38),
                    _ata_attr(199, "UDMA_CRC_Error_Count", 200, 200, 0, 0),
                ], capacity=4000787030016),
            ),
            # 第三张卡：MegaRAID 逻辑卷（VD），演示 RAID 卡上的只读展示与禁用按钮
            self._drive(
                raid, "sdh", "1:0:0:0", "AVAGO", "MR9361-8i", "DEMO-VD0", "4.660",
                3599456636928, "SAS", True, "VD 0 / RAID 5", "5000c500d4e5f6a0",
                _smart_sas("MR9361-8i", "DEMO-VD0", "4.660", 41, 5120, 36, 0, 3599456636928),
            ),
        ]

    # ------------------------------------------------------------------ API
    def snapshot(self) -> dict:
        drives = [
            copy.deepcopy(d) for d in self._drives if d["name"] not in self._removed
        ]
        hbas = []
        for hba in self._hbas:
            item = copy.deepcopy(hba)
            item["drive_count"] = sum(
                1 for drive in drives if drive["hba_pci"] == item["pci_address"]
            )
            hbas.append(item)
        tools = {
            "smartctl": {"available": True, "path": "/usr/sbin/smartctl", "version": "smartctl 7.4 2023-08-01 r5530 [x86_64-linux]"},
            "sdparm": {"available": True, "path": "/usr/bin/sdparm", "version": "version: 1.12 20210421"},
            "sg_start": {"available": True, "path": "/usr/bin/sg_start", "version": "sg_start version 1.48"},
            "sas3flash": {"available": False, "path": "", "version": ""},
            "blockdev": {"available": True, "path": "/usr/sbin/blockdev", "version": ""},
            "lspci": {"available": True, "path": "/usr/bin/lspci", "version": ""},
            "lsblk": {"available": True, "path": "/usr/bin/lsblk", "version": ""},
        }
        return {
            "hba": hbas[0] if hbas else None,
            "hbas": hbas,
            "hosts": [host for hba in hbas for host in hba["host_ids"]],
            "drives": drives,
            "pci_ids_path": "/usr/share/misc/pci.ids",
            "tools": tools,
            "system": {
                "hostname": "demo-workstation",
                "kernel": "6.8.0-45-generic",
                "distro": "Ubuntu 24.04 LTS",
                "username": "demo",
                "uid": 0,
                "is_root": True,
                "can_control": True,
                "timestamp": iso_now(),
                "demo": True,
            },
            "capabilities": {
                "smart": True, "spin": True, "remove": True, "rescan": True, "root": True,
            },
            "other_controllers": [],
            "timestamp": iso_now(),
            "duration": 0.12,
        }

    def _find(self, dev: str) -> dict:
        try:
            control.validate_device(dev)
        except control.ControlError as exc:
            raise ProviderError(str(exc)) from exc
        for drive in self._drives:
            if drive["dev"] == dev:
                if drive["name"] in self._removed:
                    break
                return drive
        raise ProviderError(f"{dev} 当前不在 HBA 上，可能已经被移除")

    def smart(self, dev: str, wake: bool = False) -> dict:
        drive = self._find(dev)
        time.sleep(0.25)
        if drive["power_mode"] in ("standby", "sleep"):
            return copy.deepcopy(_standby_smart(drive["model"], drive["serial"]))
        if drive["smart"]:
            data = copy.deepcopy(drive["smart"])
            data["timestamp"] = iso_now()
            return data
        return copy.deepcopy(_standby_smart(drive["model"], drive["serial"]))

    def action(self, op: str, dev: str, params: dict | None = None) -> dict:
        params = params or {}
        if op == "rescan":
            time.sleep(0.4)
            return {
                "ok": True,
                "message": f"已重新扫描 {len(self._hbas)} 个控制器的 SCSI 总线（演示）",
                "scanned": [h for hba in self._hbas for h in hba["host_ids"]],
            }
        drive = self._find(dev)
        if op == "spin_down":
            if not drive.get("can_spin", True):
                raise ProviderError("该硬盘由 RAID/虚拟控制器管理，不能单独停转")
            if not drive["rotational"]:
                raise ProviderError("该硬盘是 SSD，无需停转")
            try:
                control.ensure_idle(drive, "停转")
            except control.ControlError as exc:
                raise ProviderError(str(exc)) from exc
            time.sleep(0.6)
            drive["power_mode"] = "standby"
            drive["smart"] = None
            return {
                "ok": True,
                "message": "已发送停转指令，硬盘已停转（演示）",
                "power_mode": "standby",
                "warning": "",
                "attempts": [{"cmd": f"sdparm --command=stop {dev}", "ok": True}],
            }
        if op == "spin_up":
            if not drive.get("can_spin", True):
                raise ProviderError("该硬盘由 RAID/虚拟控制器管理，不能单独启动")
            time.sleep(0.6)
            drive["power_mode"] = "active"
            smart = drive.get("smart")
            if not smart or smart.get("standby") or not smart.get("available"):
                drive["smart"] = self._rebuild_smart(drive)
            return {
                "ok": True,
                "message": "硬盘已启动并恢复响应（演示）",
                "power_mode": "active",
                "warning": "",
                "attempts": [{"cmd": f"sdparm --command=start {dev}", "ok": True}],
            }
        if op == "restore_phy":
            return {"ok": True, "restored": [], "failed": [],
                    "message": t("没有需要恢复的端口链路")}
        if op == "query_apm":
            protocol = (drive.get("protocol") or "").upper()
            if protocol != "SATA":
                return {
                    "dev": dev, "op": op,
                    "apm": {"supported": False, "level": None, "error": t("该硬盘不支持或已禁用 APM")},
                    "spdn_cnt": None,
                    "risk": {"risky": False, "reason": "", "level": "ok"},
                }
            # 演示数据：一块盘带低 APM（会自行起转），其余正常
            risky = dev == "/dev/sdg"
            level = 80 if risky else 254
            spdn = 6 if risky else 0
            apm = {"supported": True, "enabled": True, "level": level, "raw": "", "error": ""}
            risk = control.power_saving_risk(drive, apm=apm, spdn_cnt=spdn)
            return {"dev": dev, "op": op, "apm": apm, "spdn_cnt": spdn, "risk": risk}
        if op == "hot_remove":
            if not drive.get("can_remove", True):
                raise ProviderError("该硬盘由 RAID/虚拟控制器管理，不能单独热拔出")
            try:
                control.ensure_idle(drive, "安全移除")
            except control.ControlError as exc:
                raise ProviderError(str(exc)) from exc
            time.sleep(0.8)
            self._removed.add(drive["name"])
            return {
                "ok": True,
                "message": f"{dev} 已从系统安全移除，现在可以拔出硬盘（演示）",
                "warning": "",
                "flushed": True,
                "spin_first": bool(params.get("spin_first", True)),
                "attempts": [{"cmd": f"write 1 > /sys/block/{drive['name']}/device/delete", "ok": True}],
            }
        raise ProviderError(f"未知操作：{op}")

    def _rebuild_smart(self, drive: dict) -> dict:
        if drive["protocol"] == "SAS":
            return _smart_sas(
                drive["model"], drive["serial"], drive["firmware"], 35, 24311, 79, 0,
                drive["size_bytes"], rpm=0 if not drive["rotational"] else 7200,
            )
        if not drive["rotational"]:
            return _smart_ata(
                drive["model"], drive["serial"], drive["firmware"], 32, 3121, 20, [
                    _ata_attr(5, "Reallocated_Sector_Ct", 100, 100, 10, 0),
                    _ata_attr(9, "Power_On_Hours", 98, 98, 0, 3121),
                    _ata_attr(194, "Temperature_Celsius", 68, 55, 0, 32),
                    _ata_attr(199, "UDMA_CRC_Error_Count", 200, 200, 0, 0),
                ], passed=True, rpm=0, capacity=drive["size_bytes"],
            )
        return _smart_ata(
            drive["model"], drive["serial"], drive["firmware"], 37, 12046, 80, [
                _ata_attr(5, "Reallocated_Sector_Ct", 100, 100, 10, 0),
                _ata_attr(9, "Power_On_Hours", 98, 98, 0, 12046),
                _ata_attr(194, "Temperature_Celsius", 62, 50, 0, 37),
                _ata_attr(197, "Current_Pending_Sector", 100, 100, 0, 0),
                _ata_attr(199, "UDMA_CRC_Error_Count", 200, 200, 0, 0),
            ], passed=True, rpm=7200, capacity=drive["size_bytes"],
        )

    def reset(self) -> None:
        self._removed.clear()
        self._hbas = self._build_hbas()
        self._drives = self._build_drives()

    def close(self) -> None:
        return None
