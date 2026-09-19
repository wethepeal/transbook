#!/usr/bin/env python3
"""Typst 中文排版核验（M0 实测 #5 的第二步）。

目的：不仅"能出 PDF"，还要证明 **中文字形真的渲染出来了**（不是豆腐块），
并导出页面图片供人眼复核（标点禁则、首行缩进）。

用法：uv run --with typst --with pypdfium2 --with fonttools python tools/typst_check.py
"""

from __future__ import annotations

import pathlib

OUT = pathlib.Path("data/work/_bench")
OUT.mkdir(parents=True, exist_ok=True)

FONT_FILES = [
    pathlib.Path(r"C:\Windows\Fonts\NotoSerifSC-VF.ttf"),
    pathlib.Path(r"C:\Windows\Fonts\NotoSansSC-VF.ttf"),
]


def font_families() -> list[tuple[str, str]]:
    """用 fontTools 读出字体的真实 family 名（用它才能被排版引擎正确匹配）。"""
    from fontTools.ttLib import TTFont

    found = []
    for path in FONT_FILES:
        if not path.exists():
            print(f"  ! 字体不存在: {path}")
            continue
        try:
            tt = TTFont(path, fontNumber=0, lazy=True)
            names = {}
            for rec in tt["name"].names:
                if rec.nameID in (1, 2, 16, 17):
                    try:
                        names.setdefault(rec.nameID, rec.toUnicode())
                    except Exception:
                        pass
            tt.close()
            found.append((path.name, names.get(16) or names.get(1) or "?"))
            print(f"  {path.name}: family={names.get(1)!r} typographic={names.get(16)!r} "
                  f"subfamily={names.get(2)!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"  ! 解析失败 {path.name}: {type(exc).__name__}: {exc}")
    return found


def build_typ(families: list[str]) -> pathlib.Path:
    body = families[0] if families else "SimSun"
    head = families[1] if len(families) > 1 else body
    doc = f"""#set page(paper: "a5", margin: (x: 16mm, y: 18mm), numbering: "1")
#set text(font: ("{body}",), size: 10.5pt, lang: "zh")
#set par(justify: true, first-line-indent: 2em, leading: 1.9em)
#show heading: set text(font: ("{head}",), size: 16pt)

= 第一章　排版基准测试

这是一段中文测试文本，用于验证排版效果：标点禁则（行首不应出现「，。、」等收尾标点）、首行缩进两个字符、中英文混排 MiXeD 123 与数字 2026 的间距，以及长段落的断行是否合理。为避免断行问题，这里刻意写长一些，让排版引擎必须处理自动换行与标点挤压。

「引号测试」、『书名号』、（括号）、——破折号、……省略号，均应正确显示。

日文假名混排：レム、アル、氷上決戦、ヤエ・テンゼン。英文与数字：Chapter 1, pp. 12–34, 100%。
"""
    src = OUT / "check.typ"
    src.write_text(doc, encoding="utf-8")
    return src


def main() -> None:
    print("① 读取本机 Noto SC 字体的真实 family 名：")
    fams = font_families()
    family_names = [f[1] for f in fams]
    print(f"   → 用于排版的 family: {family_names}")

    print("\n② Typst 编译：")
    import typst

    src = build_typ(family_names)
    pdf = OUT / "check.pdf"
    typst.compile(str(src), output=str(pdf))
    print(f"   {pdf.name}: {pdf.stat().st_size / 1024:.1f} KB")

    print("\n③ 用 pypdfium2 取回文字（验证字形是否真的写入 PDF）：")
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf)
    text = doc[0].get_textpage().get_text_range()
    flat = "".join(text.split())
    print(f"   页数={len(doc)}  首页字符数={len(flat)}")
    print(f"   前 80 字: {flat[:80]!r}")
    checks = {
        "中文标题": "排版基准测试" in flat,
        "正文中文": "标点禁则" in flat,
        "日文假名": "レム" in flat,
        "英文数字": "MiXeD" in flat and "2026" in flat,
        "标点": "「引号测试」" in flat and "……省略号" in flat,
    }
    for k, v in checks.items():
        print(f"   {'✓' if v else '✗'} {k}")

    print("\n④ 导出页面图片供人眼复核：")
    png = OUT / "check-p1.png"
    doc[0].render(scale=2.2).to_pil().save(png)
    print(f"   {png}  ({png.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
