#!/bin/sh
# 便捷安装脚本：直接安装 dist 中的 .deb；没有构建时先构建。
set -eu
cd "$(dirname "$0")"

DEB=$(ls dist/sas-hba-console_*.deb 2>/dev/null | head -1 || true)
if [ -z "$DEB" ]; then
    ./scripts/build-deb.sh
    DEB=$(ls dist/sas-hba-console_*.deb 2>/dev/null | head -1 || true)
fi
if [ -z "$DEB" ]; then
    echo "未找到 .deb 包，构建失败。" >&2
    exit 1
fi

if [ "$(id -u)" -ne 0 ]; then
    echo "需要 root 权限安装，使用 sudo 重新执行…"
    exec sudo -E "$0" "$@"
fi

apt-get install -y "$DEB"
echo
echo "安装完成。可从应用菜单启动「SAS HBA 存储控制台」，或运行："
echo "  sas-hba-console            # 真实硬件（启动时弹出 root 授权）"
echo "  sas-hba-console --demo     # 演示模式"
