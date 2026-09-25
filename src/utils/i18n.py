"""轻量国际化（i18n）：日志文本的中英切换。

用法::

    from src.utils.i18n import t
    logger.info(t("Detected Type: %s"), rom_type)

通过环境变量 ``HYPEROS_LANG`` 切换语言：``zh``（中文，默认）或 ``en``（英文）。
未命中词条时回退英文原文，因此缺少翻译不会导致报错。

注意：这里刻意不使用 gettext 惯例的 ``_()``，因为项目内多处把 ``_`` 当作
循环中的忽略变量（如 ``for _, name, _ in pkgutil.iter_modules(...)``），
会遮蔽同名的翻译函数。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict

LOCALES_DIR = Path(__file__).resolve().parent / "locales"
DEFAULT_LANG = "zh"

_CACHE: Dict[str, Dict[str, str]] = {}


def get_lang() -> str:
    """返回当前语言代码（zh / en）。"""
    return (os.environ.get("HYPEROS_LANG") or DEFAULT_LANG).strip().lower()


def load_table(lang: str) -> Dict[str, str]:
    """加载并缓存指定语言的词条表；文件缺失或解析失败时返回空表。"""
    if lang in _CACHE:
        return _CACHE[lang]
    table: Dict[str, str] = {}
    path = LOCALES_DIR / f"{lang}.json"
    if path.exists():
        try:
            table = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            table = {}
    _CACHE[lang] = table
    return table


def t(text: str) -> str:
    """翻译单条文本；语言为 en 或未命中词条时返回原文。"""
    lang = get_lang()
    if lang == "en":
        return text
    return load_table(lang).get(text, text)
