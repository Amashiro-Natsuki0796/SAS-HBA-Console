# Hardware, Kernel, and Compatibility Notes

## 1. Controller Identification

The program does not implement any vendor whitelisting. The discovery process is:

```
/sys/class/scsi_host/hostN/device -> PCI device
        │
        ├─ PCI class 0100/0104/0107/0180 ? ── Yes ──> Treated as storage controller
        │
        └─ Driver in known HBA list? ── Yes ──> Also treated as storage controller
```

Model parsing priority:

| Priority | Source | Example | Confidence |
| --- | --- | --- | --- |
| 1 | `scsi_host/hostN/board_name` | `SAS2308-8i` → `LSI SAS 9207-8i` | high |
| 2 | System `pci.ids` | `1000:0097` → `SAS3008 PCI-Express Fusion-MPT SAS-3` | medium |
| 3 | Driver name | `megaraid_sas` → `Broadcom MegaRAID Controller` | low |
| 4 | Raw ID | `0x1234:0x5678 Controller` | low |

`pci.ids` paths (in order): `/usr/share/misc/pci.ids`, `/usr/share/hwdata/pci.ids`,
`/usr/share/pci.ids`, `/var/lib/pciutils/pci.ids`. Installing `pciutils` on Ubuntu/Debian provides this.
The program automatically re-parses after file updates (cached by mtime).

## 2. Supported Chip Families

| Chip | Common Cards | Driver | Type | Spin-down/Hot-remove |
| --- | --- | --- | --- | --- |
| SAS2004/2008 | 9211-4i/8i, IBM M1015 flashed to IT | mpt2sas / mpt3sas | SAS HBA | Supported |
| SAS2116 | 9205-8i | mpt2sas | SAS HBA | Supported |
| SAS2308 | **9207-4i4e / 8i / 8e** | mpt3sas | SAS HBA | Supported |
| SAS3004/3008 | 9300-4i/8i/16i | mpt3sas | SAS HBA | Supported |
| SAS3108 | MegaRAID 9361-8i, etc. | megaraid_sas | RAID | Disabled |
| SAS3216/3224/3316/3324 | MegaRAID 12G | megaraid_sas | RAID | Disabled |
| SAS3408/3416 | 9400-8i/16i | mpt3sas | SAS HBA | Supported |
| SAS3508/3516 | 9500-8i/16i | mpt3sas | SAS HBA | Supported |
| SAS3616/3716 | 9600 series | mpt3sas | HBA/RAID | By type |
| PM8001/8070/8072 | Adaptec HBA 1100, SmartHBA | pm80xx | SAS HBA | Supported |
| SmartPQI | Adaptec SmartRAID 3154, etc. | smartpqi | RAID | Disabled |
| C600/C620 | Intel onboard SAS | isci | SAS HBA | Supported |
| HPE Smart Array | P440/P840, etc. | hpsa | RAID | Disabled |
| Any other | Classified by pci.ids | — | By class | By type |

> The correct PCI ID for 9207-8i is **1000:0086 / 1000:0087** (SAS2308).
> `1000:0097` is SAS3008 (9300 series). Version 1.x previously treated 0097 as 9207; version 2.0 has corrected this.

## 3. Kernel and Firmware

* Linux 4.19+, recommended 5.10+.
* IT firmware recommended for 9207 (P20, e.g., `20.00.07.00`); in IR mode, some SMART and power commands may be intercepted by firmware.
* IT firmware recommended for 9300/9400/9500.
* For RAID cards, keep vendor firmware; do not flash to IT.

```bash
lspci -nnk | grep -A3 -iE 'sas|raid|scsi'
lsmod | grep -E 'mpt3sas|mpt2sas|megaraid|smartpqi|hpsa|isci|pm80xx'
for h in /sys/class/scsi_host/host*; do echo -n "$h: "; cat $h/proc_name; done
```

## 4. Tools

| Software | Purpose | Required |
| --- | --- | --- |
| python3-gi + gir1.2-gtk-3.0 | Graphical interface | Yes |
| pkexec + polkit authentication agent | Root authorization at startup | Yes (unless running as root) |
| smartmontools (`smartctl`) | SMART, SATA drive spin-down/spin-up | Yes |
| sdparm | SAS drive spin-down/spin-up | Highly recommended |
| sg3-utils (`sg_start`) | SAS drive control fallback | Recommended |
| util-linux (`blockdev`) | Cache flush before hot removal | Recommended |
| pciutils (`pci.ids` + `lspci`) | Model identification, PCIe link | Highly recommended |
| sas3flash | Supplementary LSI firmware/BIOS/SAS address | Optional |

## 5. Known Limitations

1. **Physical drives on RAID cards**: The program sees logical volumes (VD), not physical drives. Physical drive SMART requires
   `smartctl -d megaraid,N /dev/sdX` or `storcli`/`arcconf`/`ssacli`. Spin-down/hot-remove is disabled on RAID.
2. **Pass-through cards do not control power**: SAS HBA only provides the link; "spin-down" is START STOP UNIT, "hot removal" is SCSI delete;
   actual power-off depends on the chassis backplane/drive cage.
3. **SAS drives may ignore spin-down**: Some enterprise SAS drives return success but continue spinning; the program will re-check and notify.
4. **SATA drives via SAS pass-through**: SMART goes through SAT translation; some bridge enclosures require `-d sat`; the program will automatically retry once.
5. **Duplicate card names**: Two cards of the same model will display the same model name; distinguish by PCI address and "controller n/N".
6. **Virtual machines**: VMware/VirtualBox LSI controllers are typically 53c1030/mptspi or PVSCSI;
   model and drives are visible, but SMART may be intercepted by the hypervisor; use `--demo` to preview.