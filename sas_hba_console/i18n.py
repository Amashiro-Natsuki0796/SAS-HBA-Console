"""界面国际化：中文（源语言）与英文。

源码中的用户可见文案统一写成中文，并用 :func:`t` 包裹；
运行时根据语言设置查表替换为英文，找不到译文时原样返回中文。

    t("刷新")                      -> "Refresh"
    t("已刷新 {0} 块硬盘", n)        -> "Refreshed {0} drive(s)"

占位符使用 ``str.format`` 语法，因此可以安全地包含 ``{}`` 以外的任意字符。
"""

from __future__ import annotations

import os
from typing import Any

from .translations import TRANSLATIONS

LANGUAGES = ("zh_CN", "en_US")
LANGUAGE_LABELS = {
    "zh_CN": "简体中文",
    "en_US": "English",
}

_current = "zh_CN"


def normalize(language: str | None) -> str:
    """把各种写法归一为 zh_CN / en_US。"""
    if not language:
        return ""
    text = str(language).strip().lower().replace("-", "_")
    if text in ("zh", "zh_cn", "zh_hans", "cn", "chinese", "中文", "简体中文"):
        return "zh_CN"
    if text in ("en", "en_us", "en_gb", "english", "英文", "英语"):
        return "en_US"
    return ""


def detect_system_language() -> str:
    """按环境变量猜测系统语言；默认中文。"""
    for name in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        value = os.environ.get(name)
        if not value:
            continue
        candidate = normalize(value.split(":")[0].split(".")[0])
        if candidate:
            return candidate
    return "zh_CN"


def set_language(language: str | None, strict: bool = False) -> str:
    """设置当前语言。传入 auto/system 时按系统环境判断。"""
    global _current
    if language in ("auto", "system", "", None):
        _current = detect_system_language()
        return _current
    resolved = normalize(language)
    if not resolved:
        if strict:
            raise ValueError(f"不支持的语言：{language!r}")
        return _current
    _current = resolved
    return _current


def get_language() -> str:
    return _current


def is_english() -> bool:
    return _current == "en_US"


def t(message: str, *args: Any, **kwargs: Any) -> str:
    """翻译文案并填充占位符。

    中文模式下直接返回原文（避免无意义的查表），
    但占位符仍会按 ``str.format`` 填充。
    """
    text = message
    if _current != "zh_CN":
        translated = TRANSLATIONS.get(message)
        if translated is not None:
            text = translated
    if not args and not kwargs:
        return text
    try:
        return text.format(*args, **kwargs)
    except (IndexError, KeyError, ValueError):
        # 译文占位符与原文不一致时退回原文，保证不崩、不吞信息。
        try:
            return message.format(*args, **kwargs)
        except (IndexError, KeyError, ValueError):
            return text


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    """英文单复数辅助；中文只用 singular。"""
    if _current != "en_US":
        return singular
    if count == 1:
        return singular
    return plural_form or f"{singular}s"


def missing_translations(messages: list[str]) -> list[str]:
    """诊断用：返回缺少英文译文的模板。"""
    if _current == "zh_CN":
        return []
    return [m for m in messages if m not in TRANSLATIONS]
