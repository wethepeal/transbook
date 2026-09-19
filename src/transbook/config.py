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


@lru_cache(maxsize=1)
def load_dotenv(path: str | None = None) -> dict[str, str]:
    """读取 `.env`：支持 `KEY=VALUE`、`#` 注释、单双引号；不覆盖已存在的环境变量。

    返回本次从文件加载到的键值（用于诊断显示来源）。
    """
    target = Path(path) if path else ENV_FILE
    loaded: dict[str, str] = {}
    if not target.is_file():
        return loaded
    for raw in target.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        loaded[key] = value
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
