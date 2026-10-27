# -*- coding: utf-8 -*-
"""
图的组装（从 langgraph_mvp/graph.py 拆出，P0-P2 逐步改造）：
    compile 时不再写死 MemorySaver，而是接收外部传入的 checkpointer——
    由应用 lifespan 创建 AsyncPostgresSaver 后注入（会话跨重启、多实例共享）。

整体结构（多智能体监督者模式）：
    START -> supervisor -+-> chat ---------> END
                        +-> knowledge -----> END（pgvector RAG 分支）
                        +-> order_agent --> END（子图：预置 ReAct 循环）
                        +-> track_one xN -> summarize -> END（Send 动态并行）
"""
from langchain.agents import create_agent
from langgraph.graph import END, START, StateGraph

from app.graph.nodes import (
    chat_node,
    route_from_supervisor,
    summarize_node,
    supervisor_node,
    track_one_node,
)
from app.graph.prompts import PROMPTS
from app.graph.state import SupportState
from app.llm import llm
from app.rag.knowledge import knowledge_node
from app.tools.support_tools import SUPPORT_TOOLS


def build_app(checkpointer):
    """组装并编译总图。checkpointer 由调用方注入（生产=AsyncPostgresSaver）"""

    # create_agent：预置组件，一个开箱即用的完整 ReAct 图（模型<->工具循环），
    # 自己就是一张图，直接作为父图节点——"图套图"就是多智能体系统的标准拼法。
    # （LangChain 1.x 起由 create_react_agent 迁移至此，后者已废弃）
    order_agent = create_agent(
        model=llm,
        tools=SUPPORT_TOOLS,
        system_prompt=PROMPTS["agent"],
    )

    builder = StateGraph(SupportState)
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("chat", chat_node)
    builder.add_node("knowledge", knowledge_node)     # RAG 分支
    builder.add_node("order_agent", order_agent)      # 子图直接当节点用
    builder.add_node("track_one", track_one_node)
    builder.add_node("summarize", summarize_node)

    builder.add_edge(START, "supervisor")
    # 条件边：路由函数决定去哪个分支（或派发多少个 Send 并行分支）。
    # 第三个参数是分支目标声明：让 get_graph().draw_ascii() 能画出完整拓扑
    builder.add_conditional_edges(
        "supervisor",
        route_from_supervisor,
        ["chat", "knowledge", "order_agent", "track_one"],
    )
    builder.add_edge("chat", END)
    builder.add_edge("knowledge", END)
    builder.add_edge("order_agent", END)
    builder.add_edge("track_one", "summarize")        # 所有并行分支汇合后进汇总
    builder.add_edge("summarize", END)

    return builder.compile(checkpointer=checkpointer)
