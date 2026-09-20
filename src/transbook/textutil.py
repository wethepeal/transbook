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
    """整段定稿清洗：去掉行内残留的软连字符/零宽字符，并压掉 CJK 之间的空格。"""
    return squeeze_cjk_spaces(strip_soft_hyphen(normalize_ws(s)))


#: 日文语境下"两侧都不该有空格"的字符：汉字、假名、CJK 标点、全角符号、破折号/框线
#: （`──` 是 U+2015，必须单列，否则 `「── 星」` 这类空格压不掉）
_CJK_EDGE = (r"\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff"
             r"\uf900-\ufaff\ufe30-\ufe4f\uff00-\uff60\uffe0-\uffe6"
             r"\u2010-\u2015\u2025\u2026\u2500-\u257f")
_CJK_SPACE = re.compile(f"(?<=[{_CJK_EDGE}])[ \\u3000]+(?=[{_CJK_EDGE}])")


def squeeze_cjk_spaces(s: str) -> str:
    """压掉**两侧都是 CJK** 时的空格。

    PDF 的文字层常把标点/破折号拆成独立文本段，抽出来就多出空格
    （`「── 星が悪かったんだよ」`），而 EPUB 孪生版写的是 `「──星が…」`；
    剥离振假名后也会在原地留下一个空格（`愛 でないと`）。
    日文本来不在汉字/假名/标点之间加空格，所以这类空格都是抽取噪声。

    只在**两侧都属于日文书写系统**时压缩：`英語 の 混在` 这种拉丁与 CJK 之间的空格保留。
    """
    if not s or " " not in s and "\u3000" not in s:
        return s
    return _CJK_SPACE.sub("", s)


#: 平假名 + 片假名。用作"这段原文是不是真日文"的判据
KANA_ANY = re.compile(r"[\u3041-\u309f\u30a1-\u30f6]")


def is_untranslated(src: str, tgt: str, *, min_chars: int = 0) -> bool:
    """译文是否**等于原文**（即根本没翻）。

    只对**含假名**的原文成立：纯汉字/符号串（`第七章 『Reweave』`、`「────」`）
    译成同样内容是**正确**的——早期版本在这里误报过 115 条（真实数据驱动修正）。
    `min_chars` 进一步排除 `「べ」` 这类拟声片段：它们原样保留无可厚非，
    但拿它们去重试纯属浪费。

    比较时**剔除全部空白**（与 `tp qa` 同一口径）：模型把原文的换行/空格重排一遍
    也仍然是"没翻"，这不该逃过护栏。
    """
    s = _WS.sub("", src or "")
    return (bool(s) and s == _WS.sub("", tgt or "")
            and len(s) >= min_chars and bool(KANA_ANY.search(s)))


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
