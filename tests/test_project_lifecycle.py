"""项目生命周期：新建 → 存在判定 → 产物命名。

覆盖两个用户实测报出来的问题：

1. **新建项目后进详情页报「项目不存在」**。上传只写了 `source.epub`，抽取是后台作业；
   而 `Project.exists()` 当时要求已有 IR 或 db，于是抽取没跑完时详情接口就 404——
   用户看到的弹窗像是"建失败了"，其实建成功了。
2. **产物用项目名命名**（`user-test.zh.epub`）。项目名是管理标识，产物是给人看的文件，
   应该叫书名（`书名.zh.epub`）。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from transbook.ir import Block, DocMeta, DocumentIR
from transbook.render.naming import output_stem
from transbook.service import jobs as J
from transbook.service import pipeline as P
from transbook.service.api import create_app
from transbook.store import connect, import_ir


def make_ir(title: str = "某本书", doc_id: str = "proj") -> DocumentIR:
    return DocumentIR(
        doc=DocMeta(id=doc_id, title=title, source_lang="ja", origin="epub"),
        blocks=[Block(id="b000001", type="paragraph", text="正文。")],
    )


# ── 产物命名 ────────────────────────────────────────────────────────
def test_output_stem_uses_book_title_not_doc_id():
    """核心诉求：产物叫书名，不叫项目名。"""
    ir = make_ir(title="Re:ゼロから始める異世界生活 43", doc_id="user-test")
    stem = output_stem(ir)
    assert "user-test" not in stem
    assert "異世界生活" in stem


def test_output_stem_replaces_illegal_chars_with_fullwidth():
    """中日文书名里的 `:` `?` 换成全角——Windows 上合法，且保住了书名原样。

    直接删掉也能用，但 `Re:ゼロ…` 会变成 `Reゼロ…`，读起来就缺了一块。
    """
    assert output_stem(make_ir(title="Re:ゼロ")) == "Re：ゼロ"
    assert output_stem(make_ir(title="誰が為の?")) == "誰が為の？"
    assert output_stem(make_ir(title='a"b<c>d|e*f')) == "a＂b＜c＞d｜e＊f"


def test_output_stem_drops_path_separators():
    """斜杠必须删掉，不能换成全角——否则等于把路径语义引进文件名。

    这里刻意用 ASCII：中日文里有大量同形字（`卷`/`巻`），写断言的字符串
    和构造数据的字符串很容易不是同一个码位，肉眼看不出来。
    """
    stem = output_stem(make_ir(title="a/b\\c"))
    assert "/" not in stem and "\\" not in stem
    assert stem == "abc"


def test_output_stem_trims_and_falls_back():
    assert output_stem(make_ir(title="  《书名》  ")) == "《书名》"
    # Windows 不允许文件名以点或空格结尾
    assert output_stem(make_ir(title="书名... ")) == "书名"
    # 书名为空 → 退回项目名，保证总能得到可用的名字
    assert output_stem(make_ir(title="", doc_id="fallback")) == "fallback"
    # 全是非法字符（斜杠被删光）→ 同样退回项目名
    assert output_stem(make_ir(title="///", doc_id="onlyslashes")) == "onlyslashes"


def test_output_stem_truncates_long_titles():
    """超长书名要截断：否则产物路径会顶到 Windows 的 MAX_PATH。"""
    assert len(output_stem(make_ir(title="书" * 200))) == 80


# ── 新建项目后不应报"项目不存在" ────────────────────────────────────
@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    # 假 spawn 必须返回一个 pid：`J.submit` 在 pid 为空时会**立刻把作业标成 failed**
    # （终态），那样"有作业在跑"就永远不成立。返回假 pid 让作业停在 queued。
    monkeypatch.setattr(J, "spawn", lambda db, r, jid: 4242)
    root = tmp_path / "work"
    root.mkdir()
    return TestClient(create_app(root, web=tmp_path / "no-web"))


def test_project_exists_right_after_upload(client: TestClient):
    """上传只落 `source.epub`，此时项目就该算"存在"。

    回归：以前 `exists()` 要求已有 IR 或 db，导致刚建完的项目在详情页 404。
    """
    r = client.post("/api/books",
                    files={"file": ("t.epub", b"PK\x03\x04fake", "application/epub+zip")},
                    data={"doc_id": "user-test", "translate": "false"})
    assert r.status_code == 201, r.text
    assert r.json()["doc_id"] == "user-test"

    # 抽取作业还没跑（spawn 被替换成空操作），但项目必须已经可见
    r = client.get("/api/books/user-test")
    assert r.status_code == 200, "刚建完的项目不该 404"
    body = r.json()
    assert body["doc_id"] == "user-test"
    assert body["has_ir"] is False
    assert body["source"] == "source.epub"
    assert "ir" not in body and "stats" not in body


def test_new_project_appears_in_list(client: TestClient):
    """刚上传的项目应立刻出现在列表里，否则用户以为没建成功。"""
    client.post("/api/books",
                files={"file": ("t.epub", b"PK\x03\x04fake", "application/epub+zip")},
                data={"doc_id": "brand-new", "translate": "false"})
    ids = [p["doc_id"] for p in client.get("/api/projects").json()]
    assert "brand-new" in ids


def test_source_file_is_not_listed_as_output(client: TestClient):
    """输入文件不是产物。

    回归：产物是按 `.epub/.pdf` 后缀扫出来的，而输入也叫 `source.epub`——
    实测在界面下载清单里见到了 `source.epub`。
    """
    client.post("/api/books",
                files={"file": ("t.epub", b"PK\x03\x04fake", "application/epub+zip")},
                data={"doc_id": "src-mix", "translate": "false"})
    body = client.get("/api/books/src-mix").json()
    assert body["source"] == "source.epub"  # 输入照常报告
    assert body["outputs"] == []            # 但不算产物


def test_nonexistent_project_still_404(client: TestClient):
    """放宽存在判定之后，真的不存在的项目仍要 404——别把两种情况混成一团。"""
    assert client.get("/api/books/never-existed").status_code == 404


# ── 删除项目内容 ────────────────────────────────────────────────────
def _seed_project(root: Path, doc_id: str, title: str = "我の本") -> P.Project:
    """造一个"完整"的项目：源书 + IR + db + assets + 一个成品。"""
    proj = P.project_of(root, doc_id)
    proj.dir.mkdir(parents=True)
    proj.assets.mkdir()
    (proj.assets / "cover.jpg").write_bytes(b"\xff\xd8\xff")
    (proj.dir / "source.epub").write_bytes(b"PK\x03\x04fake source")
    ir = make_ir(title=title, doc_id=doc_id)
    proj.ir_path.write_text(ir.model_dump_json(), encoding="utf-8")
    conn = connect(proj.db_path)
    try:
        import_ir(conn, ir)
    finally:
        conn.close()
    (proj.dir / f"{title}.zh.epub").write_bytes(b"PK\x03\x04fake output")
    return proj


def test_delete_clears_everything_but_keeps_the_row(client: TestClient, tmp_path: Path):
    """删除 = 清空项目内容，但项目行要留下（否则用户以为删错了东西）。"""
    root = tmp_path / "work"
    proj = _seed_project(root, "to-delete")
    assert client.get("/api/books/to-delete").status_code == 200

    r = client.delete("/api/books/to-delete")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["files"] >= 4
    assert body["bytes"] > 0
    assert "MB" in body["summary"]

    # 内容清空
    assert not proj.ir_path.exists()
    assert not proj.db_path.exists()
    assert not proj.source_file()
    assert not proj.assets.exists()
    assert list(proj.dir.glob("*.epub")) == []
    assert proj.is_deleted() is True

    # 但项目行还在，且状态是"已删除"
    detail = client.get("/api/books/to-delete").json()
    assert detail["deleted"] is True
    assert detail["has_ir"] is False and detail["has_db"] is False
    assert detail["outputs"] == []
    assert detail["source"] is None
    assert "to-delete" in [p["doc_id"] for p in client.get("/api/projects").json()]


def test_delete_keeps_job_logs(client: TestClient, tmp_path: Path):
    """任务日志要保留——它在工作根的 service.db 里，不在项目目录下。

    这条是用户明确要求的："保留项目对应的任务日志"。
    """
    root = tmp_path / "work"
    _seed_project(root, "with-logs")
    r = client.post("/api/books/with-logs/jobs", json={"kind": "extract", "params": {}})
    assert r.status_code == 201
    job_id = r.json()["job_id"]
    # 让作业进入终态，否则删除会因为"还有作业在跑"被拒（那是另一条测试）
    client.delete(f"/api/jobs/{job_id}")

    assert client.delete("/api/books/with-logs").status_code == 200

    logs = client.get("/api/jobs?doc_id=with-logs").json()
    assert [j["id"] for j in logs] == [job_id], "删除项目不该动任务日志"


def test_delete_refuses_while_a_job_is_running(client: TestClient, tmp_path: Path):
    """有作业在跑时拒绝删除：边跑边删会把半途的产物又写回来。"""
    root = tmp_path / "work"
    _seed_project(root, "busy")
    client.post("/api/books/busy/jobs", json={"kind": "extract", "params": {}})

    r = client.delete("/api/books/busy")
    assert r.status_code == 409
    assert "作业" in r.json()["detail"]
    # 拒绝之后内容必须一个都没少
    assert (root / "busy" / "book.ir.json").is_file()


def test_delete_is_not_reversible_by_rerunning(client: TestClient, tmp_path: Path):
    """删完再跑渲染也不会变回"可翻译"——缺了源书和翻译库，跑不起来。

    用户明确要求状态"保持已删除，不自动变回去"。
    """
    root = tmp_path / "work"
    _seed_project(root, "gone")
    client.delete("/api/books/gone")

    r = client.post("/api/books/gone/jobs", json={"kind": "render", "params": {}})
    assert r.status_code in (201, 409)  # 提交得出去也没关系
    body = client.get("/api/books/gone").json()
    assert body["deleted"] is True


def test_reuploading_revives_a_deleted_project(client: TestClient, tmp_path: Path):
    """往已删除的项目里重新上传 = 重新建它。

    不清标记的话，这个项目会永远卡在「已删除」，用户没法复活它。
    """
    root = tmp_path / "work"
    _seed_project(root, "revive")
    client.delete("/api/books/revive")
    assert client.get("/api/books/revive").json()["deleted"] is True

    r = client.post("/api/books",
                    files={"file": ("t.epub", b"PK\x03\x04fake", "application/epub+zip")},
                    data={"doc_id": "revive", "translate": "false"})
    assert r.status_code == 201
    body = client.get("/api/books/revive").json()
    assert body["deleted"] is False
    assert body["source"] == "source.epub"


def test_delete_missing_project_is_404(client: TestClient):
    assert client.delete("/api/books/never-existed").status_code == 404


# ── 列表排序：最近活动的在最前 ──────────────────────────────────────
def _make_project(root: Path, name: str, age_seconds: float) -> P.Project:
    """造一个项目，并把它的文件时间往前拨 age_seconds 秒。"""
    proj = P.project_of(root, name)
    proj.dir.mkdir(parents=True)
    f = proj.dir / "source.epub"
    f.write_bytes(b"PK\x03\x04")
    stamp = time.time() - age_seconds
    os.utime(f, (stamp, stamp))
    return proj


def test_projects_sorted_newest_first(tmp_path: Path):
    """越新的排越前——浏览逻辑是"我刚动过的在最上面"。

    回归：原来按目录名排序，新建的项目会沉到列表底部，每次都要去找。
    """
    root = tmp_path / "work"
    root.mkdir()
    _make_project(root, "alpha", age_seconds=3000)
    _make_project(root, "beta", age_seconds=2000)
    _make_project(root, "gamma", age_seconds=1000)

    assert [p.doc_id for p in P.list_projects(root)] == ["gamma", "beta", "alpha"]


def test_translating_moves_project_to_top(tmp_path: Path):
    """改 `translations.db` 的内容也要算"活动"。

    这里刻意不去动目录本身：目录的 mtime 只在增删直接子项时更新，
    所以排序必须看**文件**的 mtime，否则翻完一本书它也不会往前排。
    """
    root = tmp_path / "work"
    root.mkdir()
    _make_project(root, "old", age_seconds=3000)
    proj = _make_project(root, "new", age_seconds=2000)
    assert [p.doc_id for p in P.list_projects(root)] == ["new", "old"]

    # 模拟翻译写库：新建一个 db 文件（比改旧文件更快，mtime 必然最新）
    proj.db_path.write_bytes(b"db")
    assert [p.doc_id for p in P.list_projects(root)][0] == "new"


def test_sort_is_stable_for_equal_timestamps(tmp_path: Path):
    """时间戳相同时按名字排，保证刷新列表顺序不乱跳。"""
    root = tmp_path / "work"
    root.mkdir()
    same = time.time() - 500
    for name in ("zeta", "alpha", "mid"):
        proj = P.project_of(root, name)
        proj.dir.mkdir(parents=True)
        f = proj.dir / "source.epub"
        f.write_bytes(b"PK\x03\x04")
        os.utime(f, (same, same))

    ids = [p.doc_id for p in P.list_projects(root)]
    assert ids == ["alpha", "mid", "zeta"]
    # 再列一次结果一样（稳定）
    assert [p.doc_id for p in P.list_projects(root)] == ids


def test_render_names_outputs_after_book_title(tmp_path: Path):
    """端到端确认产物文件名用的是书名。"""
    root = tmp_path / "work"
    proj = P.project_of(root, "user-test")
    proj.dir.mkdir(parents=True)
    proj.assets.mkdir()

    ir = make_ir(title="私の本", doc_id="user-test")
    proj.ir_path.write_text(ir.model_dump_json(), encoding="utf-8")
    conn = connect(proj.db_path)
    try:
        import_ir(conn, ir)
    finally:
        conn.close()

    res = P.run_render(proj, mode="zh", to="epub")
    assert not res.error, res.error
    names = [p.name for p in res.files]
    assert names == ["私の本.zh.epub"], names
    assert not any("user-test" in n for n in names)
