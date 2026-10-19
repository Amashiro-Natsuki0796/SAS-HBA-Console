"""校验 i18n 覆盖率：找出缺少英文译文或占位符不一致的模板。

用法::

    python3 scripts/check_i18n.py          # 报告缺失
    python3 scripts/check_i18n.py --fix-hint
"""

from __future__ import annotations

import argparse
import ast
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from sas_hba_console.translations import TRANSLATIONS  # noqa: E402

CJK = re.compile(r"[\u4e00-\u9fff]")
PLACEHOLDER = re.compile(r"\{(\d+)(?:![rsa])?(?::[^}]*)?\}")


def collect_templates() -> dict[str, str]:
    """返回 {模板: 首次出现的文件}。"""
    found: dict[str, str] = {}
    for path in glob.glob(os.path.join(ROOT, "sas_hba_console", "**", "*.py"), recursive=True):
        try:
            tree = ast.parse(open(path, encoding="utf-8").read())
        except SyntaxError:
            continue
        rel = os.path.relpath(path, ROOT)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            if node.func.id != "t" or not node.args:
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                found.setdefault(arg.value, rel)
    return found


def placeholders(text: str) -> set[int]:
    return {int(m.group(1)) for m in PLACEHOLDER.finditer(text)}


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 i18n 覆盖率")
    parser.add_argument("--fix-hint", action="store_true", help="打印缺失项模板")
    args = parser.parse_args()

    templates = collect_templates()
    cjk_keys = {k: v for k, v in templates.items() if CJK.search(k)}
    missing = {k: v for k, v in cjk_keys.items() if k not in TRANSLATIONS}
    unused = {k: v for k, v in TRANSLATIONS.items() if k not in templates}
    bad_ph = []
    for key in cjk_keys:
        translated = TRANSLATIONS.get(key)
        if translated is None:
            continue
        if placeholders(key) != placeholders(translated):
            bad_ph.append((key, translated))

    print(f"含中文的 t() 模板：{len(cjk_keys)}")
    print(f"已翻译：{len(cjk_keys) - len(missing)}")
    print(f"缺失：{len(missing)}")
    print(f"译文未使用：{len(unused)}")
    print(f"占位符不一致：{len(bad_ph)}")

    for key, where in sorted(missing.items()):
        print(f"  MISSING  {where}: {key!r}")
    for key, translated in bad_ph:
        print(f"  PLACEHOLDER  {key!r} -> {translated!r}")
    for key in sorted(unused)[:20]:
        print(f"  UNUSED   {key!r}")

    if missing or bad_ph:
        if args.fix_hint:
            print("\n把上面的 MISSING 键补进 sas_hba_console/translations.py 即可。")
        return 1
    print("\n✔ 所有界面文案都有英文译文，且占位符一致。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
