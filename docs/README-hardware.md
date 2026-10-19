# 硬件、内核与兼容性说明

## 1. 控制器识别

程序不做任何厂商白名单，发现流程为：

```
/sys/class/scsi_host/hostN/device -> PCI 设备
        │
        ├─ PCI class 0100/0104/0107/0180 ? ── 是 ──> 视为存储控制器
        │
        └─ 驱动在已知 HBA 列表中？ ── 是 ──> 也视为存储控制器
```

型号解析优先级：

| 优先级 | 来源 | 示例 | 置信度 |
| --- | --- | --- | --- |
| 1 | `scsi_host/hostN/board_name` | `SAS2308-8i` → `LSI SAS 9207-8i` | high |
| 2 | 系统 `pci.ids` | `1000:0097` → `SAS3008 PCI-Express Fusion-MPT SAS-3` | medium |
| 3 | 驱动名 | `megaraid_sas` → `Broadcom MegaRAID 控制器` | low |
| 4 | 原始 ID | `0x1234:0x5678 控制器` | low |

`pci.ids` 路径（按顺序）：`/usr/share/misc/pci.ids`、`/usr/share/hwdata/pci.ids`、
`/usr/share/pci.ids`、`/var/lib/pciutils/pci.ids`。Ubuntu/Debian 安装 `pciutils` 即具备。
文件更新后程序会自动重新解析（按 mtime 缓存）。

## 2. 支持的芯片族

| 芯片 | 常见整卡 | 驱动 | 类型 | 单盘停转/热拔 |
| --- | --- | --- | --- | --- |
| SAS2004/2008 | 9211-4i/8i、IBM M1015 刷 IT | mpt2sas / mpt3sas | SAS HBA | 支持 |
| SAS2116 | 9205-8i | mpt2sas | SAS HBA | 支持 |
| SAS2308 | **9207-4i4e / 8i / 8e** | mpt3sas | SAS HBA | 支持 |
| SAS3004/3008 | 9300-4i/8i/16i | mpt3sas | SAS HBA | 支持 |
| SAS3108 | MegaRAID 9361-8i 等 | megaraid_sas | RAID | 禁用 |
| SAS3216/3224/3316/3324 | MegaRAID 12G | megaraid_sas | RAID | 禁用 |
| SAS3408/3416 | 9400-8i/16i | mpt3sas | SAS HBA | 支持 |
| SAS3508/3516 | 9500-8i/16i | mpt3sas | SAS HBA | 支持 |
| SAS3616/3716 | 9600 系列 | mpt3sas | HBA/RAID | 按类型 |
| PM8001/8070/8072 | Adaptec HBA 1100、SmartHBA | pm80xx | SAS HBA | 支持 |
| SmartPQI | Adaptec SmartRAID 3154 等 | smartpqi | RAID | 禁用 |
| C600/C620 | Intel 板载 SAS | isci | SAS HBA | 支持 |
| HPE Smart Array | P440/P840 等 | hpsa | RAID | 禁用 |
| 任意其它 | 由 pci.ids 分类 | — | 按 class | 按类型 |

> 9207-8i 的正确 PCI ID 是 **1000:0086 / 1000:0087**（SAS2308）。
> `1000:0097` 是 SAS3008（9300 系列）。1.x 版本曾把 0097 当作 9207，2.0 已修正。

## 3. 内核与固件

* Linux 4.19+，建议 5.10+。
* 9207 建议 IT 固件（P20，如 `20.00.07.00`）；IR 模式下部分 SMART 与电源指令会被固件拦截。
* 9300/9400/9500 建议 IT 固件。
* RAID 卡请保持厂商固件，不要刷 IT。

```bash
lspci -nnk | grep -A3 -iE 'sas|raid|scsi'
lsmod | grep -E 'mpt3sas|mpt2sas|megaraid|smartpqi|hpsa|isci|pm80xx'
for h in /sys/class/scsi_host/host*; do echo -n "$h: "; cat $h/proc_name; done
```

## 4. 工具

| 软件 | 用途 | 必需 |
| --- | --- | --- |
| python3-gi + gir1.2-gtk-3.0 | 图形界面 | 是 |
| pkexec + polkit 认证代理 | 启动时 root 授权 | 是（除非以 root 运行） |
| smartmontools (`smartctl`) | SMART、SATA 盘停转/启动 | 是 |
| sdparm | SAS 盘停转/启动 | 强烈建议 |
| sg3-utils (`sg_start`) | SAS 盘控制回退 | 建议 |
| util-linux (`blockdev`) | 热拔出前刷新缓存 | 建议 |
| pciutils (`pci.ids` + `lspci`) | 型号识别、PCIe 链路 | 强烈建议 |
| sas3flash | 补充 LSI 固件/BIOS/SAS 地址 | 可选 |

## 5. 已知限制

1. **RAID 卡的物理盘**：程序看到的是逻辑卷（VD），不是物理盘。物理盘 SMART 需要
   `smartctl -d megaraid,N /dev/sdX` 或 `storcli`/`arcconf`/`ssacli`。单盘停转/热拔在 RAID 上被禁用。
2. **直通卡不控制供电**：SAS HBA 只提供链路，“停转”是 START STOP UNIT，“热拔出”是 SCSI delete；
   真正断电取决于机箱背板/硬盘笼。
3. **SAS 盘可能忽略停转**：部分企业级 SAS 盘返回成功但继续转动，程序会复查并提示。
4. **SATA 盘经 SAS 直通**：SMART 走 SAT 转换，个别桥接盒需要 `-d sat`，程序会自动重试一次。
5. **多卡同名**：两张同型号卡会显示相同型号，用 PCI 地址和“控制器 n/N”区分。
6. **虚拟机**：VMware/VirtualBox 的 LSI 控制器多为 53c1030/mptspi 或 PVSCSI，
   能看到型号与硬盘，但 SMART 可能被 hypervisor 拦截；可用 `--demo` 预览。
