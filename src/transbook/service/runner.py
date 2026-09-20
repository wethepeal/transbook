"""在**独立子进程**里执行一个作业。

为什么用子进程而不是线程：
* 状态写在 SQLite 的 `job` 表里，所以**服务重启、作业照跑**，SSE 只管轮询表；
* 重活（PDF 抽取、Typst 编译）不占服务进程，接口不会被拖住；
* 取消只需终止进程（同时把状态置为 cancelled，作业自己也会在下一个检查点退出）。

用法（由服务层自动调用，一般不用手敲）：
    python -m transbook.service.runner --db <db> --root <root> --job <id>
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from transbook.service import pipeline as P
from transbook.store import connect
from transbook.store import db as store


class Cancelled(RuntimeError):
    """作业被取消——由进度回调抛出，让流水线在检查点退出。"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _progress_cb(conn, job_id: str, stages: tuple[str, ...]):
    """把进度回调接到 `job` 表上。

    每次回调都顺手查一下状态：**取消是协作式的**——即便 API 来不及杀进程，
    作业也会在下一个检查点自己停下，不会写坏数据库。
    """
    def cb(message: str, frac: float) -> None:
        row = conn.execute("SELECT status FROM job WHERE id=?", (job_id,)).fetchone()
        if row and row["status"] == "cancelled":
            raise Cancelled("作业已取消")
        store.update_job(conn, job_id, stage=message,
                         progress=max(0.0, min(1.0, frac)))
    return cb


def dispatch(conn, root: Path, job: dict) -> dict:
    """按 `kind` 执行作业。返回可 JSON 序列化的结果摘要。"""
    kind = job["kind"]
    params = job.get("params") or {}
    doc_id = job.get("doc_id") or params.get("doc_id") or ""
    progress = _progress_cb(conn, job["id"], (kind,))

    if kind == "full":
        proj = P.project_of(root, doc_id)
        src = Path(params["source"]) if params.get("source") else proj.source_file()
        if src is None or not src.is_file():
            raise P.PipelineError("找不到输入文件（上传或 source.<ext>）")
        res = P.run_full(proj, src, engine=params.get("engine", "deepseek"),
                         model=params.get("model"), base_url=params.get("base_url"),
                         price_tier=params.get("price_tier", "peak"),
                         glossary=params.get("glossary"),
                         max_cost=float(params.get("max_cost") or 0),
                         mode=params.get("mode", "bilingual"),
                         to=params.get("to", "epub"),
                         rolling_summary=bool(params.get("rolling_summary")),
                         dry_run=bool(params.get("dry_run")), progress=progress)
        return {"extract": res.extract, "imported": res.imported,
                "translate": res.translate, "render": res.render}

    proj = P.project_of(root, doc_id)
    if kind == "extract":
        src = Path(params["source"]) if params.get("source") else proj.source_file()
        if src is None:
            raise P.PipelineError("找不到输入文件")
        progress("抽取", 0.1)
        ex = P.run_extract(proj, src, doc_id=doc_id,
                           assets=params.get("assets", True),
                           filter_headers=params.get("filter_headers", True),
                           strip_ruby=params.get("strip_ruby", True))
        return {"summary": ex.summary, "vertical": ex.vertical,
                "headers_dropped": ex.headers_dropped, "ruby_stripped": ex.ruby_stripped}
    if kind == "import":
        progress("入库", 0.5)
        return {"stats": str(P.run_import(proj))}
    if kind == "translate":
        progress("翻译", 0.05)
        rep = P.run_translate(proj, engine=params.get("engine", "deepseek"),
                              model=params.get("model"), base_url=params.get("base_url"),
                              price_tier=params.get("price_tier", "peak"),
                              glossary=params.get("glossary"),
                              limit=params.get("limit"), max_cost=float(params.get("max_cost") or 0),
                              batch_chars=int(params.get("batch_chars") or 2400),
                              batch_items=int(params.get("batch_items") or 24),
                              rolling_summary=bool(params.get("rolling_summary")),
                              dry_run=bool(params.get("dry_run")), progress=progress)
        return {"summary": rep.summary(), "translated": rep.translated,
                "failed": rep.failed, "cost": rep.usage.cost}
    if kind == "summarize":
        progress("生成摘要", 0.05)
        rep = P.run_summarize(proj, engine=params.get("engine", "deepseek"),
                              model=params.get("model"), base_url=params.get("base_url"),
                              price_tier=params.get("price_tier", "peak"),
                              budget=int(params.get("budget") or 800),
                              window=int(params.get("window") or 4),
                              force=bool(params.get("force")), progress=progress)
        return {"summary": rep.summary(), "generated": rep.generated, "cost": rep.usage.cost}
    if kind == "render":
        progress("渲染", 0.3)
        res = P.run_render(proj, mode=params.get("mode", "bilingual"),
                           to=params.get("to", "epub"),
                           out_dir=Path(params["out_dir"]) if params.get("out_dir") else None)
        return res.as_dict()
    raise P.PipelineError(f"未知作业类型：{kind}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="执行一个 transbook 后台作业")
    ap.add_argument("--db", required=True)
    ap.add_argument("--root", required=True)
    ap.add_argument("--job", required=True)
    args = ap.parse_args(argv)

    conn = connect(args.db)
    try:
        job = store.get_job(conn, args.job)
        if job is None:
            print(f"作业不存在：{args.job}", file=sys.stderr)
            return 2
        # **已被取消的作业不能再启动**：否则 worker 会用 running 把 cancelled 覆盖掉，
        # 用户点了取消却照跑（"取消排队中的作业"这个竞态实测踩过）。
        if job["status"] in store.TERMINAL_STATUS:
            return 0 if job["status"] == "cancelled" else 2
        store.update_job(conn, args.job, status="running", stage="启动中",
                         started_at=_now(), progress=0.01)
        try:
            result = dispatch(conn, Path(args.root), job)
        except Cancelled as exc:
            store.update_job(conn, args.job, status="cancelled", stage="已取消",
                             message=str(exc), finished_at=_now())
            return 0
        except Exception as exc:  # noqa: BLE001 - 任何失败都要落到作业状态里
            import traceback

            store.update_job(conn, args.job, status="failed", stage="失败",
                             error=f"{type(exc).__name__}: {exc}"[:500],
                             message=traceback.format_exc()[-1500:], finished_at=_now())
            return 1
        store.update_job(conn, args.job, status="done", stage="完成", progress=1.0,
                         result=result, finished_at=_now())
        return 0
    finally:
        conn.close()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
