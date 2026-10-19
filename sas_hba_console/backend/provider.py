"""真实硬件后端：把 sysfs / smartctl / 控制命令组合成 GUI 所需的数据。"""

from __future__ import annotations

import getpass
import os
import platform
import socket
import time

from ..i18n import t
from ..util import find_tool, iso_now, read_text
from . import control, pci_db, smart, sysfs


class ProviderError(Exception):
    """可安全展示给用户的错误。"""


def _distro_name() -> str:
    for line in read_text("/etc/os-release").splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip().strip('"')
    return platform.platform()


class LocalProvider:
    """在拥有 root 权限的进程内直接访问硬件。"""

    def __init__(
        self,
        root: str = "/",
        can_control: bool | None = None,
        runner=control.run_cmd,
        smart_reader=smart.read_smart,
        state_probe=smart.query_power_state,
    ):
        self.root = root
        self._can_control = (
            os.geteuid() == 0 if can_control is None else bool(can_control)
        )
        self.runner = runner
        self.smart_reader = smart_reader
        self.state_probe = state_probe

    @property
    def can_control(self) -> bool:
        return self._can_control

    def snapshot(self) -> dict:
        started = time.monotonic()
        hbas = sysfs.find_hbas(self.root, enrich=self._can_control)
        hosts = [host_id for hba in hbas for host_id in hba["host_ids"]]
        drives = sysfs.enumerate_drives(self.root, hosts, hbas=hbas)
        for hba in hbas:
            hba["drive_count"] = sum(
                1 for drive in drives if drive.get("hba_pci") == hba["pci_address"]
            )
        tools = smart.tool_versions()
        system = {
            "hostname": socket.gethostname(),
            "kernel": platform.release(),
            "distro": _distro_name(),
            "username": getpass.getuser(),
            "uid": os.geteuid(),
            "is_root": os.geteuid() == 0,
            "can_control": self.can_control,
            "timestamp": iso_now(),
        }
        spin_tools = any(
            tools.get(name, {}).get("available", False)
            for name in ("smartctl", "sdparm", "sg_start")
        )
        capabilities = {
            "smart": tools.get("smartctl", {}).get("available", False),
            "spin": spin_tools and any(
                hba["capabilities"].get("spin") for hba in hbas
            ),
            "remove": any(hba["capabilities"].get("remove") for hba in hbas),
            "rescan": bool(hosts),
            "root": self.can_control,
        }
        # 诊断：未识别的存储控制器也列出来，便于排查驱动/卡型问题。
        other = sysfs.pci_summary(self.root)
        return {
            "hba": hbas[0] if hbas else None,
            "hbas": hbas,
            "hosts": hosts,
            "drives": drives,
            "pci_ids_path": pci_db.pci_ids_path(),
            "sas_topology": sysfs.sas_topology(self.root),
            "disabled_phys": control.list_disabled_phy_entries(),
            "tools": tools,
            "system": system,
            "capabilities": capabilities,
            "other_controllers": other,
            "timestamp": iso_now(),
            "duration": round(time.monotonic() - started, 2),
        }

    def _lookup_drive(self, dev: str) -> dict:
        try:
            control.validate_device(dev)
        except control.ControlError as exc:
            raise ProviderError(str(exc)) from exc
        hbas = sysfs.find_hbas(self.root, enrich=False)
        hosts = [host_id for hba in hbas for host_id in hba["host_ids"]]
        for drive in sysfs.enumerate_drives(self.root, hosts, hbas=hbas):
            if drive["dev"] == dev:
                return drive
        raise ProviderError(
            f"{dev}t( 当前不在任何已识别的 HBA 上，可能已经被移除；请刷新硬盘列表)"
        )

    def smart(self, dev: str, wake: bool = False) -> dict:
        try:
            control.validate_device(dev)
        except control.ControlError as exc:
            raise ProviderError(str(exc)) from exc
        if not find_tool("smartctl"):
            return {
                "available": False,
                "error": t("未安装 smartmontools，请执行：apt install smartmontools"),
                "timestamp": iso_now(),
            }
        # 只允许读取当前确实挂在 HBA 上的盘，避免任意设备访问。
        self._lookup_drive(dev)
        return self.smart_reader(dev, wake=wake)

    def action(self, op: str, dev: str, params: dict | None = None) -> dict:
        params = params or {}
        if not self.can_control:
            raise ProviderError(t("该操作需要 root 权限（请重新授权）"))
        if op == "restore_phy":
            # 恢复链路不针对单块盘，不需要（也不能）解析设备路径
            result = control.enable_all_disabled_phys(self.root)
            if not result["restored"] and result["failed"]:
                raise ProviderError(
                    t("恢复端口链路失败：{0}", "、".join(result["failed"]))
                )
            try:
                sysfs.rescan_hosts(self.root)
            except Exception:
                pass
            return {
                "ok": True,
                "restored": result["restored"],
                "failed": result["failed"],
                "message": result["message"],
            }
        if op == "rescan":
            result = sysfs.rescan_hosts(self.root)
            if result["errors"]:
                raise ProviderError("；".join(result["errors"]))
            return {
                "ok": True,
                "message": f"t(已重新扫描 ){len(result['scanned'])}t( 条 SCSI 总线)",
                "scanned": result["scanned"],
            }

        drive = self._lookup_drive(dev)
        if op in ("spin_down", "spin_up") and not drive.get("can_spin", True):
            raise ProviderError(
                t("该硬盘由 RAID/虚拟控制器管理，不能单独停转或启动；请在控制器固件或管理工具中操作。")

            )
        if op == "hot_remove" and not drive.get("can_remove", True):
            raise ProviderError(
                t("该硬盘由 RAID/虚拟控制器管理，不能单独热拔出；请先删除逻辑卷并在固件中移除物理盘。")

            )
        if op == "spin_down":
            result = control.spin_down(
                drive, runner=self.runner, state_probe=self.state_probe
            )
        elif op == "spin_up":
            result = control.spin_up(
                drive, runner=self.runner, state_probe=self.state_probe
            )
        elif op == "query_apm":
            apm = control.read_apm(dev, runner=self.runner)
            spdn = control.read_spin_down_count(drive, self.root)
            return {
                "dev": dev,
                "apm": apm,
                "spdn_cnt": spdn,
                "risk": control.power_saving_risk(drive, apm=apm, spdn_cnt=spdn),
                "op": op,
            }
        elif op == "hot_remove":
            result = control.hot_remove(
                drive,
                sys_root=self.root,
                spin_first=bool(params.get("spin_first", True)),
                runner=self.runner,
                state_probe=self.state_probe,
                require_standby=bool(params.get("require_standby", True)),
                disable_apm_first=bool(params.get("disable_apm_first", False)),
                disconnect_phy=bool(params.get("disconnect_phy", True)),
            )
        else:
            raise ProviderError(t("未知操作：{0}", op))
        result["dev"] = dev
        result["op"] = op
        return result

    def close(self) -> None:
        return None
