# -*- coding: utf-8 -*-
"""路由层单测：RouteDecision schema 约束 + 条件边派发逻辑（纯函数，不依赖网络/DB）"""
import pytest
from pydantic import ValidationError

from app.graph.nodes import route_from_supervisor
from app.graph.state import RouteDecision


def test_route_decision_accepts_all_branches():
    """四个分支名都是合法取值（knowledge 是 P2 新增的 RAG 分支）"""
    for branch in ["chat", "knowledge", "order_agent", "batch_logistics"]:
        assert RouteDecision(next=branch, orders=[]).next == branch


def test_route_decision_rejects_unknown_branch():
    """schema 是强约束：模型想编个新分支名直接被 pydantic 拦下"""
    with pytest.raises(ValidationError):
        RouteDecision(next="billing", orders=[])


def test_route_decision_orders_default_empty():
    assert RouteDecision(next="chat").orders == []


def test_static_routes():
    """非批量分支返回节点名，直接走静态边"""
    for branch in ["chat", "knowledge", "order_agent"]:
        assert route_from_supervisor({"route": branch, "orders": []}) == branch


def test_batch_logistics_dispatches_one_send_per_order():
    """map-reduce 的核心：订单数决定并行分支数"""
    sends = route_from_supervisor(
        {"route": "batch_logistics", "orders": ["A20250001", "B20250002", "C20250003"]}
    )
    assert len(sends) == 3
    assert [s.node for s in sends] == ["track_one"] * 3
    assert [s.arg for s in sends] == [
        {"order_id": "A20250001"},
        {"order_id": "B20250002"},
        {"order_id": "C20250003"},
    ]


def test_batch_logistics_without_orders_returns_empty_list():
    """没有订单号时不派发任何分支（监督者的 schema 已提示要抽全订单号）"""
    assert route_from_supervisor({"route": "batch_logistics", "orders": []}) == []
