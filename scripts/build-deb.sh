#!/bin/sh
# 构建 Debian/Ubuntu 二进制包：dist/sas-hba-console_<版本>_all.deb
set -eu
cd "$(dirname "$0")/.."

if ! command -v dpkg-buildpackage >/dev/null 2>&1; then
    echo "缺少 dpkg-buildpackage，请先安装：apt install dpkg-dev debhelper" >&2
    exit 1
fi

rm -rf dist
mkdir -p dist
dpkg-buildpackage -us -uc -b
mv ../sas-hba-console_*.deb dist/
mv ../sas-hba-console_*.buildinfo dist/ 2>/dev/null || true
echo
echo "已生成："
ls -l dist/
