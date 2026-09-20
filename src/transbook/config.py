"""项目配置与 `.env` 读取。

设计取舍：
* **不引入 python-dotenv**——格式需求极简，10 行代码即可，减少一个依赖。
* **环境变量优先于 `.env`**：`os.environ.setdefault`，因此 CI 或临时覆盖不会被文件里的值顶掉。
* `.env` 已在 `.gitignore` 中，密钥不会进仓库。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"

#: 项目会用到的密钥/端点（写进 .env 或环境变量都可以）
KNOWN_KEYS = (
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "TRANSLATE_ENGINE",
    "TRANSLATE_MODEL",
)


def env_candidates() -> list[Path]:
    """`.env` 的查找位置，按优先级从高到低。

    为什么不止一个：`PROJECT_ROOT` 是 `config.py` 往上数两级，在源码树里正好是项目根，
    但**装成 wheel 之后会变成 `site-packages` 的上一级（`Lib/`）**，那儿不会有 `.env`。
    而发布包的实际用法是"用户把书和配置放在一个文件夹里，双击启动"，
    所以必须同时认当前工作目录，否则装了包的用户配了密钥也读不到。
    """
    out: list[Path] = []
    override = os.environ.get("TRANSBOOK_ENV")
    if override:
        out.append(Path(override))
    out.append(PROJECT_ROOT / ".env")
    out.append(Path.cwd() / ".env")
    base = os.environ.get("APPDATA")
    out.append((Path(base) if base else Path.home() / ".config") / "transbook" / ".env")

    seen: set[str] = set()
    uniq: list[Path] = []
    for p in out:
        key = str(p.resolve())
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


def env_files_found() -> list[Path]:
    """实际存在、且按优先级排序的 `.env`（`tp doctor` 用来告诉用户读的是哪一份）。"""
    return [p for p in env_candidates() if p.is_file()]


def env_write_path() -> Path:
    """配置应该写到**哪一份** `.env`。

    优先写已经存在的那一份（用户配过就顺着改，不会又冒出一份新的）；
    一份都没有时，源码树里写项目根、装成 wheel 后写当前工作目录——
    与 `env_candidates()` 的查找顺序一致，保证写进去下次一定读得到。
    """
    existing = next((p for p in env_candidates() if p.is_file()), None)
    if existing is not None:
        return existing
    root = PROJECT_ROOT if (PROJECT_ROOT / "pyproject.toml").is_file() else Path.cwd()
    return root / ".env"


def set_env_values(target: Path, values: dict[str, str]) -> None:
    """把若干键写进 `.env`，**保留文件里的其它内容与注释**。

    空串表示"清空这个键"（写成 `NAME=`），而不是删掉整行——保留行便于用户之后
    手填，也不会因为删行把文件里成组的注释拆散。
    """
    lines = target.read_text(encoding="utf-8").splitlines() if target.is_file() else []
    out: list[str] = []
    done: set[str] = set()
    for line in lines:
        stripped = line.lstrip()
        name = None
        if "=" in stripped and not stripped.startswith("#"):
            name = stripped.split("=", 1)[0].strip()
        if name in values:
            out.append(f"{name}={values[name]}")
            done.add(name)
        else:
            out.append(line)
    # 新键统一追加到末尾：只在**第一项**前面空一行，其余紧挨着写，
    # 否则每加一个键就多一个空行，文件会显得很散
    added = False
    for name, value in values.items():
        if name in done:
            continue
        if not added:
            if out and out[-1].strip():
                out.append("")
            added = True
        out.append(f"{name}={value}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(out) + "\n", encoding="utf-8")


def apply_env_values(values: dict[str, str]) -> Path:
    """写进 `.env` 并让**当前进程立刻生效**（不必重启服务），返回写入的文件路径。

    必须同时改 `os.environ`：`load_dotenv` 用的是 `setdefault`，若进程启动时
    已经从旧 `.env` 里设过一个空值，光改文件是不会被重新读到的。
    作业子进程会重新读磁盘上的 `.env`，所以它们也能拿到新值。
    """
    target = env_write_path()
    set_env_values(target, values)
    for name, value in values.items():
        if value:
            os.environ[name] = value
        else:
            os.environ.pop(name, None)
    load_dotenv.cache_clear()
    return target


@lru_cache(maxsize=1)
def load_dotenv(path: str | None = None) -> dict[str, str]:
    """读取 `.env`：支持 `KEY=VALUE`、`#` 注释、单双引号；不覆盖已存在的环境变量。

    显式给了 `path` 就只读那一份；否则按 `env_candidates()` 的顺序全部读一遍，
    **先读到的键胜出**（`setdefault`）。返回本次从文件加载到的键值（用于诊断显示来源）。
    """
    targets = [Path(path)] if path else env_candidates()
    loaded: dict[str, str] = {}
    for target in targets:
        if not target.is_file():
            continue
        for raw in target.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if not key:
                continue
            loaded.setdefault(key, value)
            os.environ.setdefault(key, value)
    return loaded


def get(name: str, default: str | None = None) -> str | None:
    """读取配置项（先查环境变量，再查 `.env`）。"""
    load_dotenv()
    return os.environ.get(name, default)


def deepseek_key() -> str | None:
    """DeepSeek API 密钥；未配置时返回 None（调用方需给出清晰报错）。"""
    return get("DEEPSEEK_API_KEY")


def has_api_key() -> bool:
    return bool(deepseek_key())
