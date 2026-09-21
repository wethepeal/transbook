"""产物命名。

**产物用书名，不用项目名。** 项目名（`doc_id`）是管理标识：要能当目录名、要稳定、
由用户随手取；而产物是给人看的文件，应该叫书名——`user-test.zh.epub` 这个文件名
读起来毫无信息量，发给别人也不知道是什么书。

书名却可能含文件系统不允许的字符，所以这里负责清洗。
"""

from __future__ import annotations

import re

from transbook.ir import DocumentIR

#: Windows 文件名里非法的字符 → 全角等价字符。
#: 直接删掉也能用，但这些是中日文书名（`Re:ゼロから始める異世界生活`），
#: 全角冒号在 Windows 上完全合法，替换比删除更保得住书名原样。
#: 斜杠与反斜杠**不替换**（全角斜杠也不该当路径分隔符引入歧义），直接删。
_SUBSTITUTE = {
    ":": "：",
    "?": "？",
    "*": "＊",
    '"': "＂",
    "<": "＜",
    ">": "＞",
    "|": "｜",
}

#: 控制字符直接删
_CONTROL = re.compile(r"[\x00-\x1f]")

#: 斜杠类：路径分隔符，绝不保留
_SLASHES = re.compile(r"[/\\]")

#: 主名长度上限。中文书名常见二三十字，80 足够；再长会把路径顶到
#: Windows 的 MAX_PATH(260) 上，尤其是产物落在深层工作目录里时。
MAX_STEM = 80


def output_stem(ir: DocumentIR) -> str:
    """产物文件名的主名：清洗过的**书名**，取不到就退回 `doc_id`。

    清洗做的事：非法字符换成全角等价字符（斜杠直接删）、把连续空白压成一个空格、
    去掉首尾空格与点（Windows 不允许文件名以点或空格结尾）、超长截断。
    结果为空（书名为空或全是非法字符）时退回 `doc_id`，保证总能得到可用的名字。
    """
    raw = (ir.doc.title or "").strip()
    stem = _SLASHES.sub("", _CONTROL.sub("", raw))
    for bad, good in _SUBSTITUTE.items():
        stem = stem.replace(bad, good)
    stem = re.sub(r"\s+", " ", stem).strip(" .")
    if not stem:
        return ir.doc.id
    return stem[:MAX_STEM].strip(" .") or ir.doc.id
