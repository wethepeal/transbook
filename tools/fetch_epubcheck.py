"""下载 epubcheck 完整发行包到 Z:\\Tools（多镜像 + 断点续传 + 重试）。

GitHub 直连在国内常年卡死，这里依次尝试多个 gh 代理；每个源都支持从已下载
字节处续传，避免"每次都从头开始然后超时"。
"""

from __future__ import annotations

import pathlib
import sys
import time
import urllib.request

VER = "5.2.1"
DST = pathlib.Path(r"Z:\Tools") / f"epubcheck-{VER}.zip"
SOURCES = [
    "https://gh-proxy.com/https://github.com/w3c/epubcheck/releases/download/v{ver}/epubcheck-{ver}.zip",
    "https://ghfast.top/https://github.com/w3c/epubcheck/releases/download/v{ver}/epubcheck-{ver}.zip",
    "https://ghproxy.net/https://github.com/w3c/epubcheck/releases/download/v{ver}/epubcheck-{ver}.zip",
    "https://github.com/w3c/epubcheck/releases/download/v{ver}/epubcheck-{ver}.zip",
]


def fetch(url: str, timeout: float = 60.0, chunk: int = 262144) -> None:
    have = DST.stat().st_size if DST.exists() else 0
    headers = {"User-Agent": "Mozilla/5.0"}
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        total = int(r.headers.get("Content-Length") or 0) + have
        mode = "ab" if have and r.status == 206 else "wb"
        if mode == "wb":
            have = 0
        with open(DST, mode) as f:
            while True:
                b = r.read(chunk)
                if not b:
                    break
                f.write(b)
                have += len(b)
                print(f"\r  {have/1048576:.2f} MB" + (f" / {total/1048576:.2f} MB" if total else ""),
                      end="", flush=True)
    print()


def main() -> int:
    for round_no in range(1, 6):
        for tpl in SOURCES:
            url = tpl.format(ver=VER)
            try:
                fetch(url)
                print(f"OK {url.split('/')[2]} → {DST} ({DST.stat().st_size/1048576:.2f} MB)")
                return 0
            except Exception as exc:  # noqa: BLE001
                print(f"  [{round_no}] {url.split('/')[2]} 失败: {type(exc).__name__} {str(exc)[:60]}")
        time.sleep(5)
    print("所有源均失败")
    return 1


if __name__ == "__main__":
    sys.exit(main())
