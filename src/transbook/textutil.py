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


# ── 英文 PDF 清洗（M2）──────────────────────────────────────────────
#: 拉丁连字 + 长 s。PDF 里 Type1 字体常把 fi/fl 抽成单码位（ﬁ/ﬂ），
#: 直接送去翻译会让模型看到 `eﬃcient` 这种词，影响术语与质量检查。
_LIGATURES = str.maketrans({
    "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi",
    "\ufb04": "ffl", "\ufb05": "st", "\ufb06": "st", "\u017f": "s",
})
_LIG_RE = re.compile(r"[\ufb00-\ufb06\u017f]")

#: 行尾连字符的三种写法：ASCII `-`、U+2010、U+2011，外加软连字符 U+00AD
_TRAIL_HYPHEN = re.compile(r"[-\u2010\u2011\u00ad]$")

#: CJK 及全角标点：用于决定换行拼接处要不要补空格
_CJK = re.compile(
    r"[\u2e80-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff"
    r"\uf900-\ufaff\ufe30-\ufe4f\uff00-\uff60\uffe0-\uffe6]"
)


def is_cjk(ch: str) -> bool:
    """该字符是否属于中日文（含全角标点）——决定拼接时是否需要空格。"""
    return bool(ch) and bool(_CJK.match(ch))


def normalize_ligatures(s: str) -> str:
    """连字还原：`ﬁ` → `fi`、`ﬂ` → `fl`、长 s `ſ` → `s`。

    不用 NFKC 整体规范化——那会把日文的全角标点（`！` `（）`）压成半角，
    破坏日文排版约定；这里只动拉丁连字这一段。
    """
    if not s or not _LIG_RE.search(s):
        return s
    return s.translate(_LIGATURES)


def join_wrapped(a: str, b: str, *, vertical: bool = False) -> str:
    """拼接被**换行/换列**拆开的两段文本（PDF 抽行的核心原语）。

    * `vertical=True`（竖排日文）：列边界不需要任何分隔符——M0/M2 实测竖排书靠这条
      还原出 3317 段，不能改。
    * `vertical=False`（横排）：按两侧字符脚本决定
      1. 前段以连字符结尾、后段以小写字母开头 → **跨行断词**，去连字符直接粘
         （`trans-` + `lation` → `translation`）；
      2. 任一侧是 CJK → 直接粘（日文横排也不用空格，`日本` + `語` → `日本語`）；
      3. 其余（英文/数字）→ 补一个空格，否则 `the quick` + `brown` 会粘成 `the quickbrown`。
    """
    if not a:
        return b
    if not b:
        return a
    if vertical:
        return a + b
    if len(a) > 1 and _TRAIL_HYPHEN.search(a) and b[0].islower():
        return a[:-1] + b
    if is_cjk(a[-1]) or is_cjk(b[0]):
        return a + b
    return f"{a} {b}"


def clean_pdf_text(s: str) -> str:
    """PDF 段文本清洗：连字还原 + 空白归一（**不**去软连字符，留给 join_wrapped 判断）。"""
    return normalize_ws(normalize_ligatures(s))


def clean_paragraph(s: str) -> str:
    """整段定稿清洗：去掉行内残留的软连字符/零宽字符。"""
    return strip_soft_hyphen(normalize_ws(s))


# ── 前后附页归类（M2）───────────────────────────────────────────────
#: 类别 → 判定正则。**顺序即优先级**：`allcover` 必须先于 `cover` 判，否则会被误当封面。
MATTER_PATTERNS: tuple[tuple[str, str], ...] = (
    ("back", r"allcover|backcover|back-cover|裏表紙|裏表纸"),
    ("cover", r"(^|[/_\-])cover|hyoshi|表紙|表纸"),
    ("toc", r"(^|[/_\-])(toc|contents)|目次|もくじ"),
    ("colophon", r"colophon|okuzuke|奥付|奥附"),
    ("promo", r"bookwalker|promo|advert|広告|試し読み|chirashi"),
    ("afterword", r"afterword|あとがき|後書き|後記|解説|atsugaki"),
    ("front", r"fmatter|front[-_]?matter|titlepage|title[-_]?page|扉|口絵|くちえ|kuchie"),
)
#: 标题（NAV/正文）兜底关键词，同样顺序即优先级
MATTER_TITLES: tuple[tuple[str, str], ...] = (
    ("back", r"裏表紙|裏表纸"),
    ("cover", r"^表紙$|^表纸$|^cover$"),
    ("toc", r"^目次$|^contents$|^もくじ$|^目次・"),
    ("colophon", r"^奥付$|^奥附$|colophon"),
    ("afterword", r"あとがき|後書き|後記|^解説$"),
    ("front", r"^扉$|^口絵$|^まえがき$|^前書き$"),
)
_MATTER_RE = tuple((k, re.compile(p, re.I)) for k, p in MATTER_PATTERNS)
_MATTER_TITLE_RE = tuple((k, re.compile(p, re.I)) for k, p in MATTER_TITLES)


def classify_matter(path_hint: str = "", title: str = "") -> str:
    """把一章归入 `cover/front/toc/main/back/colophon/promo/afterword`。

    真实样书（Re:Zero 44）的文件名即已自解释：`p-cover` / `p-fmatter-00N` /
    `p-toc-001` / `p-0NN`（正文）/ `p-allcover-001` / `p-colophon[2]` / `p-bookwalker`。
    先看路径（最可靠），再退回 NAV 标题，都不命中即 `main`。
    """
    for kind, rx in _MATTER_RE:
        if path_hint and rx.search(path_hint):
            return kind
    for kind, rx in _MATTER_TITLE_RE:
        if title and rx.search(title.strip()):
            return kind
    return "main"


#: 默认不翻译的类别（版权页/广告是出版社信息，译文无意义且会污染术语表）
SKIP_MATTER = ("colophon", "promo")
