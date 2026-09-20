"""端到端验收脚本：对一本书从零跑完整流水线，记录每阶段耗时与结果。

为什么要单独写成脚本：整套流程有 8 个阶段、跨越二十分钟，用 shell 一行行敲既容易
出错（参数错位、引号、编码），也不可复现。写成脚本后，**任何人拿到同一本书都能跑出
同一组数据**，交付说明里的验收数字也就有了出处。

用法：
    python tools/acceptance_run.py "D:\\books\\某本书.epub" --work data/work/acc1 --doc-id acc1
    python tools/acceptance_run.py <书> --engine fake        # 零成本验证链路
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TP = ROOT / ".venv" / "Scripts" / "tp.exe"


def run(label: str, args: list[str], work: Path, *, tail: int = 3) -> dict:
    """跑一个阶段，把完整输出留档、只回显最后几行。"""
    t0 = time.perf_counter()
    proc = subprocess.run([str(TP), *args], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    dt = time.perf_counter() - t0
    out = (proc.stdout or "") + (proc.stderr or "")
    (work / f"_log_{label}.txt").write_text(out, encoding="utf-8")
    lines = [x for x in out.splitlines() if x.strip()]
    print(f"\n─── {label}  ({dt:.1f}s, rc={proc.returncode}) ───")
    for x in lines[-tail:]:
        print("   " + x[:150])
    if proc.returncode != 0:
        print(f"   ⚠ 退出码 {proc.returncode}，完整输出见 _log_{label}.txt")
    return {"label": label, "seconds": round(dt, 1), "rc": proc.returncode}


def main() -> int:
    ap = argparse.ArgumentParser(description="transbook 端到端验收")
    ap.add_argument("source", help="输入的 .epub / .pdf")
    ap.add_argument("--work", default="data/work/acc", help="工作目录")
    ap.add_argument("--doc-id", default=None)
    ap.add_argument("--engine", default="deepseek", choices=["deepseek", "local", "fake"])
    ap.add_argument("--model", default="deepseek-flash")
    ap.add_argument("--max-cost", type=float, default=3.0)
    ap.add_argument("--price-tier", default="idle", choices=["peak", "idle"])
    ap.add_argument("--rolling-summary", action="store_true")
    ap.add_argument("--keep", action="store_true", help="保留已有工作目录（否则清空重跑）")
    args = ap.parse_args()

    src = Path(args.source)
    if not src.is_file():
        print(f"文件不存在：{src}", file=sys.stderr)
        return 2
    work = Path(args.work)
    if work.exists() and not args.keep:
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    doc_id = args.doc_id or src.stem[:28]

    common = ["--engine", args.engine, "--model", args.model] if args.engine != "fake" else \
             ["--engine", "fake"]
    steps: list[dict] = []
    print(f"书：{src.name}\n工作目录：{work}\n引擎：{args.engine} / {args.model}"
          f"（{args.price_tier}）\n开始：{time.strftime('%H:%M:%S')}")

    steps.append(run("extract", ["extract", str(src), "-o", str(work),
                                 "--doc-id", doc_id], work, tail=2))
    steps.append(run("import", ["import", str(work)], work, tail=1))
    if args.engine != "fake":
        steps.append(run("summarize", ["summarize", str(work), *common,
                                       "--price-tier", args.price_tier], work, tail=12))
    steps.append(run("translate", ["translate", str(work), *common, "--max-cost",
                                   str(args.max_cost), "--price-tier", args.price_tier,
                                   "--batch-chars", "4000", "--batch-items", "30",
                                   *(["--rolling-summary"] if args.rolling_summary else [])],
                     work, tail=2))
    steps.append(run("qa", ["qa", str(work)], work, tail=2))
    steps.append(run("render-zh", ["render", str(work), "-m", "zh", "--to", "both"],
                     work, tail=3))
    steps.append(run("render-bilingual", ["render", str(work), "-m", "bilingual",
                                          "--to", "epub"], work, tail=1))
    steps.append(run("validate", ["validate", str(work)], work, tail=8))

    # ── 汇总 ────────────────────────────────────────────────────────
    print("\n" + "=" * 62)
    print("阶段耗时")
    for s in steps:
        flag = "" if s["rc"] == 0 else "  ← 失败"
        print(f"  {s['label']:<18}{s['seconds']:>7.1f}s{flag}")

    import sqlite3

    db = work / "translations.db"
    summary: dict = {"steps": steps}
    if db.is_file():
        c = sqlite3.connect(db)
        c.row_factory = sqlite3.Row
        summary["status"] = dict(c.execute(
            "SELECT status, COUNT(*) FROM segment GROUP BY status").fetchall())
        summary["cost"] = c.execute(
            "SELECT IFNULL(SUM(cost),0) FROM segment").fetchone()[0]
        summary["tokens"] = dict(zip(("in", "out"), c.execute(
            "SELECT IFNULL(SUM(tokens_in),0), IFNULL(SUM(tokens_out),0) "
            "FROM segment").fetchone()))
        try:
            summary["summaries"] = c.execute(
                "SELECT COUNT(*) FROM chapter_summary").fetchone()[0]
        except sqlite3.Error:
            summary["summaries"] = 0
        c.close()
        print(f"\n段落：{summary['status']}")
        print(f"花费：¥{summary['cost']:.4f} ｜ token 入 {summary['tokens']['in']:,} / "
              f"出 {summary['tokens']['out']:,} ｜ 摘要 {summary['summaries']} 章")

    ir = work / "book.ir.json"
    if ir.is_file():
        data = json.loads(ir.read_text(encoding="utf-8"))
        counts: dict = {}
        for b in data["blocks"]:
            counts[b["type"]] = counts.get(b["type"], 0) + 1
        summary["counts"] = counts
        summary["vertical"] = data["doc"]["vertical"]
        print(f"块构成：{counts} ｜ 竖排：{data['doc']['vertical']}")

    files = sorted(p.name for p in work.glob("*.epub")) + \
        sorted(p.name for p in work.glob("*.pdf"))
    for f in files:
        print(f"  产物 {f}  ({(work / f).stat().st_size / 1048576:.2f} MB)")
    summary["files"] = files

    (work / "_acceptance.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入 {work / '_acceptance.json'}")
    print(f"结束：{time.strftime('%H:%M:%S')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
