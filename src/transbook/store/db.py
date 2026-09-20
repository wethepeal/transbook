"""SQLite 存储层：段落表 = 翻译 / 审核 / 断点续跑 / 回滚 的核心。

设计要点（对应 `docs/plan.md` §5.2）：

* **`seg_id` 可读且确定**：`{doc_id}:{block_id}`。同一份 IR 反复导入得到同一批 ID，
  因此审核回灌（TSV 里的 seg_id）与断点续跑都靠它对齐。
* **`text_hash` 是 TM 键**：内容寻址。即使将来抽取逻辑变化导致 `block_id` 漂移，
  也能凭 `text_hash` 把旧译文迁移过来（`carry_over_translations`）。
* **`translation` 永不覆盖**（机翻留痕），审核定稿写 `final_translation`，渲染时优先取定稿。
  **例外**：原文本身变了（块序漂移/重新抽取）时必须清空译文并退回 `pending`，
  再由 TM 按 `text_hash` 回填——否则译文会与原文静默错配。
* **成本字段随段落记录**，便于按章/按引擎核算，并支撑 `--max-cost` 硬护栏。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from transbook.ir import DocumentIR
from transbook.textutil import normalize_ws

SCHEMA = """
CREATE TABLE IF NOT EXISTS doc (
    id            TEXT PRIMARY KEY,
    title         TEXT,
    author        TEXT,
    source_lang   TEXT,
    origin        TEXT,
    ir_schema     TEXT,
    block_count   INTEGER,
    imported_at   TEXT
);

CREATE TABLE IF NOT EXISTS segment (
    seg_id            TEXT PRIMARY KEY,
    doc_id            TEXT NOT NULL,
    block_id          TEXT NOT NULL,
    ord               INTEGER NOT NULL,
    kind              TEXT NOT NULL,           -- heading / paragraph / footnote
    source_text       TEXT NOT NULL,
    text_hash         TEXT NOT NULL,
    source_lang       TEXT,
    status            TEXT NOT NULL DEFAULT 'pending',
    translation       TEXT,                    -- 机翻，永不覆盖
    final_translation TEXT,                    -- 审核定稿
    engine            TEXT,
    model             TEXT,
    prompt_version    TEXT,
    glossary_version  TEXT,
    tokens_in         INTEGER DEFAULT 0,
    tokens_out        INTEGER DEFAULT 0,
    cost              REAL DEFAULT 0,
    attempts          INTEGER DEFAULT 0,
    last_error        TEXT,
    updated_at        TEXT
);

CREATE INDEX IF NOT EXISTS idx_segment_status ON segment(status);
CREATE INDEX IF NOT EXISTS idx_segment_hash   ON segment(text_hash);
CREATE INDEX IF NOT EXISTS idx_segment_doc    ON segment(doc_id, ord);

CREATE TABLE IF NOT EXISTS tm (
    text_hash    TEXT NOT NULL,
    source_lang  TEXT NOT NULL,
    target_lang  TEXT NOT NULL,
    translation  TEXT NOT NULL,
    engine       TEXT,
    model        TEXT,
    hits         INTEGER DEFAULT 0,
    updated_at   TEXT,
    PRIMARY KEY (text_hash, source_lang, target_lang)
);

-- 滚动摘要（M3）：`summary` 是**截至本章**的累积梗概，翻译下一章时注入，
-- 用来维持长篇的人称/称谓/伏笔一致（计划书 §6.4）。
CREATE TABLE IF NOT EXISTS chapter_summary (
    doc_id        TEXT NOT NULL,
    chapter_index INTEGER NOT NULL,
    title         TEXT,
    summary       TEXT NOT NULL,
    engine        TEXT,
    model         TEXT,
    tokens_in     INTEGER DEFAULT 0,
    tokens_out    INTEGER DEFAULT 0,
    cost          REAL DEFAULT 0,
    updated_at    TEXT,
    PRIMARY KEY (doc_id, chapter_index)
);

