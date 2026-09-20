"""配置页（`/api/config`）与 `.env` 读写的测试。

**隔离是必须的**：`env_write_path()` 默认会落到真实的项目根 `.env`——测试里若不把
`PROJECT_ROOT`/`cwd`/`APPDATA` 全指向 tmp_path，跑一次测试就会把用户真实的
API Key 覆盖掉。所以下面每个用例都走 `isolated_env`。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import transbook.config as cfg
from transbook.service import jobs as J
from transbook.service.api import create_app, mask_secret

TRACKED = ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "TRANSLATE_MODEL")


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch):
    """把 `.env` 的查找与写入完全关进 tmp_path，并在结束后还原环境变量。"""
    saved = {n: os.environ.get(n) for n in TRACKED}
    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.setattr(cfg, "PROJECT_ROOT", root)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.chdir(root)
    for name in (*TRACKED, "TRANSBOOK_ENV"):
        os.environ.pop(name, None)
    cfg.load_dotenv.cache_clear()
    try:
        yield root
    finally:
        # apply_env_values 直接改了 os.environ，monkeypatch 不管这个，得自己还原
        for name in TRACKED:
            if saved[name] is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = saved[name]
        cfg.load_dotenv.cache_clear()


@pytest.fixture
def client(isolated_env, tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(J, "spawn", lambda db, r, jid: None)
    root = tmp_path / "work"
    root.mkdir()
    return TestClient(create_app(root))


# ── 打码 ────────────────────────────────────────────────────────────
def test_mask_secret_keeps_only_ends():
    assert mask_secret("") == ""
    assert mask_secret("short") == "*****"
    masked = mask_secret("sk-fake00000000000000000000abcdef")
    assert masked.startswith("sk-045") and masked.endswith("ba86")
    assert "e3cf43da93c43ee" not in masked


# ── 读 ──────────────────────────────────────────────────────────────
def test_get_config_never_returns_secret_plaintext(client: TestClient, isolated_env: Path):
    secret = "sk-fake00000000000000000000abcdef"
    (isolated_env / ".env").write_text(f"DEEPSEEK_API_KEY={secret}\n", encoding="utf-8")
    cfg.load_dotenv.cache_clear()

    r = client.get("/api/config")
    assert r.status_code == 200
    body = r.json()

    key = next(f for f in body["fields"] if f["name"] == "DEEPSEEK_API_KEY")
    assert key["secret"] is True
    assert key["is_set"] is True
    assert key["value"] == "", "机密项不能有值字段"
    assert key["masked"].endswith("ba86")

    # 最要紧的一条：整个响应体里不能出现明文
    assert secret not in r.text, "接口把密钥明文回传了"
    assert body["active"]["key_set"] is True


def test_get_config_exposes_non_secret_values(client: TestClient, isolated_env: Path):
    (isolated_env / ".env").write_text(
        "DEEPSEEK_BASE_URL=http://127.0.0.1:8117/v1\nTRANSLATE_MODEL=deepseek-chat\n",
        encoding="utf-8")
    cfg.load_dotenv.cache_clear()

    body = client.get("/api/config").json()
    by_name = {f["name"]: f for f in body["fields"]}
    assert by_name["DEEPSEEK_BASE_URL"]["value"] == "http://127.0.0.1:8117/v1"
    assert by_name["TRANSLATE_MODEL"]["value"] == "deepseek-chat"
    assert by_name["DEEPSEEK_BASE_URL"]["secret"] is False
    assert body["env_file"].endswith(".env")


# ── 写 ──────────────────────────────────────────────────────────────
def test_put_config_writes_file_and_applies_immediately(client: TestClient, isolated_env: Path):
    r = client.put("/api/config", json={"values": {"DEEPSEEK_API_KEY": "sk-test-1234567890"}})
    assert r.status_code == 200 and r.json()["ok"] is True

    env_file = isolated_env / ".env"
    assert env_file.is_file()
    assert "DEEPSEEK_API_KEY=sk-test-1234567890" in env_file.read_text(encoding="utf-8")
    # 不重启服务就生效——服务进程自己的环境变量也要更新
    assert os.environ["DEEPSEEK_API_KEY"] == "sk-test-1234567890"


def test_put_config_only_touches_submitted_keys(client: TestClient, isolated_env: Path):
    """**核心安全属性**：只改模型时，密钥必须原封不动。

    这正是"只提交改动字段"要防的事故——若前端整表回传、密钥字段留空，
    用户改个模型就会把密钥一起清掉。
    """
    (isolated_env / ".env").write_text(
        "# 我的配置\nDEEPSEEK_API_KEY=sk-keep-me-please\nDEEPSEEK_BASE_URL=http://x/v1\n",
        encoding="utf-8")
    cfg.load_dotenv.cache_clear()

    client.put("/api/config", json={"values": {"TRANSLATE_MODEL": "deepseek-chat"}})

    text = (isolated_env / ".env").read_text(encoding="utf-8")
    assert "DEEPSEEK_API_KEY=sk-keep-me-please" in text
    assert "DEEPSEEK_BASE_URL=http://x/v1" in text
    assert "TRANSLATE_MODEL=deepseek-chat" in text
    assert "# 我的配置" in text, "注释不能被吃掉"
    # 注意：os.environ 不会自动被 .env 填充，要经 load_dotenv 才落到环境变量里，
    # 所以这里用 cfg.get（它会触发加载）而不是直接读 os.environ
    assert cfg.get("DEEPSEEK_API_KEY") == "sk-keep-me-please"


def test_put_config_empty_value_clears_key(client: TestClient, isolated_env: Path):
    (isolated_env / ".env").write_text("DEEPSEEK_API_KEY=sk-to-be-removed\n", encoding="utf-8")
    cfg.load_dotenv.cache_clear()
    assert cfg.load_dotenv().get("DEEPSEEK_API_KEY") == "sk-to-be-removed"

    client.put("/api/config", json={"values": {"DEEPSEEK_API_KEY": ""}})

    assert "DEEPSEEK_API_KEY=" in (isolated_env / ".env").read_text(encoding="utf-8")
    assert not os.environ.get("DEEPSEEK_API_KEY"), "清空后不应残留旧值"
    assert client.get("/api/config").json()["active"]["key_set"] is False


def test_put_config_rejects_unknown_key(client: TestClient):
    r = client.put("/api/config", json={"values": {"PATH": "/evil"}})
    assert r.status_code == 400
    assert "PATH" in r.json()["detail"]


def test_put_config_with_no_changes_is_a_noop(client: TestClient, isolated_env: Path):
    r = client.put("/api/config", json={"values": {}})
    assert r.status_code == 200
    assert r.json()["changed"] == []
    assert not (isolated_env / ".env").exists(), "没有改动就不该凭空创建 .env"


# ── config 模块本身 ─────────────────────────────────────────────────
def test_env_write_path_prefers_existing_file(isolated_env: Path):
    assert cfg.env_write_path() == isolated_env / ".env"
    (isolated_env / ".env").write_text("A=1\n", encoding="utf-8")
    assert cfg.env_write_path() == isolated_env / ".env"


def test_set_env_values_replaces_in_place_and_appends_new(isolated_env: Path):
    target = isolated_env / ".env"
    target.write_text("# 注释\nDEEPSEEK_API_KEY=old\nOTHER=keep\n", encoding="utf-8")

    cfg.set_env_values(target, {"DEEPSEEK_API_KEY": "new", "TRANSLATE_MODEL": "m"})

    lines = target.read_text(encoding="utf-8").splitlines()
    assert "# 注释" in lines
    assert "DEEPSEEK_API_KEY=new" in lines
    assert "OTHER=keep" in lines
    assert "TRANSLATE_MODEL=m" in lines
    assert len([ln for ln in lines if ln.startswith("DEEPSEEK_API_KEY")]) == 1, "不能写重复行"


def test_config_works_without_built_frontend(isolated_env, tmp_path, monkeypatch):
    """界面没构建时（纯 CLI 部署）配置接口也必须可用。"""
    monkeypatch.setattr(J, "spawn", lambda db, r, jid: None)
    root = tmp_path / "work"
    root.mkdir()
    # 显式给一个不存在的产物目录，确保这次真的是"没有界面"，
    # 而不是恰好命中了项目里已构建的 web/dist
    c = TestClient(create_app(root, web=tmp_path / "no-such-webdir"))
    assert c.get("/api/config").status_code == 200
    # 没有界面时 `/` 不托管 HTML，而是回一条说明，确认这次确实是"没有界面"
    assert "前端未构建" in c.get("/").json()["detail"]


# ── 可选值（下拉框）────────────────────────────────────────────────
def test_model_field_exposes_choices(client: TestClient):
    """「默认模型」要给下拉选项，且第一项是"默认"（空值 = 用引擎默认）。"""
    body = client.get("/api/config").json()
    model = next(f for f in body["fields"] if f["name"] == "TRANSLATE_MODEL")

    values = [o["value"] for o in model["options"]]
    assert values == ["", "deepseek-flash", "deepseek-v4-pro"]
    assert "deepseek-flash" in model["options"][0]["label"], "默认项要写明默认用的是哪个模型"


def test_every_model_choice_is_a_priced_model():
    """下拉里出现的模型，必须是引擎认识、且算得出价钱的。

    否则用户选了它，`DeepSeekProvider.estimate_cost` 会**静默**回落到 flash 的单价
    （`PRICES.get(model, PRICES["deepseek-flash"])`），账目就错了却不报错。
    """
    from transbook.service.api import MODEL_CHOICES
    from transbook.translate.deepseek import PRICES

    for value, _label in MODEL_CHOICES:
        if value:
            assert value in PRICES, f"{value} 没有报价，不该出现在下拉里"


def test_secret_and_free_text_fields_have_no_choices(client: TestClient):
    """只有需要枚举的字段才给选项，其余保持自由文本。"""
    body = client.get("/api/config").json()
    by_name = {f["name"]: f for f in body["fields"]}
    assert by_name["DEEPSEEK_API_KEY"]["options"] == []
    assert by_name["DEEPSEEK_BASE_URL"]["options"] == []


def test_custom_model_value_round_trips(client: TestClient, isolated_env: Path):
    """下拉之外的模型名（本地端点）必须能存能读——所以界面保留了「自定义」。"""
    client.put("/api/config", json={"values": {"TRANSLATE_MODEL": "Qwen3-8B-Q5_K_M"}})
    body = client.get("/api/config").json()
    model = next(f for f in body["fields"] if f["name"] == "TRANSLATE_MODEL")
    assert model["value"] == "Qwen3-8B-Q5_K_M"
