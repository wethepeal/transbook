#!/usr/bin/env python3
"""中文 PDF 排版选型基准（M0 实测 #5）。

对比 WeasyPrint（HTML/CSS → PDF，BSD）与 Typst（Apache-2.0）在 Windows 上的：
  ① 能否装上（WeasyPrint 在 Windows 需要 Pango/GTK 运行库，是已知痛点）
  ② 中文渲染是否正常（标点禁则、首行缩进、中英混排）
  ③ 渲染耗时与产物大小

用法：uv run --with weasyprint --with typst python tools/render_bench.py
"""

from __future__ import annotations

import pathlib
import time

OUT = pathlib.Path("data/work/_bench")
OUT.mkdir(parents=True, exist_ok=True)

CJK_SERIF = '"Noto Serif CJK SC", "Source Han Serif SC", "SimSun", "MS Mincho", serif'
CJK_SANS = '"Noto Sans CJK SC", "Source Han Sans SC", "Microsoft YaHei", sans-serif'

HTML_DOC = f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><style>
@page {{ size: A5; margin: 18mm 16mm; @bottom-center {{ content: counter(page); font-size: 9pt; }} }}
body {{ font-family: {CJK_SERIF}; font-size: 10.5pt; line-height: 1.7; text-align: justify; }}
h1 {{ font-family: {CJK_SANS}; font-size: 16pt; margin: 0 0 1em; }}
p {{ text-indent: 2em; margin: 0 0 .45em; }}
</style></head><body>
<h1>第一章　排版基准测试</h1>
<p>这是一段中文测试文本，用于验证排版效果：标点禁则（行首不应出现「，。、」等收尾标点）、首行缩进两个字符、中英文混排 MiXeD 123 与数字 2026 的间距，以及长段落的断行是否合理。为避免断行问题，这里刻意写长一些，让排版引擎必须处理自动换行与标点挤压。</p>
<p>「引号测试」、『书名号』、（括号）、——破折号、……省略号，均应正确显示。</p>
<p>日文假名混排：レム、アル、氷上決戦、ヤエ・テンゼン。英文与数字：Chapter 1, pp. 12–34, 100%。</p>
</body></html>
"""

TYP_DOC = """#set page(paper: "a5", margin: (x: 16mm, y: 18mm), numbering: "1")
#set text(font: ("Noto Serif CJK SC", "Source Han Serif SC", "SimSun"), size: 10.5pt, lang: "zh")
#set par(justify: true, first-line-indent: 2em, leading: 1.9em)
#set heading(numbering: none)
= 第一章　排版基准测试

这是一段中文测试文本，用于验证排版效果：标点禁则（行首不应出现「，。、」等收尾标点）、首行缩进两个字符、中英文混排 MiXeD 123 与数字 2026 的间距，以及长段落的断行是否合理。为避免断行问题，这里刻意写长一些，让排版引擎必须处理自动换行与标点挤压。

「引号测试」、『书名号』、（括号）、——破折号、……省略号，均应正确显示。

日文假名混排：レム、アル、氷上決戦、ヤエ・テンゼン。英文与数字：Chapter 1, pp. 12–34, 100%。
"""


def bench(name: str, fn) -> dict:
    out = OUT / f"{name}.pdf"
    t0 = time.perf_counter()
    try:
        fn(out)
        dt = time.perf_counter() - t0
        size = out.stat().st_size if out.exists() else 0
        return {"name": name, "ok": True, "sec": dt, "kb": size / 1024,
                "head": out.read_bytes()[:5]}
    except Exception as exc:  # noqa: BLE001 - 基准脚本，需捕获一切以便对比
        return {"name": name, "ok": False, "sec": time.perf_counter() - t0,
                "err": f"{type(exc).__name__}: {exc}"[:200]}


def run_weasyprint(out: pathlib.Path) -> None:
    from weasyprint import HTML  # 延迟导入：未安装时也要能跑另一半

    HTML(string=HTML_DOC).write_pdf(str(out))


def run_typst(out: pathlib.Path) -> None:
    import typst  # 延迟导入

    src = OUT / "doc.typ"
    src.write_text(TYP_DOC, encoding="utf-8")
    typst.compile(str(src), output=str(out))


if __name__ == "__main__":
    print(f"字体候选: {CJK_SERIF}")
    rows = [bench("weasyprint", run_weasyprint), bench("typst", run_typst)]
    print("\n" + "=" * 74)
    print(f"{'引擎':<14}{'结果':<8}{'耗时(秒)':>10}{'大小(KB)':>12}  备注")
    for r in rows:
        if r["ok"]:
            pdf_ok = r["head"] == b"%PDF-"
            print(f"{r['name']:<14}{'OK' if pdf_ok else '非法PDF':<8}{r['sec']:>10.2f}{r['kb']:>12.1f}  "
                  f"{'PDF 头正常' if pdf_ok else r['head']!r}")
        else:
            print(f"{r['name']:<14}{'FAIL':<8}{r['sec']:>10.2f}{'-':>12}  {r['err']}")
    print("\n产物目录: " + str(OUT.resolve()))
