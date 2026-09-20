"""作业编排（M5）：提交、派生工作进程、取消。

约定：
* **作业状态一律先落库**，再启动进程。这样即使进程起不来，也能看到一条
  `queued`/`failed` 记录，而不是"提交了但什么都没发生"。
* 派生用 `python -m transbook.service.runner`，**detached**：服务重启不影响已提交的作业。
* 取消是**协作式 + 兜底杀进程**：先把状态置 `cancelled`（作业在下一个检查点自己退出），
  再终止进程，避免中途写坏 SQLite。
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from transbook.store import db as store

#: 允许通过 HTTP 提交的作业类型
KINDS = ("full", "extract", "import", "translate", "summarize", "render")


def spawn(db_path: Path, root: Path, job_id: str) -> int | None:
    """派生执行进程，返回 pid；失败返回 None（作业会被标成 failed）。"""
    cmd = [sys.executable, "-m", "transbook.service.runner",
           "--db", str(db_path), "--root", str(root), "--job", job_id]
    kwargs: dict = {"cwd": str(Path.cwd())}
    if os.name == "nt":  # 让作业不受服务进程退出影响
        kwargs["creationflags"] = (subprocess.DETACHED_PROCESS
                                   | subprocess.CREATE_NEW_PROCESS_GROUP)
        kwargs["stdin"] = subprocess.DEVNULL
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
    else:  # pragma: no cover - 本项目在 Windows 上跑
        kwargs["start_new_session"] = True
        kwargs["stdin"] = subprocess.DEVNULL
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
    try:
        proc = subprocess.Popen(cmd, **kwargs)
    except OSError:
        return None
    return proc.pid


def submit(conn: sqlite3.Connection, *, kind: str, doc_id: str, params: dict,
           db_path: Path, root: Path, spawn_worker=None) -> str:
    """建作业并派生子进程。返回作业 id。

    `spawn_worker` 可注入——单元测试里换同步假实现，避免真的起进程。
    默认值写成 `None` 而不是 `spawn`：默认参数在**定义时**求值，
    写成 `spawn_worker=spawn` 会让 `monkeypatch.setattr(jobs, "spawn", ...)` 失效。
    """
    if kind not in KINDS:
        raise ValueError(f"未知作业类型 {kind}（可选 {', '.join(KINDS)}）")
    job_id = store.create_job(conn, kind, doc_id=doc_id, params=params)
    pid = (spawn_worker or spawn)(Path(db_path), Path(root), job_id)
    if pid:
        store.update_job(conn, job_id, pid=pid)
    else:
        store.update_job(conn, job_id, status="failed", stage="失败",
                         error="无法启动执行进程")
    return job_id


def cancel(conn: sqlite3.Connection, job_id: str, *, kill=None) -> bool:
    """取消作业。已终态返回 False（幂等）。"""
    job = store.get_job(conn, job_id)
    if job is None:
        return False
    if job["status"] in store.TERMINAL_STATUS:
        return False
    # 先落状态：作业在下一个进度检查点会自己退出
    store.update_job(conn, job_id, status="cancelled", stage="已取消",
                     message="用户取消")
    pid = job.get("pid")
    if pid:
        (kill or _kill)(int(pid))
    return True


def _kill(pid: int) -> None:
    """终止执行进程；进程已退出则忽略。"""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, check=False)
        else:  # pragma: no cover
            os.kill(pid, 15)
    except OSError:
        pass


def reap_stale(conn: sqlite3.Connection, *, alive=None) -> int:
    """把"标记为 running 但进程已死"的作业收尸成 failed。

    服务重启或机器断电后，`running` 会变成永远不动的僵尸状态——
    不清理的话 SSE 会一直挂着等。
    """
    alive = alive or _alive
    n = 0
    for row in conn.execute("SELECT id, pid FROM job WHERE status='running'").fetchall():
        if not row["pid"] or not alive(int(row["pid"])):
            store.update_job(conn, row["id"], status="failed", stage="中断",
                             error="执行进程已不存在（服务重启或进程被杀）",
                             finished_at=store._now())
            n += 1
    return n


def _alive(pid: int) -> bool:
    if os.name == "nt":  # pragma: no cover - 平台相关
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                             capture_output=True, text=True)
        return str(pid) in (out.stdout or "")
    try:  # pragma: no cover
        os.kill(pid, 0)
        return True
    except OSError:
        return False
