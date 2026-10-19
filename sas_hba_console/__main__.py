"""命令行入口。"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import APP_NAME, __version__
from .i18n import t


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sas-hba-console",
        description=t("通用 SAS / SCSI / RAID 控制器与硬盘 SMART 图形化控制台"),
    )
    parser.add_argument("--version", action="version", version=f"sas-hba-console {__version__}")
    parser.add_argument(
        "--demo",
        action="store_true",
        help=t("演示模式：使用模拟的多控制器硬件，不读取真实设备、不需要 root"),
    )
    parser.add_argument(
        "--no-elevate",
        action="store_true",
        help=t("不请求 root 授权：以当前用户只读方式运行（SMART/控制可能不可用）"),
    )
    parser.add_argument(
        "--mode",
        choices=("auto", "demo", "local", "readonly", "privileged"),
        default=None,
        help=t("强制指定后端模式（auto 为默认）"),
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help=t("输出硬件、工具与权限诊断信息（JSON），不启动图形界面"),
    )
    parser.add_argument(
        "--lang",
        choices=("auto", "zh_CN", "en_US", "zh", "en"),
        default="auto",
        help="界面语言（auto 按系统环境判断）",
    )
    parser.add_argument(
        "--sys-root",
        default="/",
        metavar="DIR",
        help=t("使用指定的根目录读取 sysfs/proc（用于测试或救援环境）"),
    )
    parser.add_argument(
        "--helper",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser


def _diagnose() -> int:
    from .backend import smart
    from .backend.provider import LocalProvider
    from .util import find_tool, iso_now

    provider = LocalProvider(can_control=os.geteuid() == 0)
    try:
        snapshot = provider.snapshot()
    except Exception as exc:  # 诊断命令不应因单个硬件异常崩溃
        snapshot = {"error": f"{type(exc).__name__}: {exc}"}
    drives = []
    for drive in snapshot.get("drives") or []:
        smart_data = drive.get("smart") or {}
        drives.append(
            {
                "dev": drive.get("dev"),
                "hctl": drive.get("hctl"),
                "model": drive.get("model"),
                "serial": drive.get("serial"),
                "size": drive.get("size_human"),
                "protocol": drive.get("protocol"),
                "power_mode": smart_data.get("power_mode") or drive.get("power_mode"),
                "mountpoints": drive.get("mountpoints"),
                "blockers": drive.get("blockers"),
                "smart_available": smart_data.get("available"),
                "smart_passed": smart_data.get("passed"),
                "error": drive.get("smart_error"),
            }
        )
    report = {
        "app": APP_NAME,
        "version": __version__,
        "generated": iso_now(),
        "uid": os.geteuid(),
        "is_root": os.geteuid() == 0,
        "display": os.environ.get("DISPLAY"),
        "wayland_display": os.environ.get("WAYLAND_DISPLAY"),
        "session": os.environ.get("XDG_SESSION_TYPE"),
        "pkexec": find_tool("pkexec"),
        "polkit_agent_running": _polkit_agent_running(),
        "hba": snapshot.get("hba"),
        "hbas": snapshot.get("hbas"),
        "other_controllers": snapshot.get("other_controllers"),
        "hosts": snapshot.get("hosts"),
        "pci_ids_path": snapshot.get("pci_ids_path"),
        "sas_topology": snapshot.get("sas_topology"),
        "tools": snapshot.get("tools") or smart.tool_versions(),
        "system": snapshot.get("system"),
        "capabilities": snapshot.get("capabilities"),
        "drives": drives,
        "snapshot_error": snapshot.get("error"),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _polkit_agent_running() -> bool:
    """粗略检测会话中是否有 polkit 认证代理在运行。"""
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open(f"/proc/{pid}/comm", "r", encoding="utf-8") as fh:
                    name = fh.read().strip().lower()
            except OSError:
                continue
            if name in ("lxpolkit", "polkit-gnome", "polkit-kde", "gnome-shell", "xfce-polkit"):
                return True
    except OSError:
        pass
    return False


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.helper:
        from .helper import main as helper_main

        return helper_main()

    if args.diagnose:
        return _diagnose()

    from . import i18n as _i18n

    _i18n.set_language(args.lang)

    if args.mode:
        mode = args.mode
    elif args.demo:
        mode = "demo"
    elif args.no_elevate:
        mode = "readonly" if os.geteuid() != 0 else "local"
    else:
        mode = "auto"

    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        print(
            t("无法启动图形界面：未检测到 DISPLAY 或 WAYLAND_DISPLAY。\n请在桌面会话中运行，或使用 --diagnose 查看硬件信息。"),

            file=sys.stderr,
        )
        return 2

    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import GLib

    GLib.set_prgname("sas-hba-console")
    GLib.set_application_name(APP_NAME)

    from .ui.app import ConsoleApp

    app = ConsoleApp(mode, sys_root=os.path.abspath(args.sys_root))
    # Gtk.Application.run 需要 argv；只传程序名，避免 argparse 参数被 GTK 再解析。
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    raise SystemExit(main())
