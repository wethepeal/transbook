"""密钥泄露检查：扫描仓库里"看起来像真实凭据"的字符串。

**为什么需要它**：这个项目真的踩过一次——真实 API Key 被写进了测试文件并推送上去，
只能重写历史清除，密钥也作废重发。教训是"别把密钥写进代码"这句话靠人自觉记住
是不可靠的，必须有自动检查。

默认扫当前跟踪的文件（快，适合 CI 每次跑）：

    python tools/check_secrets.py

加 `--history` 会扫**全部 git 历史对象**——已经删掉但曾提交过的内容仍在旧 blob 里，
仓库转公开前应该跑一次这个：

    python tools/check_secrets.py --history

只输出命中的类别与位置，**不打印密钥本身**。
"""

from __future__ import annotations

import argparse
import re
import subprocess
from collections import defaultdict
from pathlib import Path

from transbook.console import tolerate_unencodable_output

# CI 的英文 runner 上控制台是 cp1252，打印中文会直接 UnicodeEncodeError 让检查以
# 非零码退出（这个坑本项目已经踩过三次，所以抽成了共享函数）。
tolerate_unencodable_output()

#: 明确是占位符/示例/测试假值，不算泄露。
#: 注意 bytes 字面量只能放 ASCII，中文要显式 encode。
ALLOW = re.compile(
    rb"sk-x{4,}|sk-xxx+|sk-fake|your[-_]?key|placeholder|example\.com|"
    rb"YOUR_|xxx|\.\.\.|sk-test|sk-verify|sk-from|sk-to-be|sk-keep|1234567890|"
    rb"CHANGE_?ME|TODO" + "你的".encode(),
    re.I,
)

#: (类别, 正则)。宁可多报几条让人看一眼，也不要漏掉真的。
PATTERNS: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    ("API 密钥（sk- 开头）", re.compile(rb"sk-[A-Za-z0-9_-]{20,}")),
    ("GitHub 细粒度令牌", re.compile(rb"github_pat_[A-Za-z0-9_]{20,}")),
    ("GitHub 经典令牌", re.compile(rb"gh[pousr]_[A-Za-z0-9]{30,}")),
    ("AWS Access Key", re.compile(rb"AKIA[0-9A-Z]{16}")),
    ("私钥文件", re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("Slack 令牌", re.compile(rb"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("带值的机密赋值", re.compile(
        rb"(?i)(api[_-]?key|secret|passwd|password|access[_-]?token)\s*[:=]\s*"
        rb"[\"']?([A-Za-z0-9_\-]{16,})[\"']?")),
)

#: 只扫这些后缀；二进制与大文件跳过
TEXT_SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".css", ".html", ".json", ".yml", ".yaml",
    ".toml", ".md", ".txt", ".cfg", ".ini", ".sh", ".cmd", ".ps1", ".example", ".env",
}
MAX_BYTES = 2_000_000


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], capture_output=True, check=True).stdout


def mask(raw: bytes) -> str:
    s = raw.decode("utf-8", "replace")
    return s if len(s) <= 12 else f"{s[:5]}…{s[-3:]}（{len(s)} 字符）"


def scan_bytes(data: bytes, where: str) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    if b"\x00" in data[:8000]:
        return out
    for label, pat in PATTERNS:
        for m in pat.finditer(data):
            text = m.group(0)
            if ALLOW.search(text):
                continue
            out.append((label, where, mask(text)))
    return out


def scan_worktree() -> tuple[list[tuple[str, str, str]], int]:
    names = git("ls-files").decode("utf-8", "replace").splitlines()
    findings: list[tuple[str, str, str]] = []
    scanned = 0
    for name in names:
        path = Path(name)
        if path.suffix.lower() not in TEXT_SUFFIXES and path.suffix:
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if len(data) > MAX_BYTES:
            continue
        scanned += 1
        findings += scan_bytes(data, name)
    return findings, scanned


def scan_history() -> tuple[list[tuple[str, str, str]], int]:
    raw = git("rev-list", "--objects", "--all").decode("utf-8", "replace")
    obj_to_paths: dict[str, set[str]] = defaultdict(set)
    for line in raw.splitlines():
        parts = line.split(" ", 1)
        if len(parts) == 2:
            obj_to_paths[parts[0]].add(parts[1])

    findings: list[tuple[str, str, str]] = []
    scanned = 0
    for sha, paths in obj_to_paths.items():
        if git("cat-file", "-t", sha).decode().strip() != "blob":
            continue
        if int(git("cat-file", "-s", sha).decode().strip()) > MAX_BYTES:
            continue
        data = git("cat-file", "blob", sha)
        if b"\x00" in data[:8000]:
            continue
        scanned += 1
        findings += scan_bytes(data, sorted(paths)[0])
    return findings, scanned


def main() -> int:
    ap = argparse.ArgumentParser(description="扫描仓库里的疑似密钥")
    ap.add_argument("--history", action="store_true",
                    help="连同全部 git 历史对象一起扫（仓库转公开前用）")
    args = ap.parse_args()

    findings, scanned = scan_worktree()
    scope = f"工作树 {scanned} 个文本文件"
    if args.history:
        hist, hscanned = scan_history()
        findings += hist
        scope += f" + 历史 {hscanned} 个文本对象"

    if not findings:
        print(f"未发现疑似密钥（已扫 {scope}）。")
        return 0

    # 同一个值可能在多处出现，去重后按类别汇总
    uniq: dict[tuple[str, str], set[str]] = defaultdict(set)
    for label, where, masked in findings:
        uniq[(label, masked)].add(where)

    print(f"疑似密钥 {len(uniq)} 处（已扫 {scope}）：\n")
    for (label, masked), places in sorted(uniq.items()):
        print(f"  【{label}】{masked}")
        for p in sorted(places)[:5]:
            print(f"        {p}")
    print("\n如果确实是误报（占位符/测试假值），把它加进本文件的 ALLOW 正则；")
    print("如果确实是真密钥，**先作废它**，再从代码与历史里清除（见 docs/DELIVERY.md §9）。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
