"""M5 服务化测试。

分三层，尽量不依赖外部进程：
1. **流水线**：直接调 `service.pipeline` 的各阶段（用最小 EPUB 夹具）；
2. **作业**：提交/取消/收尸——用注入的假 spawn，不起真进程；
3. **HTTP**：`TestClient` 打真实路由，包括 SSE 进度流。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from transbook.service import jobs as J
from transbook.service import pipeline as P
from transbook.service.api import create_app
from transbook.store import connect
from transbook.store import db as store


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "work"
    r.mkdir()
    return r


@pytest.fixture
def client(root: Path, monkeypatch) -> TestClient:
    """HTTP 客户端；`spawn` 换成假实现，测试里自己决定何时执行作业。"""
    monkeypatch.setattr(J, "spawn", lambda db, r, jid: None)
    return TestClient(create_app(root))


def epub_bytes(minimal_epub: Path) -> bytes:
    return minimal_epub.read_bytes()


def _segment_count(db: Path) -> int:
    c = connect(db)
    try:
        return c.execute("SELECT COUNT(*) n FROM segment").fetchone()["n"]
    finally:
        c.close()


# ── 流水线 ──────────────────────────────────────────────────────────
def test_pipeline_extract_import_render(root: Path, minimal_epub: Path, tmp_path: Path):
    proj = P.store_source(root, "book1", "sample.epub", epub_bytes(minimal_epub))
    ex = P.run_extract(proj, proj.source_file())
    assert ex.ir.doc.title == "测试之书" and "块" in ex.summary
    assert proj.ir_path.is_file() and proj.assets.is_dir()

    st = P.run_import(proj)
    assert st.created == _segment_count(proj.db_path)

    # 用 Fake 引擎填上译文，再渲染
    n = _segment_count(proj.db_path)
    rep = P.run_translate(proj, engine="fake")
    assert rep.translated == n
    out = P.run_render(proj, mode="zh", to="epub")
    assert out.error == "" and len(out.files) == 1
    assert out.covered == out.translatable
    assert out.files[0].is_file()


def test_pipeline_full_chain(root: Path, minimal_epub: Path):
    proj = P.store_source(root, "b2", "s.epub", epub_bytes(minimal_epub))
    seen: list[tuple[str, float]] = []
    res = P.run_full(proj, proj.source_file(), engine="fake", mode="zh", to="epub",
                     progress=lambda m, f: seen.append((m, f)))
    assert res.translate and res.render["files"]
    assert [s for s, _ in seen][:2] == ["抽取", "入库"]
    assert seen[-1][1] == 1.0


def test_progress_never_goes_backwards(root: Path, minimal_epub: Path):
    """嵌套阶段的进度必须映射进各自区间。

    不映射的话，翻译阶段会把总进度拉回 0（实测序列 2% → 0% → 92% → 100%），
    进度条在界面上会倒退。
    """
    proj = P.store_source(root, "mono", "s.epub", epub_bytes(minimal_epub))
    fracs: list[float] = []
    P.run_full(proj, proj.source_file(), engine="fake", mode="zh", to="epub",
               progress=lambda m, f: fracs.append(f))
    assert fracs == sorted(fracs), f"进度倒退：{fracs}"
    assert fracs[0] >= 0.0 and fracs[-1] == 1.0


def test_translate_progress_is_banded(root: Path, minimal_epub: Path):
    """`run_translate` 的 band 参数要真的把子进度映射进父区间。"""
    proj = P.store_source(root, "band", "s.epub", epub_bytes(minimal_epub))
    P.run_extract(proj, proj.source_file())
    P.run_import(proj)
    fracs: list[float] = []
    P.run_translate(proj, engine="fake", band=(0.30, 0.92),
                    progress=lambda m, f: fracs.append(f))
    assert fracs, "应当至少回调一次（每批一次）"
    assert all(0.30 <= f <= 0.92 for f in fracs), fracs


def test_pipeline_rejects_unsupported_format(root: Path):
    with pytest.raises(P.PipelineError):
        P.store_source(root, "x", "book.txt", b"hi")


def test_pipeline_missing_ir_raises(root: Path):
    with pytest.raises(P.PipelineError):
        P.run_import(P.project_of(root, "nope"))


def test_project_listing(root: Path, minimal_epub: Path):
    assert P.list_projects(root) == []
    proj = P.store_source(root, "b3", "s.epub", epub_bytes(minimal_epub))
    P.run_extract(proj, proj.source_file())
    got = P.list_projects(root)
    assert [p.doc_id for p in got] == ["b3"]


# ── 作业编排 ────────────────────────────────────────────────────────
def test_submit_creates_queued_job(root: Path, tmp_path: Path):
    conn = connect(tmp_path / "svc.db")
    jid = J.submit(conn, kind="translate", doc_id="d", params={"engine": "fake"},
                   db_path=tmp_path / "svc.db", root=root, spawn_worker=lambda *a: 4242)
    job = store.get_job(conn, jid)
    assert job["status"] == "queued" and job["pid"] == 4242
    assert job["params"] == {"engine": "fake"}
    conn.close()


def test_submit_rejects_unknown_kind(root: Path, tmp_path: Path):
    conn = connect(tmp_path / "svc.db")
    with pytest.raises(ValueError):
        J.submit(conn, kind="rm-rf", doc_id="d", params={},
                 db_path=tmp_path / "svc.db", root=root, spawn_worker=lambda *a: 1)
    conn.close()


def test_submit_marks_failed_when_spawn_fails(root: Path, tmp_path: Path):
    """进程起不来也必须留下记录，不能"提交了但什么都没发生"。"""
    conn = connect(tmp_path / "svc.db")
    jid = J.submit(conn, kind="translate", doc_id="d", params={},
                   db_path=tmp_path / "svc.db", root=root, spawn_worker=lambda *a: None)
    job = store.get_job(conn, jid)
    assert job["status"] == "failed" and "无法启动" in job["error"]
    conn.close()


def test_cancel_is_idempotent_and_sets_status(root: Path, tmp_path: Path):
    conn = connect(tmp_path / "svc.db")
    killed: list[int] = []
    jid = J.submit(conn, kind="translate", doc_id="d", params={},
                   db_path=tmp_path / "svc.db", root=root, spawn_worker=lambda *a: 999)
    assert J.cancel(conn, jid, kill=killed.append) is True
    assert killed == [999]
    assert store.get_job(conn, jid)["status"] == "cancelled"
    assert J.cancel(conn, jid, kill=killed.append) is False, "终态作业再取消应返回 False"
    assert J.cancel(conn, "no-such-job") is False
    conn.close()


def test_reap_stale_marks_dead_running_jobs(root: Path, tmp_path: Path):
    """服务重启后 `running` 会变成僵尸；不清理 SSE 会一直挂着等。"""
    conn = connect(tmp_path / "svc.db")
    jid = J.submit(conn, kind="translate", doc_id="d", params={},
                   db_path=tmp_path / "svc.db", root=root, spawn_worker=lambda *a: 12345)
    store.update_job(conn, jid, status="running", pid=12345)
    assert J.reap_stale(conn, alive=lambda pid: False) == 1
    job = store.get_job(conn, jid)
    assert job["status"] == "failed" and "已不存在" in job["error"]
    # 还活着的不能被收尸
    jid2 = J.submit(conn, kind="translate", doc_id="d", params={},
                    db_path=tmp_path / "svc.db", root=root, spawn_worker=lambda *a: 222)
    store.update_job(conn, jid2, status="running", pid=222)
    assert J.reap_stale(conn, alive=lambda pid: True) == 0
    conn.close()


# ── runner（同步执行）────────────────────────────────────────────────
def test_runner_dispatch_full_updates_job(root: Path, minimal_epub: Path, tmp_path: Path):
    from transbook.service import runner

    P.store_source(root, "rb", "s.epub", epub_bytes(minimal_epub))
    db = tmp_path / "svc.db"
    conn = connect(db)
    jid = store.create_job(conn, "full", doc_id="rb",
                           params={"engine": "fake", "mode": "zh", "to": "epub"})
    conn.close()

    rc = runner.main(["--db", str(db), "--root", str(root), "--job", jid])
    assert rc == 0
    conn = connect(db)
    job = store.get_job(conn, jid)
    conn.close()
    assert job["status"] == "done" and job["progress"] == 1.0
    assert job["result"]["render"]["files"]


def test_runner_reports_failure(root: Path, tmp_path: Path):
    from transbook.service import runner

    db = tmp_path / "svc.db"
    conn = connect(db)
    jid = store.create_job(conn, "extract", doc_id="missing")
    conn.close()
    rc = runner.main(["--db", str(db), "--root", str(root), "--job", jid])
    assert rc == 1
    conn = connect(db)
    job = store.get_job(conn, jid)
    conn.close()
    assert job["status"] == "failed" and job["error"]


def test_runner_exits_on_cancelled(root: Path, minimal_epub: Path, tmp_path: Path):
    """取消是协作式的：作业在下一个进度检查点自己退出。"""
    from transbook.service import runner

    P.store_source(root, "cb", "s.epub", epub_bytes(minimal_epub))
    P.run_extract(P.project_of(root, "cb"), P.project_of(root, "cb").source_file())
    db = tmp_path / "svc.db"
    conn = connect(db)
    jid = store.create_job(conn, "import", doc_id="cb")
    store.update_job(conn, jid, status="cancelled")
    conn.close()
    rc = runner.main(["--db", str(db), "--root", str(root), "--job", jid])
    assert rc == 0  # 已取消不算失败
    conn = connect(db)
    assert store.get_job(conn, jid)["status"] == "cancelled"
    conn.close()


# ── HTTP ────────────────────────────────────────────────────────────
def test_health_and_empty_projects(client: TestClient, root: Path):
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.get("/api/projects").json() == []


def test_upload_creates_project_and_job(client: TestClient, root: Path, minimal_epub: Path):
    r = client.post("/api/books",
                    files={"file": ("sample.epub", epub_bytes(minimal_epub),
                                    "application/epub+zip")},
                    data={"doc_id": "up1", "translate": "true", "engine": "fake"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["doc_id"] == "up1" and body["job_id"]
    assert (root / "up1" / "source.epub").is_file()
    job = client.get(f"/api/jobs/{body['job_id']}").json()
    assert job["status"] in ("queued", "failed")  # spawn 被测试替换成了空操作
    assert job["kind"] == "full"


def test_upload_rejects_bad_format(client: TestClient):
    r = client.post("/api/books", files={"file": ("a.txt", b"hi", "text/plain")})
    assert r.status_code == 400


def test_unknown_project_is_404(client: TestClient):
    assert client.get("/api/books/nope").status_code == 404
    assert client.get("/api/jobs/nope").status_code == 404


def test_project_detail_and_segments(root: Path, minimal_epub: Path, monkeypatch):
    monkeypatch.setattr(J, "spawn", lambda db, r, jid: None)
    client = TestClient(create_app(root))
    proj = P.store_source(root, "det", "s.epub", epub_bytes(minimal_epub))
    P.run_extract(proj, proj.source_file())
    P.run_import(proj)
    P.run_translate(proj, engine="fake")

    detail = client.get("/api/books/det").json()
    assert detail["ir"]["title"] == "测试之书"
    assert detail["stats"]["done"] == detail["stats"]["segments"]

    segs = client.get("/api/books/det/segments", params={"limit": 2}).json()
    assert segs["total"] == detail["stats"]["segments"] and len(segs["items"]) == 2
    first = segs["items"][0]
    assert {"seg_id", "source_text", "translation", "status"} <= set(first)

    found = client.get("/api/books/det/segments", params={"q": "最初"}).json()
    assert found["total"] >= 1


def test_qa_endpoint(root: Path, minimal_epub: Path, monkeypatch):
    monkeypatch.setattr(J, "spawn", lambda db, r, jid: None)
    client = TestClient(create_app(root))
    proj = P.store_source(root, "qa1", "s.epub", epub_bytes(minimal_epub))
    P.run_extract(proj, proj.source_file())
    P.run_import(proj)
    P.run_translate(proj, engine="fake")
    body = client.get("/api/books/qa1/qa").json()
    assert "summary" in body and "counts" in body


def test_review_roundtrip_over_http(root: Path, minimal_epub: Path, monkeypatch):
    """验收项：能用 HTTP 交审核（导出 TSV → 改一行 → 回灌）。"""
    monkeypatch.setattr(J, "spawn", lambda db, r, jid: None)
    client = TestClient(create_app(root))
    proj = P.store_source(root, "rv", "s.epub", epub_bytes(minimal_epub))
    P.run_extract(proj, proj.source_file())
    P.run_import(proj)
    P.run_translate(proj, engine="fake")

    tsv = client.get("/api/books/rv/review.tsv").text
    lines = tsv.splitlines()
    # 前三行是注释、第 4 行是表头，**第 5 行才是第一条数据**
    assert lines[0].startswith("#") and "seg_id" in lines[3]
    seg_id = lines[4].split("\t")[0]
    for i, line in enumerate(lines):
        if line.startswith(seg_id + "\t"):
            c = line.split("\t")
            c[3] = "人工定稿在这里"
            lines[i] = "\t".join(c)
            break
    body = client.post("/api/books/rv/review", json={"tsv": "\n".join(lines)})
    assert body.status_code == 200, body.text
    applied = body.json()
    assert applied["updated"] == 1, applied

    conn = connect(proj.db_path)
    row = conn.execute("SELECT final_translation FROM segment WHERE seg_id=?",
                       (seg_id,)).fetchone()
    conn.close()
    assert row["final_translation"] == "人工定稿在这里"


def test_submit_job_over_http(root: Path, minimal_epub: Path, monkeypatch):
    monkeypatch.setattr(J, "spawn", lambda db, r, jid: None)
    client = TestClient(create_app(root))
    proj = P.store_source(root, "sj", "s.epub", epub_bytes(minimal_epub))
    P.run_extract(proj, proj.source_file())
    r = client.post("/api/books/sj/jobs", json={"kind": "render",
                                                "params": {"mode": "zh", "to": "epub"}})
    assert r.status_code == 201 and r.json()["job_id"]
    bad = client.post("/api/books/sj/jobs", json={"kind": "nope"})
    assert bad.status_code == 400


# ── SSE ─────────────────────────────────────────────────────────────
def test_sse_streams_progress_and_ends(root: Path, monkeypatch):
    """验收项：查进度（SSE 实时推送）。"""
    db = root / "service.db"
    jid = "fixedjob0001"
    conn = connect(db)
    store.create_job(conn, "translate", doc_id="d", job_id=jid)
    store.update_job(conn, jid, status="done", stage="完成", progress=1.0,
                     result={"summary": "ok"})
    conn.close()

    client = TestClient(create_app(root))
    with client.stream("GET", f"/api/jobs/{jid}/events") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        raw = "".join(resp.iter_text())
    events = [json.loads(line[6:]) for line in raw.splitlines()
              if line.startswith("data: ")]
    snaps = [e for e in events if "status" in e]   # 结束事件不带 status
    assert snaps and snaps[-1]["status"] == "done"
    assert snaps[-1]["progress"] == 1.0
    assert "event: end" in raw, "终态后必须发结束事件，否则客户端一直挂着"


def test_sse_missing_job_ends_immediately(root: Path):
    client = TestClient(create_app(root))
    with client.stream("GET", "/api/jobs/ghost/events") as resp:
        raw = "".join(resp.iter_text())
    assert "not found" in raw