-- 后台作业（M5）：状态放库里，**不放在服务进程内存里**。
-- 这样作业能在独立子进程里跑、服务重启也不丢进度，SSE 只需轮询这张表。
CREATE TABLE IF NOT EXISTS job (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,          -- full / extract / translate / render / summarize
    doc_id      TEXT,
    status      TEXT NOT NULL,          -- queued / running / done / failed / cancelled
    stage       TEXT,                   -- 当前阶段（给人看的）
    progress    REAL DEFAULT 0,         -- 0~1
    message     TEXT,
    params      TEXT,                   -- JSON
    result      TEXT,                   -- JSON
    error       TEXT,
    pid         INTEGER,
    created_at  TEXT,
    started_at  TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_job_status ON job(status, created_at);
"""

TRANSLATABLE = ("heading", "paragraph", "footnote", "table")


def connect(db_path: str | Path) -> sqlite3.Connection:
    """打开（必要时创建）数据库并应用 schema。"""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    return conn


def text_hash_of(text: str) -> str:
    """TM 键：规范化空白的原文 → sha256 前 24 位。"""
    return hashlib.sha256(normalize_ws(text).encode("utf-8")).hexdigest()[:24]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class ImportStats:
    doc_id: str
    created: int
    updated: int
    skipped: int
    carried: int = 0

    def __str__(self) -> str:
        return (f"文档 {self.doc_id}：新建 {self.created} ｜ 更新 {self.updated} ｜ "
                f"跳过 {self.skipped} ｜ 迁移旧译文 {self.carried}")


def import_ir(conn: sqlite3.Connection, ir: DocumentIR, target_lang: str = "zh") -> ImportStats:
    """把 IR 的**可翻译块**导入为段落；已存在的 seg_id 只更新原文相关字段，不动译文。"""
    doc = ir.doc
    now = _now()
    conn.execute(
        "INSERT INTO doc(id,title,author,source_lang,origin,ir_schema,block_count,imported_at) "
        "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
        "title=excluded.title, author=excluded.author, source_lang=excluded.source_lang, "
        "origin=excluded.origin, ir_schema=excluded.ir_schema, block_count=excluded.block_count",
        (doc.id, doc.title, doc.author, doc.source_lang, doc.origin,
         ir.schema_version, len(ir.blocks), now),
    )

    created = updated = skipped = carried = 0
    order = 0
    for block in ir.blocks:
        if not block.is_translatable():
            skipped += 1
            continue
        # 表格按**单元格**展开成多个翻译单元；其余块就是一单元。
        # `block_id` 统一指向父块，便于渲染时把整张表还原回去。
        for uid in block.unit_ids():
            text = block.unit_text(uid)
            order += 1
            if not text.strip():
                continue
            seg_id = f"{doc.id}:{uid}"
            th = text_hash_of(text)
            row = conn.execute("SELECT source_text, status FROM segment WHERE seg_id=?",
                               (seg_id,)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO segment(seg_id,doc_id,block_id,ord,kind,source_text,text_hash,"
                    "source_lang,status,updated_at) VALUES(?,?,?,?,?,?,?,?, 'pending', ?)",
                    (seg_id, doc.id, block.id, order, block.type, text, th,
                     doc.source_lang, now),
                )
                created += 1
            elif normalize_ws(row["source_text"]) != normalize_ws(text):
                # 原文变了（抽取逻辑调整、块序漂移、或真的修正了文本）→ 旧译文对应的
                # 是**另一句话**，必须清掉并退回 pending，再由 `carry_over_translations`
                # 按**新的 text_hash** 从 TM 回填。
                # 若只改 source_text 而留着旧译文，段落的译文会与原文静默错配
                # ——实测重抽真实样书时 3372 段全部错配，且从状态上看不出来。
                conn.execute(
                    "UPDATE segment SET source_text=?, text_hash=?, ord=?, kind=?, "
                    "translation=NULL, final_translation=NULL, engine=NULL, model=NULL, "
                    "status='pending', updated_at=? WHERE seg_id=?",
                    (text, th, order, block.type, now, seg_id),
                )
                updated += 1
            # 原文未变则原样保留（含译文与状态）

    carried = carry_over_translations(conn, doc.id, target_lang)
    conn.commit()
    return ImportStats(doc.id, created, updated, skipped, carried)


def carry_over_translations(conn: sqlite3.Connection, doc_id: str, target_lang: str = "zh") -> int:
    """把 TM 里已有的译文回填到本文件的待译段落（内容相同即复用，跨书共享）。

    这是"抽取逻辑改变后译文不丢"的保险，也是第二本书省钱的来源。
    """
    rows = conn.execute(
        "SELECT s.seg_id, s.text_hash, s.source_lang FROM segment s "
        "LEFT JOIN tm ON tm.text_hash = s.text_hash AND tm.source_lang = s.source_lang "
        "                 AND tm.target_lang = ? "
        "WHERE s.doc_id = ? AND s.status = 'pending' AND tm.translation IS NOT NULL",
        (target_lang, doc_id),
    ).fetchall()
    n = 0
    for r in rows:
        tm = conn.execute(
            "SELECT translation, engine, model FROM tm WHERE text_hash=? AND source_lang=? AND target_lang=?",
            (r["text_hash"], r["source_lang"] or "", target_lang),
        ).fetchone()
        if not tm:
            continue
        conn.execute(
            "UPDATE segment SET translation=?, engine=?, model=?, status='done', updated_at=? "
            "WHERE seg_id=?",
            (tm["translation"], tm["engine"] or "tm", tm["model"], _now(), r["seg_id"]),
        )
        conn.execute("UPDATE tm SET hits = hits + 1 WHERE text_hash=? AND source_lang=? AND target_lang=?",
                     (r["text_hash"], r["source_lang"] or "", target_lang))
        n += 1
    return n


def stats(conn: sqlite3.Connection) -> dict[str, Any]:
    """进度与成本统计。"""
    out: dict[str, Any] = {}
    out["docs"] = [dict(r) for r in conn.execute("SELECT * FROM doc ORDER BY id")]
    out["by_status"] = {
        r["status"]: r["n"] for r in conn.execute(
            "SELECT status, COUNT(*) AS n FROM segment GROUP BY status ORDER BY n DESC")
    }
    row = conn.execute(
        "SELECT COUNT(*) n, SUM(CASE WHEN translation IS NOT NULL THEN 1 ELSE 0 END) done, "
        "SUM(CASE WHEN final_translation IS NOT NULL THEN 1 ELSE 0 END) reviewed, "
        "COALESCE(SUM(cost),0) cost, COALESCE(SUM(tokens_in),0) tin, "
        "COALESCE(SUM(tokens_out),0) tout FROM segment"
    ).fetchone()
    out.update(dict(row))
    out["tm_entries"] = conn.execute("SELECT COUNT(*) n FROM tm").fetchone()["n"]
    return out


def pending(conn: sqlite3.Connection, limit: int | None = None) -> list[sqlite3.Row]:
    """取待译段落（按原文顺序）。"""
    sql = ("SELECT * FROM segment WHERE status IN ('pending','failed') "
           "ORDER BY doc_id, ord" + (f" LIMIT {int(limit)}" if limit else ""))
    return list(conn.execute(sql))


def record_translation(conn: sqlite3.Connection, seg_id: str, translation: str, *,
                       engine: str = "", model: str = "", prompt_version: str = "",
                       glossary_version: str = "", tokens_in: int = 0, tokens_out: int = 0,
                       cost: float = 0.0, target_lang: str = "zh",
                       write_tm: bool = True) -> None:
    """写入一条机翻结果，并（可选）写入 TM。**不覆盖 final_translation**。"""
    now = _now()
    conn.execute(
        "UPDATE segment SET translation=?, status='done', engine=?, model=?, prompt_version=?, "
        "glossary_version=?, tokens_in=?, tokens_out=?, cost=?, attempts=attempts+1, "
        "last_error=NULL, updated_at=? WHERE seg_id=?",
        (translation, engine, model, prompt_version, glossary_version,
         tokens_in, tokens_out, cost, now, seg_id),
    )
    if write_tm and translation:
        row = conn.execute("SELECT text_hash, source_lang FROM segment WHERE seg_id=?", (seg_id,)).fetchone()
        if row:
            conn.execute(
                "INSERT INTO tm(text_hash,source_lang,target_lang,translation,engine,model,hits,updated_at) "
                "VALUES(?,?,?,?,?,?,0,?) ON CONFLICT(text_hash,source_lang,target_lang) DO UPDATE SET "
                "translation=excluded.translation, engine=excluded.engine, model=excluded.model, "
                "updated_at=excluded.updated_at",
                (row["text_hash"], row["source_lang"] or "", target_lang, translation,
                 engine, model, now),
            )
    conn.commit()


def record_failure(conn: sqlite3.Connection, seg_id: str, error: str) -> None:
    conn.execute(
        "UPDATE segment SET status='failed', attempts=attempts+1, last_error=?, updated_at=? "
        "WHERE seg_id=?",
        (error[:500], _now(), seg_id),
    )
    conn.commit()


def effective_text(seg: sqlite3.Row | dict[str, Any]) -> str:
    """渲染用译文：审核定稿优先，其次机翻。"""
    d = dict(seg)
    return d.get("final_translation") or d.get("translation") or ""


def dump_segments(conn: sqlite3.Connection, doc_id: str | None = None) -> list[dict[str, Any]]:
    """导出段落（调试/测试用）。"""
    sql = "SELECT * FROM segment"
    args: Iterable[Any] = ()
    if doc_id:
        sql += " WHERE doc_id=?"
        args = (doc_id,)
    sql += " ORDER BY doc_id, ord"
    return [dict(r) for r in conn.execute(sql, args)]


def to_json(obj: Any) -> str:  # pragma: no cover - 便捷调试
    return json.dumps(obj, ensure_ascii=False, indent=2)


# ── 滚动摘要（M3）───────────────────────────────────────────────────
def put_summary(conn: sqlite3.Connection, doc_id: str, chapter_index: int, *,
                summary: str, title: str = "", engine: str = "", model: str = "",
                tokens_in: int = 0, tokens_out: int = 0, cost: float = 0.0) -> None:
    """写入"截至第 `chapter_index` 章"的累积梗概（同章重跑覆盖）。"""
    conn.execute(
        "INSERT INTO chapter_summary(doc_id,chapter_index,title,summary,engine,model,"
        "tokens_in,tokens_out,cost,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(doc_id,chapter_index) DO UPDATE SET title=excluded.title, "
        "summary=excluded.summary, engine=excluded.engine, model=excluded.model, "
        "tokens_in=excluded.tokens_in, tokens_out=excluded.tokens_out, "
        "cost=excluded.cost, updated_at=excluded.updated_at",
        (doc_id, chapter_index, title, summary, engine, model,
         tokens_in, tokens_out, cost, _now()),
    )
    conn.commit()


def summaries(conn: sqlite3.Connection, doc_id: str) -> dict[int, str]:
    """取某本书所有章的摘要：{chapter_index: summary}。"""
    return {r["chapter_index"]: r["summary"] for r in conn.execute(
        "SELECT chapter_index, summary FROM chapter_summary WHERE doc_id=?", (doc_id,))}


def summaries_with_titles(conn: sqlite3.Connection, doc_id: str) -> dict[int, tuple[str, str]]:
    """{chapter_index: (summary, title)}——注入前情时要带上章名。"""
    return {r["chapter_index"]: (r["summary"], r["title"] or "")
            for r in conn.execute(
                "SELECT chapter_index, summary, title FROM chapter_summary WHERE doc_id=?",
                (doc_id,))}


def summary_before(conn: sqlite3.Connection, doc_id: str, chapter_index: int,
                   window: int = 4) -> str:
    """翻译第 `chapter_index` 章时应看到的**前情**：最近 `window` 章的摘要。

    只取**严格早于**本章的章——不是"第 N-1 章"，因为中间章可能还没生成摘要
    （`tp summarize` 可分批跑）。总量由"窗口 × 每章预算"确定性封顶，
    不依赖模型自觉遵守字数上限（实测累积式摘要会一章比一章长）。
    """
    rows = conn.execute(
        "SELECT chapter_index, title, summary FROM chapter_summary "
        "WHERE doc_id=? AND chapter_index<? ORDER BY chapter_index DESC LIMIT ?",
        (doc_id, chapter_index, max(1, window))).fetchall()
    if not rows:
        return ""
    parts = [f"第 {r['chapter_index']} 章「{r['title'] or '无题'}」：{r['summary']}"
             for r in reversed(rows)]
    return "前情提要：\n" + "\n".join(parts)


def summary_stats(conn: sqlite3.Connection, doc_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT COUNT(*) n, IFNULL(SUM(cost),0) cost, IFNULL(SUM(tokens_in),0) ti, "
        "IFNULL(SUM(tokens_out),0) tos FROM chapter_summary WHERE doc_id=?",
        (doc_id,)).fetchone()
    return {"chapters": row["n"], "cost": row["cost"],
            "tokens_in": row["ti"], "tokens_out": row["tos"]}


def prune_summaries(conn: sqlite3.Connection, doc_id: str,
                    keep: Iterable[int]) -> int:
    """删掉不再属于任何章的摘要行，返回删除数。

    必须做这一步：改了分章规则（比如把目录/奥付排除掉）之后，旧行会留在库里，
    而 `summary_before` 照取不误——实测旧累积摘要 2393 字被当成"奥付那章的前情"
    继续注入。摘要与分章规则必须同步。
    """
    ids = sorted(set(int(i) for i in keep))
    if ids:
        marks = ",".join("?" * len(ids))
        cur = conn.execute(
            f"DELETE FROM chapter_summary WHERE doc_id=? AND chapter_index NOT IN ({marks})",
            (doc_id, *ids))
    else:
        cur = conn.execute("DELETE FROM chapter_summary WHERE doc_id=?", (doc_id,))
    conn.commit()
    return cur.rowcount or 0


# ── 后台作业（M5）───────────────────────────────────────────────────
#: 终态：作业不会再变，SSE 可以断开
TERMINAL_STATUS = ("done", "failed", "cancelled")
#: 允许更新的列（白名单，避免拼 SQL 时被注入）
_JOB_FIELDS = frozenset({
    "status", "stage", "progress", "message", "result", "error", "pid",
    "doc_id", "started_at", "finished_at",
})


def create_job(conn: sqlite3.Connection, kind: str, *, doc_id: str = "",
               params: dict[str, Any] | None = None, job_id: str | None = None) -> str:
    """建一条排队中的作业，返回作业 id。"""
    jid = job_id or uuid.uuid4().hex[:12]
    conn.execute(
        "INSERT INTO job(id,kind,doc_id,status,stage,progress,params,created_at) "
        "VALUES(?,?,?,'queued','排队中',0,?,?)",
        (jid, kind, doc_id, json.dumps(params or {}, ensure_ascii=False), _now()),
    )
    conn.commit()
    return jid


def update_job(conn: sqlite3.Connection, job_id: str, **fields: Any) -> None:
    """更新作业字段。只接受白名单列，值里的 dict/list 自动转 JSON。"""
    bad = set(fields) - _JOB_FIELDS
    if bad:
        raise ValueError(f"不可更新的作业字段：{sorted(bad)}")
    if not fields:
        return
    sets, args = [], []
    for k, v in fields.items():
        sets.append(f"{k}=?")
        args.append(json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
    args.append(job_id)
    conn.execute(f"UPDATE job SET {', '.join(sets)} WHERE id=?", args)
    conn.commit()


def get_job(conn: sqlite3.Connection, job_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
    return _job_dict(row) if row else None


def list_jobs(conn: sqlite3.Connection, *, limit: int = 50,
              doc_id: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM job"
    args: list[Any] = []
    if doc_id:
        sql += " WHERE doc_id=?"
        args.append(doc_id)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    return [_job_dict(r) for r in conn.execute(sql, args)]


def _job_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for key in ("params", "result"):
        if d.get(key):
            try:
                d[key] = json.loads(d[key])
            except (TypeError, ValueError):
                pass
    return d
