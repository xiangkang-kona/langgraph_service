# -*- coding: utf-8 -*-
"""
审批接口：人审工单的查询与决策（P1 人审闭环的另一半）。

生产形态的关键点：图在 interrupt 处挂起后，
    - /chat 负责"发起"（返回 interrupt 事件 + ticket_id）
    - 本接口负责"决策并恢复"（Command(resume=...) 让图从断点继续）
两个动作分属不同接口、不同权限，审批留痕进审计表。
"""
import logging
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from langgraph.types import Command
from pydantic import BaseModel, Field

from app.api.deps import verify_api_key
from app.config import settings
from app.log import request_id_var, thread_id_var
from app.models import repo

logger = logging.getLogger("support.api")
router = APIRouter(dependencies=[Depends(verify_api_key)])


class DecisionRequest(BaseModel):
    decision: Literal["approve", "reject"] = Field(description="审批结论")
    approver: str = Field(min_length=1, max_length=50, description="审批人（写入审计日志）")


@router.get("/approvals")
async def list_approvals(status: str = "pending"):
    """审批工单列表（默认只看待审）"""
    tickets = await repo.list_tickets(status)
    return {"status": status, "count": len(tickets), "tickets": tickets}


@router.post("/approvals/{ticket_id}/decision")
async def decide(ticket_id: int, req: DecisionRequest, request: Request):
    """审批决策：更新工单并恢复挂起的图，返回用户最终收到的回复"""
    ticket = await repo.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="审批工单不存在")
    if ticket["status"] != "pending":
        raise HTTPException(
            status_code=409, detail=f"工单当前状态为 {ticket['status']}，不可重复审批"
        )

    thread_id = ticket["thread_id"]
    thread_id_var.set(thread_id)
    request_id_var.set(uuid.uuid4().hex[:8])
    logger.info("审批决策 ticket=%s decision=%s approver=%s",
                ticket_id, req.decision, req.approver)

    graph = request.app.state.graph
    config = {"configurable": {"thread_id": thread_id},
              "recursion_limit": settings.recursion_limit}
    try:
        result = await graph.ainvoke(
            Command(resume={"decision": req.decision, "approver": req.approver}),
            config,
        )
    except Exception:
        logger.exception("恢复图执行失败 ticket=%s", ticket_id)
        raise HTTPException(status_code=500, detail="恢复会话执行失败，请稍后重试")

    updated = await repo.get_ticket(ticket_id)
    return {
        "ticket_id": ticket_id,
        "status": updated["status"],
        "approver": updated["approver"],
        "answer": result["messages"][-1].content,
    }
