"""提示词模板（版本化）。

`PROMPT_VERSION` 会随每次翻译写进数据库：换提示词后可评估效果、可回滚，
也可只重译受影响的段落（计划书 §14.5）。
"""

from __future__ import annotations

from transbook.translate.base import BookContext, SegmentIn

PROMPT_VERSION = "v2"

#: 护栏重试时追加到用户消息里的强指令。
#: 实测模型偶发把长段原文原样返回（1/3216），此时**必须换更强的提示词**再试，
#: 原样重发同样的请求大概率得到同样的结果。
STRICT_SUFFIX = """
【重要】上一轮你把这些段落**原样返回了日文原文**，这是错误的。
必须逐段给出中文译文；即使是拟声词、专有名词或短句，也要译成中文
（拟声词可译为对应的中文拟声表达）。**禁止**在 `text` 字段里出现日文假名。
"""

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


def build_messages(items: list[SegmentIn], ctx: BookContext, *,
                   strict: bool = False) -> list[dict[str, str]]:
    """构造 OpenAI 兼容的 messages。`strict=True` 时追加"禁止原样返回原文"的强指令。"""
    import json

    payload = json.dumps([{"id": i.seg_id, "text": i.text} for i in items],
                         ensure_ascii=False, indent=None)
    dnt = "、".join(ctx.do_not_translate) if ctx.do_not_translate else "（无）"
    system = SYSTEM.format(
        source_lang=ctx.source_lang, target_lang=ctx.target_lang, title=ctx.title or "（未知）",
        author=ctx.author or "（未知）", style_hint=ctx.style_hint,
        glossary=ctx.glossary_block(), dnt=dnt,
    )
    if ctx.rolling_summary:
        system = f"{system}\n前文梗概（保持衔接与称谓一致）：\n{ctx.rolling_summary}\n"
    user = USER.format(n=len(items), target_lang=ctx.target_lang, payload=payload)
    if strict:
        user = f"{user}{STRICT_SUFFIX}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
