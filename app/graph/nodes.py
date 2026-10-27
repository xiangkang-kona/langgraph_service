# -*- coding: utf-8 -*-
"""
图的节点定义（从 langgraph_mvp/graph.py 拆出，P0-P2 逐步改造）：

节点清单：
    supervisor  监督者：结构化输出做路由决策（多智能体的"大脑"），顺带触发长会话压缩
    chat        直答节点：闲聊
    knowledge   RAG 分支：查 pgvector 知识库后作答（见 app/rag/knowledge.py）
    track_one   Send 并行分支：一个实例查一个订单（map 阶段）
    summarize   并行汇合后的汇总节点（reduce 阶段）
    order_agent 子图：create_react_agent 预置 ReAct（见 agent.py）

改造要点：
    1. 全异步 + LLM 调用包 llm_retry 重试
    2. 提示词从 prompts.toml 读取（P2），改文案不用改代码
    3. 长会话在 supervisor 入口处按需压缩（app/context.py）
"""
import logging

from langchain_core.messages import AIMessage, SystemMessage
from langgraph.types import Send

from app.config import settings
from app.context import compact_if_needed
from app.graph.prompts import PROMPTS
from app.graph.state import RouteDecision, SupportState
from app.llm import llm
from app.models import repo
from app.resilience import llm_retry

logger = logging.getLogger("support.graph")

# 结构化输出的 LLM：返回值直接是 RouteDecision 实例，不需要解析自由文本
router_llm = llm.with_structured_output(RouteDecision)


# ---- 带应用层重试的 LLM 调用封装（tenacity 指数退避，见 app/resilience.py） ----

@llm_retry
async def _route_llm(messages):
    """监督者路由决策（带重试）"""
    return await router_llm.ainvoke(messages)


@llm_retry
async def _chat_llm(messages):
    """chat 直答（带重试）"""
    return await llm.ainvoke(messages)


async def supervisor_node(state: SupportState) -> dict:
    """监督者：先按需压缩长会话，再输出路由决策"""
    messages = state["messages"]

    # 长会话压缩：超阈值时把早期消息压成摘要并从状态中删除（只做一次，之后状态变小）
    compaction = await compact_if_needed(messages, llm)
    if compaction:
        keep = settings.context_keep_recent
        messages = [compaction["messages"][-1]] + messages[-keep:]  # 摘要 + 最近若干条

    decision = await _route_llm([SystemMessage(PROMPTS["supervisor"])] + messages)
    logger.info("路由决策 -> %s%s", decision.next,
                f" 订单: {decision.orders}" if decision.orders else "")
    return {**compaction, "route": decision.next, "orders": decision.orders}


async def chat_node(state: SupportState) -> dict:
    """普通节点：直接把历史发给模型生成回复"""
    resp = await _chat_llm([SystemMessage(PROMPTS["chat"])] + state["messages"])
    return {"messages": [resp]}


async def track_one_node(state: dict) -> dict:
    """map 阶段：一个实例只查一个订单。
    入参不是完整黑板，而是 Send 送来的 {"order_id": ...} 小字典——
    Send 的特点：给每个并行分支"私发"一份独立负载"""
    order_id = state["order_id"]
    info = await repo.get_logistics(order_id) or "暂无物流信息"
    logger.info("并行分支 track_one 查询 %s", order_id)
    # 写回 results 字段，多个分支同时写时由 operator.add reducer 拼接
    return {"results": [f"订单 {order_id}：{info}"]}


async def summarize_node(state: SupportState) -> dict:
    """reduce 阶段：等所有并行分支完成后才执行（框架保证），汇总成最终回复"""
    report = "\n".join(f"- {r}" for r in state["results"])
    text = f"已并行查询 {len(state['results'])} 个订单的物流状态：\n{report}"
    return {"messages": [AIMessage(text)]}


def route_from_supervisor(state: SupportState):
    """条件边的路由函数。返回值两种情况：
    1. 节点名（str）  -> 走静态边，如 "chat"、"knowledge"、"order_agent"
    2. Send 列表      -> 动态并行：运行时根据订单数创建 N 个分支实例
    """
    if state["route"] == "batch_logistics":
        return [Send("track_one", {"order_id": oid}) for oid in state["orders"]]
    return state["route"]
