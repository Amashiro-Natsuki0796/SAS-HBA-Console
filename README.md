# SAS HBA Console

**A graphical console for SAS / SCSI / RAID controllers and hard drives**, providing convenient control over individual drive spin-down, spin-up, and safe hot removal. It also features detailed SMART information viewing, comprehensive fault checking, and more.

![Main Interface](docs/main-en.png)

## Project Structure

```text
sas_hba_console/
├── __main__.py            # CLI (GUI / --diagnose / --demo / --lang)
├── i18n.py                # Language switching runtime (t(), system language detection)
├── translations.py        # Chinese → English translation table (320 entries)
├── helper.py              # Root helper: JSON line protocol + whitelisted operations
├── util.py                # sysfs reading, command execution, formatting
├── backend/
│   ├── pci_db.py          # pci.ids parsing + board/chip model recognition
│   ├── sysfs.py           # Controller discovery, drive enumeration, occupancy check, bus rescan
│   ├── smart.py           # smartctl invocation and SMART JSON parsing
│   ├── control.py         # Spin-down / spin-up / safe hot removal
│   ├── provider.py        # Real hardware backend
│   ├── demo.py            # Three-controller demo data
│   ├── client.py          # pkexec helper client (non-blocking JSON line protocol)
│   └── backends.py        # Unified backend interface for GUI
└── ui/
    ├── app.py / window.py / dialogs.py
    ├── theme.py           # Dark/light CSS, icons
    ├── format.py          # Health assessment and display fields
    └── settings.py        # Configuration persistence (including 1.x migration)
```

## Supported Cards

The program **does not hardcode vendors or models**. The recognition order is:

1. **`board_name`** (board name exposed by mpt3sas / mpt2sas, e.g., `SAS2308-8i`) → mapped to marketing model `LSI SAS 9207-8i`
2. **System `pci.ids`** (provided by `pciutils` on Debian/Ubuntu, located at `/usr/share/misc/pci.ids`)
   → Any vendor, any new card can display its real name
3. **Built-in fallback table**: only covers common Broadcom/LSI SAS controllers (used when `pci.ids` is unavailable)

Typical models verified/built-in:

| Chip | Marketing Model | PCI ID | Type |
| --- | --- | --- | --- |
| SAS2008 | LSI SAS 9211-8i / 9211-4i | 1000:0072 | SAS HBA |
| SAS2308 | **LSI SAS 9207-8i / 8e / 4i4e** | 1000:0086 / 0087 | SAS HBA |
| SAS3008 | LSI SAS 9300-8i / 16i | 1000:0097 | SAS HBA |
| SAS3408 / 3416 | LSI SAS 9400-8i / 16i | 1000:00af / 00ac | SAS HBA |
| SAS3508 / 3516 | LSI SAS 9500-8i / 16i | 1000:00ae / 00ab | SAS HBA |
| SAS3108 | Broadcom MegaRAID 9361-8i, etc. | 1000:005d | RAID |
| SAS3316 / 3324 | MegaRAID 12G | 1000:00c5…00cf | RAID |
| SmartPQI | Adaptec/Microchip SmartRAID | 9005:028f | RAID |
| PM8001/8070 | Adaptec HBA 1100 / SmartHBA | 9005:8081 / 11f8:8070 | SAS HBA |
| C600 Series | Intel onboard SAS (isci) | 8086:1d6x | SAS HBA |
| Any other | Parsed by `pci.ids` | — | Classified by PCI class |

> **Important Correction**: `1000:0097` is actually **SAS3008 (9300 series)**; the 9207-8i uses **SAS2308 (1000:0086/0087)**.
> Version 1.x only recognized `0097` and labeled it as 9207, which would misidentify a real 9207. Version 2.0 has corrected this using board names and `pci.ids`.

RAID controllers (MegaRAID, SmartRAID, HPE Smart Array, etc.) can also be identified and display model/firmware/driver information;
logical volumes under them can view SMART, but **individual drive spin-down and hot removal are automatically disabled** (RAID firmware manages physical drives),
and the interface will explain the reason and suggest using `smartctl -d megaraid,N` or vendor tools to read physical drives.

## Installation (Ubuntu / Debian)

### Method 1: deb package (recommended)

