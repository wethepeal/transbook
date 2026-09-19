"""文本清洗与规范化（EPUB / PDF 共用）。

核心是 `text_without_rt`：日文电子书大量使用 `<ruby>基文<rt>注音</rt></ruby>`，甚至是
逐字注音（`<ruby>幕<rt>まく</rt>間<rt>あい</rt></ruby>`）。**必须剥离 `<rt>`**，
否则假名会混进正文、标题还会与目录对不上（M0 实测：剥离前 5 处不一致，剥离后 0 处）。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

_WS = re.compile(r"[\s\u3000]+")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def localname(tag: Any) -> str:
    """取 lxml 的本地标签名。"""
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def normalize_ws(s: str) -> str:
    """归一化空白：连续空白（含全角空格）压成单个普通空格，并去首尾。"""
    return _WS.sub(" ", _CTRL.sub("", s or "")).strip()


def text_without_rt(el: Any) -> str:
    """取元素文本，跳过 `<rt>`（注音）子树；结果已归一化空白。"""
    parts: list[str] = []

    def walk(node: Any) -> None:
        if not isinstance(node.tag, str):  # 注释 / 处理指令
            return
        if localname(node.tag) == "rt":
            return
        if node.text:
            parts.append(node.text)
        for child in node:
            walk(child)
            if child.tail:
                parts.append(child.tail)

    walk(el)
    return normalize_ws("".join(parts))


def count_rt(el: Any) -> int:
    """统计元素内 `<rt>` 数量（用于报告剥离量）。"""
    return sum(1 for n in el.iter() if localname(n.tag) == "rt")


def is_blank_block(text: str) -> bool:
    """空段落判定：只有空白、或只有竖排换行用的零宽/占位字符。"""
    return normalize_ws(text) == ""


def normalize_ja(s: str) -> str:
    """日文文本规范化（保守：只做全角 ASCII → 半角，其余保持原样）。"""
    return unicodedata.normalize("NFKC", s) if False else s  # 默认关闭：NFKC 会改动书名号等


def strip_soft_hyphen(s: str) -> str:
    """去掉软连字符与零宽字符（PDF 抽取常见）。"""
    return s.replace("\u00ad", "").replace("\u200b", "").replace("\ufeff", "")
