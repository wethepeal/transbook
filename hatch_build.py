"""hatchling 构建钩子：把前端构建产物 `web/dist` 嵌进 wheel。

**为什么需要它**

M6 的前端产物在 `.gitignore` 里（带内容哈希的文件名会让每次构建产生无意义 diff），
但交付形态要求"装包即用"——用户在私人电脑上装 wheel 时不该为了一个界面去装 Node。
所以打包时必须把 `web/dist` 一起塞进去。

**为什么不用 pyproject 里的 `force-include` 表**

那张表在前端没构建时直接抛 `FileNotFoundError: Forced include not found`，
而全新 clone 恰恰没有 `web/dist`（被 gitignore 了）——结果是 `uv sync` 和
`uv build` 双双失败，开发流程直接断掉。（已实测确认。）

这里改成"有就嵌入、没有就跳过并告警"：开发流程永远可用，
发布包的完整性由 `tools/build_release.py` 在打包后复核 wheel 内容来保证。

**目标路径为什么是 `transbook/web/dist`**

`service/api.py::find_web_dist()` 会从 `api.py` 所在目录逐级向上找 `web/dist`。
wheel 装好后 `api.py` 在 `site-packages/transbook/service/`，
其父目录 `site-packages/transbook/` 下的 `web/dist` 正好落在查找链上，
因此运行期无需任何额外配置即可找到界面。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class FrontendBuildHook(BuildHookInterface):
    """把 `web/dist` 映射为 wheel 内的 `transbook/web/dist`。"""

    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        dist = Path(self.root) / "web" / "dist"
        if (dist / "index.html").is_file():
            build_data.setdefault("force_include", {})["web/dist"] = "transbook/web/dist"
            self.app.display_info(f"已嵌入前端产物 {dist}")
        else:
            # 不是错误：开发者可能只想跑 CLI。发布时 build_release.py 会拦。
            self.app.display_warning(
                "未找到 web/dist，本次产物不含 Web 界面"
                "（发布前请跑 python tools/build_release.py）"
            )
