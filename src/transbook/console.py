"""控制台输出的编码兜底。

**问题**：Windows 上 stdout 被**重定向**（管道、写文件、CI 采集）时，Python 按 ANSI
代码页编码——简体中文机器上是 GBK，英文机器上是 cp1252。GBK 装不下 `✓`(U+2713)
这类符号，cp1252 连中文都装不下，一 `print` 就抛 `UnicodeEncodeError`，整条命令
以非零码退出。直接输出到真实控制台时走的是控制台 Unicode API，没有这个问题，
所以这个坑只在**管道 / 重定向 / CI** 里踩得到。

**为什么单独抽出来**：这个坑在本项目已经踩过三次（`cli.py`、`tools/build_release.py`、
`tools/check_secrets.py`），每次都是"写新脚本时忘了加"。抽成一个函数，别再指望
下次记得住。

用法：在任何会往标准输出打非 ASCII 的脚本顶部调用一次即可。

    from transbook.console import tolerate_unencodable_output

    tolerate_unencodable_output()
"""

from __future__ import annotations

import sys


def tolerate_unencodable_output() -> None:
    """把标准输出的编码错误策略改成 `replace`，让命令不因"印不出来"而失败。

    是兜底而不是替代：新增输出仍应优先用目标编码装得下的字符。
    对已经是 UTF-8 的环境（Linux/macOS/设置了 PYTHONIOENCODING）这是空操作。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):  # 已被包装过 / 不支持重配置
            pass
