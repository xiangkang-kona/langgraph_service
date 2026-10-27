# -*- coding: utf-8 -*-
"""
数据访问层：所有业务 SQL 集中在此。

分层理由：工具（tools/）和接口（api/）只调用函数、不写 SQL——
换表结构只改这里，且 SQL 集中便于审查（防注入：全部参数化查询）。
"""
import json
import logging

from app.models.db import get_pool

logger = logging.getLogger("support.repo")


# ==================== 业务数据 ====================

async def get_order(order_id: str) -> dict | None:
    """查订单（退款金额校验的权威来源）"""
    row = await get_pool().fetchrow(
        "SELECT order_id, product, amount, status, created_at FROM orders WHERE order_id = $1",
        order_id,
    )
    return dict(row) if row else None


async def get_logistics(order_id: str) -> str | None:
    """查物流状态"""
    return await get_pool().fetchval(
        "SELECT status FROM logistics WHERE order_id = $1", order_id
    )


async def insert_refund(order_id: str, amount: float, reason: str) -> None:
    """审批通过后落退款单"""
    await get_pool().execute(
        "INSERT INTO refunds (order_id, amount, reason) VALUES ($1, $2, $3)",
        order_id, amount, reason,
    )


# ==================== 审批工单 ====================

async def create_or_get_pending_ticket(
    thread_id: str, order_id: str, amount: float, reason: str, payload: dict
) -> tuple[int, bool]:
    """创建审批工单，返回 (ticket_id, 是否新建)。

    幂等关键：interrupt 恢复时工具函数会从头重跑，若直接 INSERT 会产生重复工单。
    这里靠 approvals 上的部分唯一索引（thread_id + order_id，仅 status='pending'）
    做 ON CONFLICT 复用；RETURNING 里的 xmax = 0 是 PostgreSQL 的判断技巧：
    为 0 表示本次是真正插入（不是命中冲突更新）。
    """
    row = await get_pool().fetchrow(
        """
        INSERT INTO approvals (thread_id, order_id, amount, reason, payload)
        VALUES ($1, $2, $3, $4, $5::jsonb)
        ON CONFLICT (thread_id, order_id) WHERE status = 'pending'
        DO UPDATE SET payload = EXCLUDED.payload
        RETURNING id, (xmax = 0) AS created
        """,
        thread_id, order_id, amount, reason, json.dumps(payload, ensure_ascii=False),
    )
    return row["id"], row["created"]


async def finalize_ticket(ticket_id: int, decision: str, approver: str) -> None:
    """审批落定：更新工单状态与审批人"""
    status = "approved" if decision == "approve" else "rejected"
    await get_pool().execute(
        "UPDATE approvals SET status = $2, approver = $3, decided_at = now() WHERE id = $1",
        ticket_id, status, approver,
    )


async def get_ticket(ticket_id: int) -> dict | None:
    row = await get_pool().fetchrow(
        "SELECT id, thread_id, order_id, amount, reason, status, approver, "
        "created_at, decided_at FROM approvals WHERE id = $1",
        ticket_id,
    )
    return dict(row) if row else None


async def list_tickets(status: str, limit: int = 50) -> list[dict]:
    rows = await get_pool().fetch(
        "SELECT id, thread_id, order_id, amount, reason, status, approver, "
        "created_at FROM approvals WHERE status = $1 ORDER BY id DESC LIMIT $2",
        status, limit,
    )
    return [dict(r) for r in rows]


# ==================== 知识库（pgvector 检索） ====================

async def search_knowledge(query_vec: list[float], top_k: int = 3) -> list[dict]:
    """余弦距离 top-k 检索：<=> 越小越相似，配合 knowledge 表上的 HNSW 索引"""
    vec_literal = "[" + ",".join(f"{v:.6f}" for v in query_vec) + "]"
    rows = await get_pool().fetch(
        "SELECT id, content, source, embedding <=> $1::vector AS distance "
        "FROM knowledge ORDER BY embedding <=> $1::vector LIMIT $2",
        vec_literal, top_k,
    )
    return [dict(r) for r in rows]


# ==================== 审计日志 ====================

async def audit(actor: str, action: str, detail: dict) -> None:
    """写审计日志：谁、何时、做了什么（资金类操作必备）"""
    await get_pool().execute(
        "INSERT INTO audit_log (actor, action, detail) VALUES ($1, $2, $3::jsonb)",
        actor, action, json.dumps(detail, ensure_ascii=False),
    )
