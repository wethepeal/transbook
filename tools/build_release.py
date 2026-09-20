"""构建正式发布包：前端 → wheel → 开箱即用的 Release zip。

**为什么需要这个脚本**

M6 的前端产物不进 git（带内容哈希的文件名会让每次构建产生一堆无意义 diff），
所以 `git clone` 拿到的源码**没有界面**。而交付要求是"在私人电脑上装包即用、
不碰命令行"，因此发布时必须把前端一起打进 wheel。

这里把三个容易漏的步骤串成一条命令，并且在最后**复核 wheel 里真有界面**：
漏掉前端不会让构建报错，只会让用户装完发现页面打不开——所以必须主动验证，
不能只看退出码。

产出（都在 `dist/` 下）：

    transbook-<版本>-py3-none-any.whl    ← 主产物，前端已内嵌
    transbook-<版本>-win64.zip           ← 面向非命令行用户：解压双击 start.cmd

用法：

    python tools/build_release.py                  # 全流程
    python tools/build_release.py --skip-frontend  # 复用已有 web/dist（前端没改时省时间）
    python tools/build_release.py --no-zip         # 只要 wheel
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

from transbook.console import tolerate_unencodable_output

# 兜底编码：这里崩溃的往往不是自己的文案，而是被捕获后转印的 npm/vite 输出
# （vite 会打印 `✓ built in ...`）。原因见 transbook/console.py。
tolerate_unencodable_output()

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
DIST = ROOT / "dist"
PACKAGING = ROOT / "packaging"

#: wheel 里必须存在的界面入口。缺了它就是"没界面的壳"，不能发布。
WHEEL_WEB_ENTRY = "transbook/web/dist/index.html"

#: Release zip 里的启动素材（放在 packaging/ 下，跟代码一起版本管理）。
ZIP_EXTRAS = ("start.cmd", "README.txt")


def _tolerate_unencodable_output() -> None:
    """让输出遇到当前编码表达不了的字符时替换成 `?`，而不是把构建打断。

    与 `src/transbook/cli.py` 里同名函数是同一个原因，只是触发点不同：
    这里崩溃的不是自己的文案，而是**被捕获后转印的 npm/vite 输出**——
    vite 会打印 `✓ built in ...`，而 Windows 管道下 Python 用 GBK 编码，
    GBK 里没有 U+2713，一 print 就抛 UnicodeEncodeError。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


_tolerate_unencodable_output()


def run(cmd: list[str], *, cwd: Path, label: str) -> None:
    """跑一条外部命令；失败就把完整输出打出来再退出，绝不吞错。"""
    print(f"── {label}\n   $ {' '.join(cmd)}", flush=True)
    # Windows 上 npm 是 .cmd，CreateProcess 不能直接执行，统一交给 cmd /c
    real = ["cmd", "/c", *cmd] if os.name == "nt" else cmd
    proc = subprocess.run(real, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode != 0:
        print(out)
        sys.exit(f"× {label} 失败（退出码 {proc.returncode}）")
    for line in [x for x in out.splitlines() if x.strip()][-4:]:
        print("   " + line[:160])


def read_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def find_npm() -> str:
    for name in ("npm.cmd", "npm"):
        if shutil.which(name):
            return name
    sys.exit(
        "× 找不到 npm：构建前端需要 Node.js ≥ 20（https://nodejs.org/）。\n"
        "  只想打不含界面的包（自用/调试）：加 --skip-frontend"
    )


def build_frontend() -> None:
    npm = find_npm()
    if not (WEB / "node_modules").is_dir():
        # 有 lock 就走 ci（可复现），否则退回 install
        sub = "ci" if (WEB / "package-lock.json").is_file() else "install"
        run([npm, sub], cwd=WEB, label=f"安装前端依赖（npm {sub}）")
    run([npm, "run", "build"], cwd=WEB, label="构建前端")


def require_frontend() -> None:
    idx = WEB / "dist" / "index.html"
    if not idx.is_file():
        sys.exit(
            f"× 前端产物缺失：{idx}\n"
            "  去掉 --skip-frontend 重跑，让脚本自己构建一次。"
        )
    size = sum(p.stat().st_size for p in (WEB / "dist").rglob("*") if p.is_file())
    print(f"── 前端产物就绪：{(WEB / 'dist').relative_to(ROOT)}（{size / 1024:.0f} KB）")


def build_wheel() -> Path:
    DIST.mkdir(parents=True, exist_ok=True)
    run(["uv", "build", "--wheel", "--out-dir", str(DIST)], cwd=ROOT, label="构建 wheel")
    wheels = sorted(DIST.glob("*.whl"), key=lambda p: p.stat().st_mtime)
    if not wheels:
        sys.exit("× uv build 没有产出 wheel")
    return wheels[-1]


def verify_wheel(whl: Path) -> None:
    """发布包的生命线：wheel 里必须真有前端。

    只检查构建退出码是不够的——`hatch_build.py` 在找不到 web/dist 时会**跳过并告警**，
    构建照样成功，但产物是个没界面的壳。所以这里直接拆开 wheel 看。
    """
    with zipfile.ZipFile(whl) as z:
        names = set(z.namelist())
    if WHEEL_WEB_ENTRY not in names:
        sys.exit(
            f"× {whl.name} 里没有 {WHEEL_WEB_ENTRY} —— 前端没打进去，不能发布。\n"
            "  排查：web/dist 是否存在、hatch_build.py 是否被 hatchling 加载。"
        )
    embedded = sorted(n for n in names if n.startswith("transbook/web/dist/"))
    print(f"── wheel 复核通过：内嵌前端 {len(embedded)} 个文件")
    for n in embedded:
        print(f"   {n}")


def make_zip(whl: Path, version: str) -> Path:
    out = DIST / f"transbook-{version}-win64.zip"
    members = [whl, *(PACKAGING / n for n in ZIP_EXTRAS)]
    missing = [m for m in members if not m.is_file()]
    if missing:
        sys.exit("× 打包素材缺失：" + "、".join(str(m.relative_to(ROOT)) for m in missing))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for m in members:
            z.write(m, m.name)
    print(f"── Release zip 就绪：{out.name}")
    for m in members:
        print(f"   {m.name}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="构建 transbook 发布包")
    ap.add_argument("--skip-frontend", action="store_true",
                    help="复用已有 web/dist，不重新构建前端")
    ap.add_argument("--no-zip", action="store_true", help="只出 wheel，不组装 Release zip")
    args = ap.parse_args()

    version = read_version()
    print(f"transbook {version} —— 构建发布包\n")

    if args.skip_frontend:
        print("── 跳过前端构建（--skip-frontend）")
    else:
        build_frontend()
    require_frontend()

    whl = build_wheel()
    verify_wheel(whl)

    if not args.no_zip:
        make_zip(whl, version)

    print(f"\n√ 完成。产物在 {DIST.relative_to(ROOT)}/")
    print("  下一步：把 dist/ 里的两个文件挂到 GitHub Release（CI 会自动做，见 "
          ".github/workflows/release.yml）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