```bash
sudo apt install ./sas-hba-console_2.0.0_all.deb
```

Dependencies will be automatically installed: `python3-gi`, `gir1.2-gtk-3.0`, `smartmontools`, `sdparm`, `sg3-utils`,
`pkexec`, `pciutils`, and polkit authentication agent (built into Ubuntu GNOME; `xfce-polkit` for Xubuntu,
`lxpolkit` for Lubuntu, `mate-polkit` for MATE).

Upgrading from 1.x: the new package `Provides/Conflicts/Replaces: lsi9207-console`, automatically replacing the old package,
preserving the `/usr/bin/lsi9207-console` compatibility command, and migrating `~/.config/lsi9207-console/settings.json`.

### Method 2: Source installation

```bash
sudo apt install python3-gi gir1.2-gtk-3.0 smartmontools sdparm sg3-utils pkexec pciutils
sudo make install          # default /usr
sudo make install PREFIX=/usr/local
```

### Method 3: Run directly from source

```bash
./scripts/sas-hba-console-dev            # Real hardware + pkexec authorization
./scripts/sas-hba-console-dev --demo     # Demo mode, no root required
```

## Usage

1. Launch "SAS HBA Storage Console" from the application menu, or run `sas-hba-console`.
2. The system prompts an authorization window; enter the current user's password (must be in the sudo/admin group).
3. The top card displays controller information; use the "Controller" dropdown to switch when multiple cards are present.
4. Select a drive on the left to view SMART; the bottom section contains **Spin Down / Spin Up / Safe Hot Remove**.
5. Top ⟳ or `F5` to refresh; the top-right ⋯ menu includes auto-refresh, bus rescan, theme, language, and diagnostic information.
6. Menu → **Language** to switch between "简体中文 / English / Follow System"; the interface rebuilds immediately after switching, keeping the current controller and drive selection. Language is saved to configuration and persists across restarts.

Command line:

```text
sas-hba-console [--demo] [--no-elevate] [--mode {auto,demo,local,readonly,privileged}]
                [--lang {auto,zh_CN,en_US}] [--sys-root DIR] [--diagnose] [--version]
```

* `--demo`: Built-in three simulated controllers (9207-8i, 9300-8i, MegaRAID 3108) with 7 drives.
* `--diagnose`: Outputs JSON diagnostics (all controllers, pci.ids paths, tool versions, permissions) without opening the GUI.
* `--lang`: Interface language, default `auto` (Chinese in Chinese environments, English otherwise); can also force `zh_CN` / `en_US`.
* `--sys-root`: Specifies root directory for reading sysfs/proc, useful for testing or rescue environments.

## Security Notes

* **Mount check**: When a drive has mounts (including `/`, `/boot`), swap, LVM/RAID/dm-crypt, the spin-down and hot-remove buttons are disabled with reasons listed.
* **Capability bits**: Individual drive power/hot-plug operations are automatically disabled for RAID controllers and virtual SCSI devices, with the backend also providing a secondary rejection.
* **Device validation**: Only accepts devices matching `^/dev/sd[a-z]+$` that remain within the sysfs tree of identified controllers; does not execute arbitrary commands or use shell.
* **Least privilege**: The GUI does not run as root; polkit policy only allows executing the helper program, which implements only six operations:
  `snapshot` / `smart` / `spin_down` / `spin_up` / `hot_remove` / `rescan`.
* **Hot removal boundaries**: Pass-through cards do not control drive bay power; this operation is a "kernel safe device removal"; whether you can physically remove the drive depends on whether the chassis tray/backplane supports hot swapping.

## Debugging and Verification

```bash
sas-hba-console --diagnose | jq '{pci_ids_path, hbas: [.hbas[] | {model, chip, kind, driver}]}'
lspci -nnk | grep -A3 -iE 'sas|raid|scsi'
lsmod | grep -E 'mpt3sas|mpt2sas|megaraid|smartpqi|hpsa|isci'
for h in /sys/class/scsi_host/host*; do echo -n "$h: "; cat $h/proc_name; done
python3 -m unittest discover -s tests -v      # 77 tests, no real HBA required
python3 scripts/check_i18n.py                 # Validates Chinese/English coverage and placeholder consistency
```