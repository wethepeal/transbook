"""IR（中间表示）——阶段之间的唯一契约。

设计原则见 `docs/plan.md` §3/§5：
* **格式中立**：EPUB 与 PDF 抽取都产出同一套结构；
* **稳定 ID**：`Block.id` 一旦生成不再变化，翻译、审核回灌、断点续跑全靠它；
* **可序列化**：JSON 落盘，可人眼检查、可 diff、可单独重跑某阶段。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "transbook/ir@1"

BlockType = Literal["heading", "paragraph", "image", "footnote", "separator"]
SpanKind = Literal["emphasis", "strong", "ruby", "tcy", "link", "code"]
Origin = Literal["epub", "pdf"]


class InlineSpan(BaseModel):
    """行内语义片段（加粗/斜体/注音基文/縦中横等）。M1 只记录，渲染阶段再用。"""

    kind: SpanKind
    text: str = ""


class Block(BaseModel):
    """一个结构块。段落是最小翻译单元。"""

    id: str = Field(description="稳定 ID，形如 b000123")
    type: BlockType
    level: int | None = Field(default=None, description="heading 的层级（1 起）")
    text: str = Field(default="", description="纯文本（已剥离注音 rt、已归一化空白）")
    spans: list[InlineSpan] = Field(default_factory=list)
    src: str = Field(default="", description="来源：nav / body / bookmark")
    source_ref: str = Field(default="", description="来源文件或页码，便于回溯")
    path: str | None = Field(default=None, description="image 的相对路径")
    caption: str = ""


class TocEntry(BaseModel):
    """目录条目。EPUB 来自 nav/NCX，PDF 来自书签。"""

    level: int = 1
    title: str
    href: str = ""
    block_id: str | None = None


class DocMeta(BaseModel):
    id: str
    title: str = ""
    author: str = ""
    publisher: str = ""
    source_lang: str = ""
    origin: Origin = "epub"
    vertical: bool = Field(default=False, description="是否竖排（EPUB 由 CSS 判定）")
    ruby_dropped: int = Field(default=0, description="剥离掉的注音 <rt> 数量")
    page_count: int | None = None
    spine_docs: int = 0


class DocumentIR(BaseModel):
    """整本书的中间表示。"""

    schema_version: str = SCHEMA_VERSION
    doc: DocMeta
    toc: list[TocEntry] = Field(default_factory=list)
    blocks: list[Block] = Field(default_factory=list)

    # ── 便捷统计（供预览与验收使用）────────────────────────────────
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {"total": len(self.blocks)}
        for b in self.blocks:
            out[b.type] = out.get(b.type, 0) + 1
        return out

    def translatable(self) -> list[Block]:
        """需要送去翻译的块（标题 + 段落 + 脚注）。"""
        return [b for b in self.blocks if b.type in ("heading", "paragraph", "footnote") and b.text.strip()]
