"""审核回流：导出可编辑校对文件 → 人工修改 → 按 `seg_id` 回灌。

对应计划书 §6.6 与用户要求（Q5）：**先出双语版审核，通过后出纯中文终版**。

设计要点：
* **不直接改 HTML/PDF**——那会错位、难回灌。改的是 **TSV + `seg_id`**，由程序回灌。
* 回灌只写 `final_translation`（定稿），**绝不覆盖 `translation`（机翻留痕）**，
  因此随时可以"清空定稿回到机翻"（计划书 §14.5 的回滚矩阵）。
* 回灌前**自动备份数据库**：人工输入是不可重建的，必须留底。
* 变更检测靠**与库中现值比对**：只写真正被改过的行，未动的行不受影响。
"""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

COLS = ("seg_id", "status", "source", "translation")
_ESCAPES = (("\\", "\\\\"), ("\t", "\\t"), ("\n", "\\n"), ("\r", "\\r"))


def _esc(s: str) -> str:
    out = s or ""
    for a, b in _ESCAPES:
        out = out.replace(a, b)
    return out


def _unesc(s: str) -> str:
    out = s or ""
    for a, b in reversed(_ESCAPES):
        out = out.replace(b, a)
    return out


@dataclass
class ApplyStats:
    updated: int = 0
    unchanged: int = 0
    unknown: int = 0
    blank: int = 0
    total: int = 0
    backup: Path | None = None

    def summary(self) -> str:
        parts = [f"共 {self.total} 行", f"采纳修改 {self.updated}", f"未变 {self.unchanged}",
                 f"未知 seg_id {self.unknown}"]
        if self.blank:
            parts.append(f"空白跳过 {self.blank}")
        if self.backup:
            parts.append(f"备份 {self.backup.name}")
        return " ｜ ".join(parts)


def _rows(conn: sqlite3.Connection, doc_id: str | None, only_translated: bool):
    sql = ("SELECT seg_id, status, kind, source_text, translation, final_translation "
           "FROM segment")
    where, args = [], []
    if doc_id:
        where.append("doc_id = ?")
        args.append(doc_id)
    if only_translated:
        where.append("COALESCE(final_translation, translation) IS NOT NULL")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY doc_id, ord"
    return list(conn.execute(sql, args))


def export_tsv(conn: sqlite3.Connection, path: str | Path, *, doc_id: str | None = None,
               only_translated: bool = False) -> int:
    """导出 TSV。**只改 `translation` 列**，保存后交给 `tp apply-review` 回灌。"""
    rows = _rows(conn, doc_id, only_translated)
    lines = [
        "# transbook 校对文件 —— 只需修改第 4 列（translation），保存后运行：",
        "#   tp apply-review <workdir> " + Path(path).name,
        "# 未修改的行会被原样保留；留空的行会被跳过（不会删掉已有译文）。",
        "\t".join(COLS),
    ]
    for r in rows:
        current = r["final_translation"] or r["translation"] or ""
        lines.append("\t".join([_esc(r["seg_id"]), r["status"], _esc(r["source_text"]),
                                _esc(current)]))
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(rows)


def export_markdown(conn: sqlite3.Connection, path: str | Path, *, doc_id: str | None = None,
                    only_translated: bool = False) -> int:
    """导出**只读**双语 Markdown（便于通读审阅；要改译文请用 TSV）。"""
    rows = _rows(conn, doc_id, only_translated)
    out: list[str] = [f"# 校对稿（只读）· 共 {len(rows)} 段", "",
                      "> 修改译文请导出 TSV：`tp export-review <workdir> --format tsv`", ""]
    for r in rows:
        cur = r["final_translation"] or r["translation"] or ""
        out.append(f"### `{r['seg_id']}` · {r['status']}")
        out.append("")
        out.append(f"> {r['source_text']}")
        out.append("")
        out.append(cur or "_（未译）_")
        out.append("")
    Path(path).write_text("\n".join(out), encoding="utf-8")
    return len(rows)


def apply_tsv(conn: sqlite3.Connection, path: str | Path, *,
              db_path: str | Path | None = None, backup: bool = True) -> ApplyStats:
    """按 `seg_id` 回灌校对结果，只写被修改过的行。"""
    stats = ApplyStats()
    src = Path(path)
    if not src.is_file():
        raise FileNotFoundError(f"校对文件不存在：{src}")

    if backup and db_path:
        src_db = Path(db_path)
        if src_db.is_file():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            dst = src_db.with_suffix(src_db.suffix + f".bak-{stamp}")
            shutil.copy2(src_db, dst)
            stats.backup = dst

    for raw in src.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        cells = raw.split("\t")
        if cells[0] == COLS[0]:  # 表头
            continue
        if len(cells) < 4:
            stats.unknown += 1
            continue
        seg_id = _unesc(cells[0])
        new_text = _unesc(cells[3])
        stats.total += 1
        row = conn.execute(
            "SELECT translation, final_translation FROM segment WHERE seg_id=?", (seg_id,)
        ).fetchone()
        if row is None:
            stats.unknown += 1
            continue
        current = row["final_translation"] or row["translation"] or ""
        if new_text == current:
            stats.unchanged += 1
            continue
        if not new_text.strip():
            stats.blank += 1
            continue
        conn.execute(
            "UPDATE segment SET final_translation=?, status='reviewed', "
            "updated_at=datetime('now') WHERE seg_id=?",
            (new_text, seg_id),
        )
        stats.updated += 1
    conn.commit()
    return stats


def clear_final(conn: sqlite3.Connection, doc_id: str | None = None) -> int:
    """清空定稿（回滚到机翻）——计划书 §14.5 的单段级回滚手段。"""
    sql = "UPDATE segment SET final_translation=NULL, status='done' WHERE final_translation IS NOT NULL"
    args: tuple = ()
    if doc_id:
        sql += " AND doc_id=?"
        args = (doc_id,)
    cur = conn.execute(sql, args)
    conn.commit()
    return cur.rowcount
