# -*- coding: utf-8 -*-
"""
图与节点单测：图结构、并行汇总、上下文压缩、退款工单流转、金额校验。

全部使用 fake 对象 / monkeypatch，不访问网络和数据库，CI 可直接跑。
"""
from decimal import Decimal

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from app.config import settings
from app.context import compact_if_needed, needs_compaction
from app.graph.agent import build_app
from app.graph.nodes import summarize_node
from app.tools import support_tools


class FakeLLM:
    """假的模型：只用于摘要场景，返回固定文本"""

    async def ainvoke(self, messages):
        return AIMessage(content="用户咨询了退款与物流问题，订单 A20250001 已退款 89 元。")


# ==================== 图结构 ====================

def test_graph_contains_all_nodes():
    graph = build_app(MemorySaver())
    nodes = set(graph.get_graph().nodes)
    assert {"supervisor", "chat", "knowledge", "order_agent", "track_one", "summarize"} <= nodes


# ==================== 节点逻辑 ====================

async def test_summarize_merges_parallel_results():
    """reduce 阶段：results 字段由 operator.add 拼接后汇总"""
    state = {"results": ["订单 A：已签收", "订单 B：运输中"]}
    out = await summarize_node(state)
    content = out["messages"][0].content
    assert "2 个订单" in content
    assert "订单 A：已签收" in content and "订单 B：运输中" in content


# ==================== 长会话压缩 ====================

def test_needs_compaction_threshold(monkeypatch):
    monkeypatch.setattr(settings, "context_max_messages", 4)
    assert not needs_compaction(4)
    assert needs_compaction(5)


async def test_compact_if_needed_removes_old_messages(monkeypatch):
    monkeypatch.setattr(settings, "context_max_messages", 6)
    monkeypatch.setattr(settings, "context_keep_recent", 2)
    messages = [
        HumanMessage(content=f"消息{i}", id=str(i)) for i in range(8)
    ]
    updates = await compact_if_needed(messages, FakeLLM())

    # 8 条、保留 2 条 -> 删除 6 条 + 1 条摘要
    removals = [m for m in updates["messages"] if m.__class__.__name__ == "RemoveMessage"]
    digest = updates["messages"][-1]
    assert len(removals) == 6
    assert digest.content.startswith("[早期对话摘要]")


async def test_compact_skipped_below_threshold(monkeypatch):
    monkeypatch.setattr(settings, "context_max_messages", 10)
    messages = [HumanMessage(content="hi", id="1")]
    assert await compact_if_needed(messages, FakeLLM()) == {}


# ==================== 退款工具：金额校验（服务端权威） ====================

def _patch_order(monkeypatch, amount="89.00"):
    async def fake_get_order(order_id):
        if order_id == "ZZZ":
            return None
        return {
            "order_id": order_id,
            "product": "不锈钢保温杯",
            "amount": Decimal(amount),
            "status": "已签收",
            "created_at": "2026-10-17",
        }

    monkeypatch.setattr("app.models.repo.get_order", fake_get_order)


async def test_refund_rejects_amount_over_order_total(monkeypatch):
    _patch_order(monkeypatch)
    out = await support_tools.submit_refund.ainvoke(
        {"order_id": "B20250002", "amount": 999, "reason": "溢出测试"}
    )
    assert "超过订单实付金额" in out


async def test_refund_rejects_non_positive_amount(monkeypatch):
    _patch_order(monkeypatch)
    out = await support_tools.submit_refund.ainvoke(
        {"order_id": "B20250002", "amount": 0, "reason": "零元测试"}
    )
    assert "必须大于 0" in out


async def test_refund_rejects_missing_order(monkeypatch):
    _patch_order(monkeypatch)
    out = await support_tools.submit_refund.ainvoke(
        {"order_id": "ZZZ", "amount": 1, "reason": "不存在"}
    )
    assert "不存在" in out


# ==================== 退款工具：工单流转（挂起 -> 审批 -> 落单） ====================

async def test_refund_approve_flow(monkeypatch):
    """模拟 interrupt 恢复：审批通过后应更新工单、写退款单、写审计"""
    calls: dict = {}
    _patch_order(monkeypatch)

    async def fake_create_ticket(thread_id, order_id, amount, reason, payload):
        calls["ticket_args"] = (thread_id, order_id, float(amount))
        return 7, True

    async def fake_finalize(ticket_id, decision, approver):
        calls["finalize"] = (ticket_id, decision, approver)

    async def fake_audit(actor, action, detail):
        calls.setdefault("audits", []).append(action)

    async def fake_insert_refund(order_id, amount, reason):
        calls["refund"] = (order_id, float(amount))

    monkeypatch.setattr("app.models.repo.create_or_get_pending_ticket", fake_create_ticket)
    monkeypatch.setattr("app.models.repo.finalize_ticket", fake_finalize)
    monkeypatch.setattr("app.models.repo.audit", fake_audit)
    monkeypatch.setattr("app.models.repo.insert_refund", fake_insert_refund)
    monkeypatch.setattr(
        "app.tools.support_tools.get_config",
        lambda: {"configurable": {"thread_id": "t-test"}},
    )
    # interrupt 被替换成直接返回人工决策（等价于图恢复时 interrupt() 的返回值）
    monkeypatch.setattr(
        "app.tools.support_tools.interrupt",
        lambda payload: {"decision": "approve", "approver": "张主管"},
    )

    out = await support_tools.submit_refund.ainvoke(
        {"order_id": "B20250002", "amount": 89, "reason": "商品破损"}
    )

    assert "已由 张主管 批准" in out
    assert calls["ticket_args"] == ("t-test", "B20250002", 89.0)
    assert calls["finalize"] == (7, "approve", "张主管")
    assert calls["refund"] == ("B20250002", 89.0)
    assert "refund_requested" in calls["audits"] and "refund_approved" in calls["audits"]


async def test_refund_reject_flow(monkeypatch):
    calls: dict = {}
    _patch_order(monkeypatch)

    async def fake_create_ticket(*args, **kwargs):
        return 8, False  # False = 工单已存在（恢复重跑）

    async def fake_finalize(ticket_id, decision, approver):
        calls["finalize"] = (ticket_id, decision, approver)

    async def fake_audit(actor, action, detail):
        calls.setdefault("audits", []).append(action)

    async def fake_insert_refund(*args, **kwargs):
        calls["refund"] = True

    monkeypatch.setattr("app.models.repo.create_or_get_pending_ticket", fake_create_ticket)
    monkeypatch.setattr("app.models.repo.finalize_ticket", fake_finalize)
    monkeypatch.setattr("app.models.repo.audit", fake_audit)
    monkeypatch.setattr("app.models.repo.insert_refund", fake_insert_refund)
    monkeypatch.setattr(
        "app.tools.support_tools.get_config",
        lambda: {"configurable": {"thread_id": "t-test"}},
    )
    monkeypatch.setattr(
        "app.tools.support_tools.interrupt",
        lambda payload: {"decision": "reject", "approver": "李经理"},
    )

    out = await support_tools.submit_refund.ainvoke(
        {"order_id": "B20250002", "amount": 89, "reason": "不想要了"}
    )

    assert "已被 李经理 拒绝" in out
    assert calls["finalize"] == (8, "reject", "李经理")
    assert "refund" not in calls          # 拒绝不落退款单
    assert "refund_requested" not in calls["audits"]  # 工单已存在时不重复记申请审计
    assert "refund_rejected" in calls["audits"]
