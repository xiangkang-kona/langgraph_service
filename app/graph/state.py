# -*- coding: utf-8 -*-
"""
State（状态）定义（从 langgraph_mvp/state.py 移植，逻辑不变）。

LangGraph 的核心概念——所有节点共用的"公共黑板"：
    每个节点返回一个 dict，只写自己负责的字段，框架按每个字段的
    "reducer（合并规则）"合并进黑板。没有 reducer 的字段 = 整体覆盖。
"""
import operator
from typing import Annotated, Literal, TypedDict

from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


class SupportState(TypedDict):
    """主图的公共黑板：对话历史 + 业务数据混排"""

    # 对话历史。reducer 是 add_messages：后写的消息"追加"进列表而不是覆盖
    messages: Annotated[list, add_messages]

    # 监督者的路由决策：chat / order_agent / batch_logistics
    route: str

    # 批量物流查询时要拆分派发的订单号列表
    orders: list

    # 各并行节点的物流查询结果。reducer 是 operator.add：列表拼接。
    # Send 并行分支能同时写同一个字段的关键——两个分支同时到达时框架拼接结果
    results: Annotated[list, operator.add]


class RouteDecision(BaseModel):
    """监督者的输出 schema：with_structured_output 强制 LLM 按此结构输出，
    实际工作中路由、抽取、分类这类"要稳定格式"的环节都用这招"""

    next: Literal["chat", "knowledge", "order_agent", "batch_logistics"] = Field(
        description="处理该消息的分支：chat=闲聊；knowledge=售后政策细节（查知识库）；"
                    "order_agent=单个订单的查询或售后操作；batch_logistics=同时查询多个订单的物流"
    )
    orders: list[str] = Field(
        default_factory=list,
        description="消息中出现的所有订单号。仅 batch_logistics 时需要填写，其余留空",
    )
