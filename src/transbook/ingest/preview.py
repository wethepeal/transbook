"""IR → Markdown 预览。

这是**最重要的人工检查闸门**（计划书 §6.1）：结构错了，翻译得再好也白费。
翻译前先出 Markdown 让人过一眼——成本近零，却能挡住绝大多数结构性问题。
"""

from __future__ import annotations

from transbook.ir import DocumentIR
from transbook.textutil import SKIP_MATTER


def to_markdown(ir: DocumentIR, limit: int | None = None, with_toc: bool = True) -> str:
    """把 IR 渲染成带统计头的 Markdown。"""
    c = ir.counts()
    out: list[str] = []
    out.append(f"# {ir.doc.title or '(无标题)'}")
    out.append("")
    out.append("| 项 | 值 |")
    out.append("|---|---|")
    out.append(f"| 来源 | {ir.doc.origin} ｜ 语言 {ir.doc.source_lang or '?'} ｜ "
               f"竖排 {'是' if ir.doc.vertical else '否'} |")
    out.append(f"| 作者 / 出版 | {ir.doc.author or '?'} / {ir.doc.publisher or '?'} |")
    out.append(f"| 篇幅 | {ir.doc.spine_docs} 个文档 ｜ {c.get('total', 0)} 个块 |")
    out.append(f"| 块构成 | 标题 {c.get('heading', 0)} ｜ 段落 {c.get('paragraph', 0)} ｜ "
               f"图片 {c.get('image', 0)} |")
    out.append(f"| 剥离注音 | {ir.doc.ruby_dropped} 处 `<rt>` |")
    out.append(f"| 可翻译块 | {len(ir.translatable())} |")
    # 前后附页归类：非 main 的类别单独列出，便于决定是否翻译（版权页/广告默认跳过）
    mc = ir.matter_counts()
    if set(mc) - {"main"}:
        parts = " ｜ ".join(f"{k} {v}" for k, v in sorted(mc.items()) if k != "main")
        n_skip = len(ir.skipped_matter_blocks(SKIP_MATTER))
        out.append(f"| 附页归类 | {parts} |")
        out.append(f"| 默认跳过翻译 | {n_skip} 块（{'、'.join(SKIP_MATTER)}） |")
    out.append("")

    if with_toc and ir.toc:
        out.append("## 目录（来自 NAV/书签）")
        out.append("")
        for t in ir.toc:
            indent = "  " * max(t.level - 1, 0)
            link = f" → `{t.block_id}`" if t.block_id else ""
            out.append(f"{indent}- {t.title}{link}")
        out.append("")

    out.append("## 正文")
    out.append("")
    n = 0
    for b in ir.blocks:
        if limit is not None and n >= limit:
            out.append(f"\n…（此处省略 {len(ir.blocks) - n} 个块，用 `--limit` 调整）")
            break
        if b.type == "heading":
            out.append(f"{'#' * min((b.level or 1) + 1, 6)} {b.text}")
            out.append("")
        elif b.type == "paragraph":
            out.append(b.text)
            out.append("")
        elif b.type == "image":
            out.append(f"![{b.caption or 'image'}]({b.path})")
            out.append("")
        elif b.type == "footnote":
            out.append(f"> [脚注] {b.text}")
            out.append("")
        else:
            out.append(f"<!-- {b.type} -->")
            out.append("")
        n += 1
    return "\n".join(out)


def summarize(ir: DocumentIR) -> str:
    """一行摘要，便于 CLI 输出。"""
    c = ir.counts()
    return (f"块 {c.get('total', 0)}（标题 {c.get('heading', 0)} / 段落 {c.get('paragraph', 0)} / "
            f"图片 {c.get('image', 0)}）｜目录 {len(ir.toc)} 条｜"
            f"可翻译 {len(ir.translatable())}｜剥离注音 {ir.doc.ruby_dropped}")
