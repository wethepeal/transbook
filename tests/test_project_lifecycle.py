"""项目生命周期：新建 → 存在判定 → 产物命名。

覆盖两个用户实测报出来的问题：

1. **新建项目后进详情页报「项目不存在」**。上传只写了 `source.epub`，抽取是后台作业；
   而 `Project.exists()` 当时要求已有 IR 或 db，于是抽取没跑完时详情接口就 404——
   用户看到的弹窗像是"建失败了"，其实建成功了。
2. **产物用项目名命名**（`user-test.zh.epub`）。项目名是管理标识，产物是给人看的文件，
   应该叫书名（`书名.zh.epub`）。
"""

from __future__ import annotations

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
    monkeypatch.setattr(J, "spawn", lambda db, r, jid: None)
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
