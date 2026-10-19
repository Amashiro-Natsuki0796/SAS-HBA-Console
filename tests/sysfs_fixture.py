"""构造合成 sysfs / proc 目录，用于无需真实硬件的单元测试。"""

from __future__ import annotations

import os


def write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content if content.endswith("\n") else content + "\n")


def link(target: str, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.lexists(path):
        os.unlink(path)
    os.symlink(target, path)


def add_pci_device(
    root: str,
    bdf: str,
    vendor: str = "0x1000",
    device: str = "0x0087",
    class_code: str = "0x010700",
    driver: str = "mpt3sas",
) -> str:
    """创建与真实 sysfs 同构的 PCI 设备：/sys/devices/.../BDF 为实体，
    /sys/bus/pci/devices/BDF 为符号链接，驱动链接挂在实体目录下。"""
    real = os.path.join(root, "sys", "devices", "pci0000:00", bdf)
    write(os.path.join(real, "vendor"), vendor)
    write(os.path.join(real, "device"), device)
    write(os.path.join(real, "class"), class_code)
    write(os.path.join(real, "revision"), "0x01")
    write(os.path.join(real, "subsystem_vendor"), "0x1000")
    write(os.path.join(real, "subsystem_device"), "0x3020")
    write(os.path.join(real, "current_link_speed"), "8.0 GT/s PCIe")
    write(os.path.join(real, "current_link_width"), "8")
    write(os.path.join(real, "max_link_speed"), "8.0 GT/s PCIe")
    write(os.path.join(real, "max_link_width"), "8")
    if driver:
        driver_dir = os.path.join(root, "sys", "bus", "pci", "drivers", driver)
        os.makedirs(driver_dir, exist_ok=True)
        link(driver_dir, os.path.join(real, "driver"))
    link(real, os.path.join(root, "sys", "bus", "pci", "devices", bdf))
    return real


def build_tree(
    root: str,
    mounts: str = "/dev/sdd1 / ext4 rw,relatime 0 0\n",
    swaps: str = "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n",
    device_id: str = "0x0087",
    board_name: str = "SAS2308-8i",
    driver: str = "mpt3sas",
    class_code: str = "0x010700",
) -> str:
    pci = "0000:03:00.0"
    pci_real = add_pci_device(
        root, pci, "0x1000", device_id, class_code, driver
    )

    host_dir = os.path.join(root, "sys", "class", "scsi_host", "host0")
    write(os.path.join(host_dir, "proc_name"), driver)
    write(os.path.join(host_dir, "version_fw"), "20.00.07.00")
    write(os.path.join(host_dir, "version_bios"), "07.39.02.00")
    write(os.path.join(host_dir, "version_mpi"), "2.00.00.00")
    write(os.path.join(host_dir, "version_nvdata_persistent"), "14.00.00.00")
    write(os.path.join(host_dir, "host_sas_address"), "0x500605b00a1b2c3d")
    write(os.path.join(host_dir, "board_name"), board_name)
    write(os.path.join(host_dir, "board_assembly"), "Y3K12-04021")
    write(os.path.join(host_dir, "board_tracer"), "SP12345678")
    write(os.path.join(host_dir, "fw_queue_depth"), "1024")
    link(pci_real, os.path.join(host_dir, "device"))
    link(host_dir, os.path.join(pci_real, "host0"))
    write(os.path.join(root, "sys", "module", driver, "version"), "43.100.00.00")

    drives = [
        {
            "name": "sdb",
            "hctl": "0:0:0:0",
            "vendor": "SEAGATE",
            "model": "ST8000NM0075",
            "rev": "0004",
            "serial": "ZA1A1A1A",
            "sas_address": "0x5000c500a1b2c3d4",
            "enclosure": "0x5000c500a1b2c3d5",
            "bay": "1",
            "size": str(15628053168),
            "rotational": "1",
            "partition": False,
        },
        {
            "name": "sdc",
            "hctl": "0:0:1:0",
            "vendor": "ATA",
            "model": "WDS200T2R0A-00BH1",
            "rev": "X61100WD",
            "serial": "22118A800123",
            "sas_address": "",
            "enclosure": "",
            "bay": "0",
            "size": str(3906369332),
            "rotational": "0",
            "partition": True,
        },
        {
            "name": "sdd",
            "hctl": "0:0:2:0",
            "vendor": "ATA",
            "model": "ST4000VN008-2DR166",
            "rev": "SC60",
            "serial": "ZDH0ABCD",
            "sas_address": "",
            "enclosure": "0x5000c500a1b2c3d5",
            "bay": "3",
            "size": str(7814037168),
            "rotational": "1",
            "partition": True,
        },
    ]

    for drive in drives:
        name = drive["name"]
        scsi_device = os.path.join(
            root, "sys", "class", "scsi_device", drive["hctl"], "device"
        )
        write(os.path.join(scsi_device, "vendor"), drive["vendor"])
        write(os.path.join(scsi_device, "model"), drive["model"])
        write(os.path.join(scsi_device, "rev"), drive["rev"])
        write(os.path.join(scsi_device, "serial"), drive["serial"])
        write(os.path.join(scsi_device, "state"), "running")
        write(os.path.join(scsi_device, "queue_depth"), "32")
        write(os.path.join(scsi_device, "delete"), "1")
        write(os.path.join(scsi_device, "wwid"), "naa.5000c500a1b2c3d4")
        if drive["sas_address"]:
            write(os.path.join(scsi_device, "sas_address"), drive["sas_address"])
            write(os.path.join(scsi_device, "phy_identifier"), "0")
        if drive["enclosure"]:
            write(os.path.join(scsi_device, "enclosure_identifier"), drive["enclosure"])
            write(os.path.join(scsi_device, "bay_identifier"), drive["bay"])

        block = os.path.join(root, "sys", "block", name)
        write(os.path.join(block, "size"), drive["size"])
        write(os.path.join(block, "removable"), "0")
        write(os.path.join(block, "queue", "rotational"), drive["rotational"])
        os.makedirs(os.path.join(block, "holders"), exist_ok=True)
        link(scsi_device, os.path.join(block, "device"))
        link(block, os.path.join(scsi_device, "block", name))
        link(block, os.path.join(root, "sys", "class", "block", name))
        if drive["partition"]:
            part = os.path.join(block, f"{name}1")
            write(os.path.join(part, "partition"), "1")
            write(os.path.join(part, "size"), "100000")
            write(os.path.join(part, "dev"), f"8:{(ord(name[-1]) - ord('a') + 1) * 16 + 1}")
            link(part, os.path.join(root, "sys", "class", "block", f"{name}1"))
        if name == "sdb":
            swap_part = os.path.join(block, "sdb2")
            write(os.path.join(swap_part, "partition"), "2")
            write(os.path.join(swap_part, "size"), "16777212")

    # sdc1 挂载在 /srv/data，sdd1 为根文件系统。
    # 给 sdb 加一个 dm 上层，模拟 LVM 占用。
    dm_dir = os.path.join(root, "sys", "block", "dm-0")
    os.makedirs(os.path.join(dm_dir, "holders"), exist_ok=True)
    link(dm_dir, os.path.join(root, "sys", "block", "sdb", "holders", "dm-0"))
    write(os.path.join(root, "sys", "block", "dm-0", "dm", "name"), "vg-data")

    write(
        os.path.join(root, "proc", "mounts"),
        mounts
        + "\n".join(
            [
                "/dev/sdc1 /srv/data ext4 rw,relatime 0 0",
                "/dev/dm-0 /srv/vm xfs rw,relatime 0 0",
                "proc /proc proc rw,nosuid 0 0",
            ]
        ),
    )
    write(os.path.join(root, "proc", "swaps"), swaps + "/dev/sdb2 partition 8388604 0 -2\n")
    return root
