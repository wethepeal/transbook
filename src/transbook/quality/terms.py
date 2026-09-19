"""术语抽取与术语表加载。

为什么需要（计划书 §6.4）：长篇作品里人名/专有名词的译法一旦漂移，读者立刻出戏。
做法是**预扫描全书**抽出候选术语 → 人工确认译法 → 翻译时强制注入。

抽取是**启发式**（不需要词典），面向日文轻小说/一般书籍：
* **片假名连续串**（≥2 字）：外来语与人名，日文里最可靠的"专有名词"信号
* **『…』/「…」引号内短语**：技能名、称号、章节名
* **汉字连续串**（2~4 字）：人名/术语，需靠频次过滤掉普通词

输出是可编辑的 TSV（第 3 列为空，等用户填译法），填完另存为术语表即可被
`tp translate --glossary` 使用。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

KATAKANA = re.compile(r"[\u30a1-\u30f6\u30fc]{2,12}")
KANJI = re.compile(r"[\u4e00-\u9fff]{2,4}")
QUOTED = re.compile(r"[『「]([^『」]{2,14})[』」]")
HIRAGANA_ONLY = re.compile(r"^[\u3041-\u309f\u30fc]+$")

#: 常见片假名普通词（非专有名词）——只做粗过滤，宁多留勿错杀
STOP_KATAKANA = {
    "コーヒー", "テーブル", "ドア", "カード", "グループ", "システム", "プログラム", "サービス",
    "エネルギー", "イメージ", "タイミング", "パターン", "レベル", "タイプ", "スタイル", "チーム",
    "メンバー", "パーティー", "ゲーム", "バランス", "スピード", "ダメージ", "スキル", "アイテム",
    "モンスター", "キャラクター", "ストーリー", "シーン", "セリフ", "アニメ", "マンガ", "ラノベ",
    "スマホ", "パソコン", "ネット", "メール", "データ", "ファイル", "フォルダ", "ボタン",
}

#: 常见汉字普通词（不是专有名词）——避免候选表被「自分/彼女」这类词淹没
STOP_KANJI = {
    "自分", "彼女", "彼等", "彼ら", "俺", "僕", "君", "皆", "何", "誰", "今回", "場合",
    "世界", "時間", "問題", "意味", "人間", "存在", "状態", "内容", "状況", "関係",
    "感じ", "気持", "言葉", "方法", "理由", "結果", "場所", "瞬間", "表情", "視線",
    "一方", "以上", "以下", "現在", "本当", "全部", "普通", "最初", "最後", "自分達",
}


@dataclass(frozen=True)
class TermCandidate:
    term: str
    count: int
    kind: str  # katakana | kanji | quoted
    sample: str

    def as_row(self) -> str:
        return f"{self.term}\t{self.count}\t{self.kind}\t{self.sample}\t"


def extract_candidates(texts: Iterable[str], *, min_count: int = 3, top: int = 400,
                       include_kanji: bool = True) -> list[TermCandidate]:
    """从正文里抽候选术语，按出现次数降序返回。"""
    counts: Counter[str] = Counter()
    kinds: dict[str, str] = {}
    samples: dict[str, str] = {}

    def note(term: str, kind: str, ctx: str) -> None:
        if not term or HIRAGANA_ONLY.match(term):
            return
        counts[term] += 1
        kinds.setdefault(term, kind)
        samples.setdefault(term, ctx[:48])

    for raw in texts:
        text = raw or ""
        for m in KATAKANA.finditer(text):
            term = m.group(0)
            if term in STOP_KATAKANA or len(term) < 2:
                continue
            note(term, "katakana", text)
        for m in QUOTED.finditer(text):
            note(m.group(1), "quoted", text)
        if include_kanji:
            for m in KANJI.finditer(text):
                term = m.group(0)
                if term in STOP_KANJI:
                    continue
                note(term, "kanji", text)

    out = [
        TermCandidate(t, c, kinds[t], samples[t])
        for t, c in counts.items()
        if c >= min_count and not t.isdigit()
    ]
    out.sort(key=lambda x: (-x.count, x.term))
    return out[:top]


def write_candidates(path: str | Path, cands: list[TermCandidate]) -> int:
    """写成可编辑 TSV：`term / count / kind / sample / translation(待填)`。"""
    lines = [
        "# transbook 术语候选 —— 请在最后一列填入译法，删掉不需要的行，",
        "# 另存为术语表文件（如 configs/glossary.tsv），再传给：",
        "#   tp translate <workdir> --glossary configs/glossary.tsv",
        "source\tcount\tkind\tsample\ttranslation",
    ]
    lines += [c.as_row() for c in cands]
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(cands)


def load_glossary(path: str | Path | None) -> dict[str, str]:
    """加载术语表。支持三种写法，逐行解析：

    * `原文=译文`
    * `原文<TAB>译文`（也兼容我们导出的 5 列 TSV：取第 1 列与**最后一个非空列**）
    * `.json`（对象）
    """
    if not path:
        return {}
    p = Path(path)
    if not p.is_file():
        return {}
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() == ".json":
        import json

        data = json.loads(text)
        return {str(k): str(v) for k, v in data.items() if str(v).strip()}

    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "\t" in line:
            cells = [c.strip() for c in line.split("\t")]
            if cells[0] == "source":  # 我们导出的表头
                continue
            filled = [c for c in cells[1:] if c]
            if cells[0] and filled:
                out[cells[0]] = filled[-1]
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            if k.strip() and v.strip():
                out[k.strip()] = v.strip()
    return out
