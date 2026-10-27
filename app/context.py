# -*- coding: utf-8 -*-
"""
长会话上下文管理：消息超过阈值时，把早期对话压缩成一段摘要，控制 token 消耗。

为什么需要：SSE 会话可以聊几百轮，messages 无限增长会
    1. 每轮都把所有历史送给模型 -> token 费用线性上涨
    2. 最终撞上模型上下文上限，请求直接失败
做法：保留最近 K 条原文，更早的消息用 LLM 压成 200 字摘要（保留订单号、金额、结论等事实），
并用 LangGraph 的 RemoveMessage 把这些旧消息从检查点状态里真正删掉（不是每次重复摘要）。
"""
import logging

from langchain_core.messages import HumanMessage, RemoveMessage, SystemMessage

from app.config import settings

logger = logging.getLogger("support.context")

_SUMMARY_PROMPT = (
    "把下面这段历史对话压缩成不超过 200 字的中文摘要，"
    "必须保留：订单号、退款金额、已做出的承诺或结论。只输出摘要正文。"
)


def needs_compaction(message_count: int) -> bool:
    """纯判断函数（单测覆盖）：消息数量是否超过压缩阈值"""
    return message_count > settings.context_max_messages


async def compact_if_needed(messages: list, llm) -> dict:
    """按需压缩，返回要并入图状态的更新；无需压缩时返回空 dict

    返回值形如 {"messages": [RemoveMessage(...), ..., SystemMessage("[早期对话摘要] ...")]}
    """
    if not needs_compaction(len(messages)):
        return {}

    keep = settings.context_keep_recent
    old_messages = messages[:-keep]
    if not old_messages:
        return {}

    # 拼成纯文本转录（每条截断，避免摘要请求本身过长）
    lines = []
    for m in old_messages:
        role = getattr(m, "type", "unknown")
        content = str(getattr(m, "content", ""))[:200]
        lines.append(f"{role}: {content}")
    transcript = "\n".join(lines)

    resp = await llm.ainvoke([
        SystemMessage(_SUMMARY_PROMPT),
        HumanMessage(transcript),
    ])
    digest = SystemMessage(content=f"[早期对话摘要] {resp.content}")

    # RemoveMessage：把旧消息从检查点状态里删除，之后不会每轮重复摘要
    removals = [
        RemoveMessage(id=m.id) for m in old_messages if getattr(m, "id", None)
    ]
    logger.info(
        "上下文压缩：原 %d 条 -> 删除 %d 条，保留最近 %d 条",
        len(messages), len(removals), keep,
    )
    return {"messages": [*removals, digest]}
