"""M6 前端托管测试。

只测**服务端**这一半：静态资源托管、SPA 兜底、产物下载与目录穿越防护。
界面交互本身用 Playwright 打真实页面验证（见提交说明），不在单测里跑浏览器。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from transbook.service import jobs as J
from transbook.service import pipeline as P
from transbook.service.api import create_app, find_web_dist


def make_web(tmp_path: Path) -> Path:
    """造一个最小的前端产物目录。"""
    d = tmp_path / "webdist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text(
        '<!doctype html><div id="root"></div>'
        '<script type="module" src="/assets/app.js"></script>', encoding="utf-8")
    (d / "assets" / "app.js").write_text("console.log('ok')", encoding="utf-8")
    return d


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setattr(J, "spawn", lambda db, r, jid: None)
    root = tmp_path / "work"
    root.mkdir()
    return TestClient(create_app(root, web=make_web(tmp_path)))


# ── 静态托管 ────────────────────────────────────────────────────────
def test_index_served_at_root(client: TestClient):
    r = client.get("/")
    assert r.status_code == 200 and 'id="root"' in r.text
    assert r.headers["content-type"].startswith("text/html")


def test_assets_served(client: TestClient):
    r = client.get("/assets/app.js")
    assert r.status_code == 200 and "console.log" in r.text


def test_unknown_path_falls_back_to_index(client: TestClient):
    """hash 路由其实用不到兜底，但直接敲路径时不该 404。"""
    r = client.get("/some/deep/path")
    assert r.status_code == 200 and 'id="root"' in r.text


def test_unknown_api_path_stays_404(client: TestClient):
    """未命中的 /api 必须还是 404。

    回退成 index.html 的话，前端会拿 HTML 当 JSON 解析，报出莫名其妙的错。
    """
    r = client.get("/api/definitely-not-here")
    assert r.status_code == 404
    assert "text/html" not in r.headers.get("content-type", "")


def test_api_still_works_with_web_mounted(client: TestClient):
    assert client.get("/api/health").json()["status"] == "ok"


def test_no_web_dir_returns_hint(tmp_path: Path):
    """没构建前端时给一句人能看懂的提示，而不是 500。"""
    root = tmp_path / "w"
    root.mkdir()
    c = TestClient(create_app(root, web=tmp_path / "nope"))
    r = c.get("/")
    assert r.status_code == 200 and "前端未构建" in r.json()["detail"]


def test_find_web_dist_returns_none_or_real_dir():
    d = find_web_dist()
    assert d is None or (d / "index.html").is_file()


# ── 产物下载 ────────────────────────────────────────────────────────
def _project_with_output(tmp_path: Path, minimal_epub: Path) -> tuple[TestClient, str]:
    root = tmp_path / "work2"
    root.mkdir()
    c = TestClient(create_app(root, web=make_web(tmp_path)))
    proj = P.store_source(root, "dl", "s.epub", minimal_epub.read_bytes())
    P.run_extract(proj, proj.source_file())
    P.run_import(proj)
    P.run_translate(proj, engine="fake")
    P.run_render(proj, mode="zh", to="epub")
    return c, "dl"


def test_download_output(tmp_path: Path, minimal_epub: Path):
    c, doc = _project_with_output(tmp_path, minimal_epub)
    name = c.get(f"/api/books/{doc}").json()["outputs"][0]
    r = c.get(f"/api/books/{doc}/files/{name}")
    assert r.status_code == 200
    assert r.content[:2] == b"PK", "EPUB 就是 zip"
    assert "attachment" in r.headers.get("content-disposition", "")


def test_download_rejects_traversal(tmp_path: Path, minimal_epub: Path):
    """目录穿越必须挡住——项目目录之外的文件一个都不能给。"""
    c, doc = _project_with_output(tmp_path, minimal_epub)
    for bad in ["..%2Fservice.db", "%2E%2E%2F%2E%2E%2Fpyproject.toml", "sub%2Fx.epub"]:
        r = c.get(f"/api/books/{doc}/files/{bad}")
        assert r.status_code in (400, 404), f"{bad} 竟然返回 {r.status_code}"


def test_download_missing_file(tmp_path: Path, minimal_epub: Path):
    c, doc = _project_with_output(tmp_path, minimal_epub)
    assert c.get(f"/api/books/{doc}/files/nope.epub").status_code == 404


def test_download_accepts_head(tmp_path: Path, minimal_epub: Path):
    """只注册 GET 时 HEAD 会返回 405。

    链接检查器、下载工具与断点续传客户端都会先发 HEAD；浏览器点链接虽然走 GET，
    但没有理由让这种标准请求失败。
    """
    c, doc = _project_with_output(tmp_path, minimal_epub)
    name = c.get(f"/api/books/{doc}").json()["outputs"][0]
    r = c.head(f"/api/books/{doc}/files/{name}")
    assert r.status_code == 200
    assert int(r.headers["content-length"]) > 0
    assert r.content == b"", "HEAD 不应带响应体"


# ── 单段保存（校对界面用）─────────────────────────────────────────
def test_patch_segment_writes_final(tmp_path: Path, minimal_epub: Path):
    c, doc = _project_with_output(tmp_path, minimal_epub)
    seg = c.get(f"/api/books/{doc}/segments", params={"limit": 1}).json()["items"][0]
    r = c.patch(f"/api/books/{doc}/segments/{seg['seg_id']}",
                json={"final_translation": "人工定稿"})
    assert r.status_code == 200
    after = c.get(f"/api/books/{doc}/segments", params={"q": "人工定稿"}).json()
    assert after["total"] == 1


def test_patch_segment_clear_reverts(tmp_path: Path, minimal_epub: Path):
    """撤销定稿要回到机翻，而不是把译文清空。"""
    c, doc = _project_with_output(tmp_path, minimal_epub)
    seg = c.get(f"/api/books/{doc}/segments", params={"limit": 1}).json()["items"][0]
    c.patch(f"/api/books/{doc}/segments/{seg['seg_id']}",
            json={"final_translation": "定稿"})
    c.patch(f"/api/books/{doc}/segments/{seg['seg_id']}", json={"clear": True})
    row = c.get(f"/api/books/{doc}/segments", params={"limit": 1}).json()["items"][0]
    assert row["final_translation"] is None
    assert row["translation"], "机翻必须还在"


def test_patch_segment_requires_payload(tmp_path: Path, minimal_epub: Path):
    c, doc = _project_with_output(tmp_path, minimal_epub)
    seg = c.get(f"/api/books/{doc}/segments", params={"limit": 1}).json()["items"][0]
    assert c.patch(f"/api/books/{doc}/segments/{seg['seg_id']}", json={}).status_code == 400
    assert c.patch(f"/api/books/{doc}/segments/nope", json={"final_translation": "x"}
                   ).status_code == 404
