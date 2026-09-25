"""i18n 翻译层测试。

注意 tests/conftest.py 里的 autouse fixture 会把 HYPEROS_LANG 固定为 en，
所以这里验证中文路径时必须显式改回。
"""

import json
import re
from pathlib import Path

from src.utils import i18n

LOCALES_DIR = Path(i18n.__file__).resolve().parent / "locales"
PLACEHOLDER = re.compile(r"%[-#0 +]*\d*(?:\.\d+)?[diouxXeEfFgGcrsa%]")


def test_default_language_is_chinese(monkeypatch):
    """未设置环境变量时默认输出中文。"""
    monkeypatch.delenv("HYPEROS_LANG", raising=False)
    assert i18n.get_lang() == "zh"
    assert i18n.t("Cache cleared") == "缓存已清除"


def test_language_switch_to_english(monkeypatch):
    """HYPEROS_LANG=en 时原样返回源码文本。"""
    monkeypatch.setenv("HYPEROS_LANG", "en")
    assert i18n.t("Cache cleared") == "Cache cleared"


def test_unknown_key_falls_back_to_source(monkeypatch):
    """词条缺失时回退到原文，不抛异常。"""
    monkeypatch.delenv("HYPEROS_LANG", raising=False)
    assert i18n.t("Some brand new log line") == "Some brand new log line"


def test_chinese_table_placeholders_match_source():
    """每条译文的占位符数量须与原文一致，否则运行时会抛格式化异常。"""
    table = json.loads((LOCALES_DIR / "zh.json").read_text(encoding="utf-8"))
    mismatched = [
        (key, value)
        for key, value in table.items()
        if len(PLACEHOLDER.findall(key)) != len(PLACEHOLDER.findall(value))
    ]
    assert mismatched == []
