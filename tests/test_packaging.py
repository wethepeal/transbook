"""发布形态的回归测试（打包相关）。

这些行为在引入打包流程之前没有任何覆盖，但它们是"装包即用"成立的前提：

* 前端必须能从**包内嵌位置**找到——否则用户装完是一个没有界面的壳；
* `.env` 必须能从**当前工作目录**读到——装成 wheel 后 `PROJECT_ROOT` 会变成
  `site-packages` 的上一级，用户把密钥放在启动目录里会完全不生效；
* 输出必须能扛住当前编码表达不了的字符——Windows 上输出被重定向时按 ANSI
  代码页编码，GBK 没有 `✓`(U+2713)，rich 一打印就抛 UnicodeEncodeError。

前两条一旦回归，症状是"装了包但用不了"，而不是构建报错，所以必须有测试兜住。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

# ── 前端定位 ────────────────────────────────────────────────────────


def _fake_installed_pkg(root: Path) -> Path:
    """造一个"装好的包"布局：`<root>/site-packages/transbook/{service,web/dist}`。"""
    pkg = root / "site-packages" / "transbook"
    (pkg / "service").mkdir(parents=True)
    (pkg / "web" / "dist").mkdir(parents=True)
    (pkg / "web" / "dist" / "index.html").write_text("<div id='root'></div>", encoding="utf-8")
    api = pkg / "service" / "api.py"
    api.write_text("", encoding="utf-8")
    return api


def test_find_web_dist_finds_package_embedded_copy(tmp_path, monkeypatch):
    """装成 wheel 后前端在包内的 `transbook/web/dist`，必须能定位到。"""
    from transbook.service import api

    fake_api = _fake_installed_pkg(tmp_path)
    elsewhere = tmp_path / "elsewhere"  # 模拟"在别处运行"，cwd 里没有 web/dist
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    found = api.find_web_dist(fake_api)

    assert found is not None, "包内嵌的前端没被找到——装出来的包会没有界面"
    assert (found / "index.html").is_file()
    assert "transbook" in Path(found).parts


def test_find_web_dist_prefers_cwd_source_tree(tmp_path, monkeypatch):
    """源码树里跑（cwd 就是项目根）时，命中的应是 cwd 下的 `web/dist`。"""
    from transbook.service import api

    project = tmp_path / "proj"
    (project / "web" / "dist").mkdir(parents=True)
    (project / "web" / "dist" / "index.html").write_text("source tree", encoding="utf-8")
    monkeypatch.chdir(project)
    fake_api = _fake_installed_pkg(tmp_path / "pkg")

    found = api.find_web_dist(fake_api)

    assert found is not None
    assert Path(found).resolve() == (project / "web" / "dist").resolve()


def test_find_web_dist_returns_none_when_nowhere(tmp_path, monkeypatch):
    """两处都没有时必须返回 None，而不是抛异常或返回瞎猜的路径。"""
    from transbook.service import api

    monkeypatch.chdir(tmp_path)
    assert api.find_web_dist(tmp_path / "absent" / "api.py") is None


# ── .env 查找 ───────────────────────────────────────────────────────


def test_env_candidates_includes_cwd(tmp_path, monkeypatch):
    from transbook.config import env_candidates

    monkeypatch.chdir(tmp_path)
    assert (tmp_path / ".env").resolve() in [p.resolve() for p in env_candidates()]


def test_load_dotenv_reads_from_cwd_when_project_root_has_none(tmp_path, monkeypatch):
    """模拟"装成 wheel"的目录形态：`PROJECT_ROOT` 下没有 `.env`，密钥必须从 cwd 读到。

    真实情况是 `PROJECT_ROOT` 会算成 `site-packages` 的上一级（`Lib/`），那里不会有
    `.env`；用户把密钥放在启动目录里。如果只认 `PROJECT_ROOT`，用户配了也读不到。
    """
    import transbook.config as config

    fake_root = tmp_path / "Lib"  # 冒充"不是项目根"的 PROJECT_ROOT
    fake_root.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    (work / ".env").write_text("DEEPSEEK_API_KEY=sk-from-cwd\n", encoding="utf-8")
    monkeypatch.setattr(config, "PROJECT_ROOT", fake_root)
    monkeypatch.chdir(work)

    saved = os.environ.pop("DEEPSEEK_API_KEY", None)
    try:
        config.load_dotenv.cache_clear()
        assert config.load_dotenv().get("DEEPSEEK_API_KEY") == "sk-from-cwd"
    finally:
        config.load_dotenv.cache_clear()
        os.environ.pop("DEEPSEEK_API_KEY", None)
        if saved is not None:
            os.environ["DEEPSEEK_API_KEY"] = saved


def test_project_root_env_wins_over_cwd(tmp_path, monkeypatch):
    """源码树里 `.env` 的优先级高于 cwd：项目根更具体，不该被随便一个目录顶掉。"""
    import transbook.config as config

    root = tmp_path / "proj"
    root.mkdir()
    (root / ".env").write_text("DEEPSEEK_API_KEY=sk-from-project-root\n", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    (work / ".env").write_text("DEEPSEEK_API_KEY=sk-from-cwd\n", encoding="utf-8")
    monkeypatch.setattr(config, "PROJECT_ROOT", root)
    monkeypatch.chdir(work)

    saved = os.environ.pop("DEEPSEEK_API_KEY", None)
    try:
        config.load_dotenv.cache_clear()
        assert config.load_dotenv().get("DEEPSEEK_API_KEY") == "sk-from-project-root"
    finally:
        config.load_dotenv.cache_clear()
        os.environ.pop("DEEPSEEK_API_KEY", None)
        if saved is not None:
            os.environ["DEEPSEEK_API_KEY"] = saved


# ── serve --open ────────────────────────────────────────────────────


def test_open_browser_soon_opens_url(monkeypatch):
    """`--open` 靠这条延迟线程开浏览器；延迟是为了避开 uvicorn 尚未监听的窗口。"""
    import transbook.cli as cli

    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)

    cli._open_browser_soon("http://127.0.0.1:1/", delay=0.01)
    for _ in range(100):
        if opened:
            break
        time.sleep(0.02)

    assert opened == ["http://127.0.0.1:1/"]


# ── 输出编码兜底 ────────────────────────────────────────────────────


def test_piped_output_survives_unencodable_characters():
    """导入 cli 之后，往管道里打印 `✓` 必须不再把命令打断。

    这是真跑一个子进程：Windows 上 stdout 是管道时按 ANSI 代码页编码，
    GBK 没有 U+2713，rich 会抛 UnicodeEncodeError 使进程以非零码退出。
    在 UTF-8 环境下这条测试同样通过（rc 本来就是 0），所以不会误报。
    """
    code = (
        "import transbook.cli\n"  # 触发输出编码兜底
        "from rich.console import Console\n"
        "Console().print('marker \\u2713 \\u246a')\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True)

    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-500:]
