"""提示词模板（版本化）。

`PROMPT_VERSION` 会随每次翻译写进数据库：换提示词后可评估效果、可回滚，
也可只重译受影响的段落（计划书 §14.5）。
"""

from __future__ import annotations

from transbook.translate.base import BookContext, SegmentIn

PROMPT_VERSION = "v1"

SYSTEM = """你是一名专业的{source_lang}→{target_lang}文学翻译。要求：
1. 忠实原意，译文自然流畅，符合{target_lang}书面语习惯；不要逐字硬译。
2. **保持段落与标题的对应关系**：只翻译，不合并、不拆分、不补充、不省略。
3. 人名、地名、专有名词全书统一；严格遵循下方术语表。
4. 保留原文中的数字、单位、符号与书名号/引号层级。
5. 只输出 JSON，不要任何解释或 Markdown 代码块。

作品：{title}　作者：{author}
文风要求：{style_hint}

术语表（必须遵守）：
{glossary}

不译项（原样保留）：
{dnt}
"""

USER = """请把下面 {n} 个段落翻译成{target_lang}。

输出格式（严格 JSON，不要代码块）：
{{"translations":[{{"id":"<原样返回的 id>","text":"<译文>"}}]}}

输入段落：
{payload}
"""


def build_messages(items: list[SegmentIn], ctx: BookContext) -> list[dict[str, str]]:
    """构造 OpenAI 兼容的 messages。"""
    import json

    payload = json.dumps([{"id": i.seg_id, "text": i.text} for i in items],
                         ensure_ascii=False, indent=None)
    dnt = "、".join(ctx.do_not_translate) if ctx.do_not_translate else "（无）"
    system = SYSTEM.format(
        source_lang=ctx.source_lang, target_lang=ctx.target_lang, title=ctx.title or "（未知）",
        author=ctx.author or "（未知）", style_hint=ctx.style_hint,
        glossary=ctx.glossary_block(), dnt=dnt,
    )
    user = USER.format(n=len(items), target_lang=ctx.target_lang, payload=payload)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
