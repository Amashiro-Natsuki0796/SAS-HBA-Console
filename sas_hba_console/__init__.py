"""通用 SAS / SCSI / RAID 控制器与硬盘 SMART 控制台。"""

__version__ = "2.6.3"

APP_ID = "io.github.sashbaconsole"
APP_NAME = "SAS HBA 存储控制台"
APP_NAME_EN = "SAS HBA Storage Console"
HELPER_PATH = "/usr/libexec/sas-hba-helper"
# 兼容 1.x 安装路径，升级后仍可使用旧助手。
LEGACY_HELPER_PATH = "/usr/libexec/lsi9207-helper"
