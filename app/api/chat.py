# -*- coding: utf-8 -*-
"""
对话接口：POST /chat（SSE 流式）+ GET /threads（会话列表）。

SSE 事件协议（前端对接文档）：
    thread    本轮会话标识 {thread_id, request_id}——客户端保存 thread_id 用于续接
    token     模型逐 token 输出 {content, node}（打字机效果的数据源）
    interrupt 图挂起等人审 {thread_id, payload, hint}——payload 里带 ticket_id，
              审批人调用 POST /approvals/{ticket_id}/decision 决策后图自动恢复
    message   非流式完整答复 {content}（本轮没有 token 流时的补充，如批量汇总节点）
    error     友好错误信息 {message}（含循环上限保护触发）
    done      本轮结束标志 {}

生产化要点（对比 langgraph_mvp/main.py 的 ask 函数）：
    1. thread_id 由服务端生成/校验，客户端只传"续接"标识
    2. GraphRecursionError 捕获后转 SSE error 事件，不让请求 500
    3. 中断（本接口）与恢复（审批接口 /approvals）职责分离，各走各的权限
"""
import json
import logging
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel, Field

from app.api.deps import verify_api_key
from app.config import settings
from app.log import request_id_var, thread_id_var

logger = logging.getLogger("support.api")

router = APIRouter(dependencies=[Depends(verify_api_key)])

# 只推送这些节点的正文 token（supervisor 的结构化输出没有正文）
# model = order_agent 子图里的模型节点（LangChain 1.x 的 create_agent 命名）
TOKEN_NODES = {"chat", "knowledge", "model"}


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000, description="用户问题")
    thread_id: str | None = Field(
        default=None, description="续接会话时传入；新会话留空由服务端生成"
    )


def sse(event: str, data: dict) -> str:
    """格式化一条 SSE 事件"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/chat")
async def chat(req: ChatRequest, request: Request):
    graph = request.app.state.graph
    thread_id = req.thread_id or uuid.uuid4().hex[:12]
    request_id = uuid.uuid4().hex[:8]
    # 写入日志上下文：本请求内所有日志自动带 thread/request 标识
    thread_id_var.set(thread_id)
    request_id_var.set(request_id)
    config = {"configurable": {"thread_id": thread_id},
              "recursion_limit": settings.recursion_limit}
    logger.info("收到请求 question=%r", req.question[:60])

    async def event_stream():
        yield sse("thread", {"thread_id": thread_id, "request_id": request_id})
        token_count = 0
        try:
            # subgraphs=True：让子图（order_agent）内部的 token 也浮出到父层，
            # 否则子图产出是聚合后的整条消息，拿不到打字机效果。
            # 此时每条产出是 (namespace, (chunk, metadata)) 三元结构
            async for _ns, (chunk, meta) in graph.astream(
                {"messages": [HumanMessage(req.question)]},
                config,
                stream_mode="messages",
                subgraphs=True,
            ):
                if chunk.content and meta.get("langgraph_node") in TOKEN_NODES:
                    token_count += 1
                    yield sse("token", {
                        "content": chunk.content,
                        "node": meta.get("langgraph_node"),
                    })
            # 流结束：检查图是否挂起（interrupt 等人审）
            snap = await graph.aget_state(config)
            if snap.next:
                payload = None
                for task in snap.tasks:
                    if task.interrupts:
                        payload = task.interrupts[0].value
                        break
                logger.info("图挂起等待人审 payload=%s", payload)
                yield sse("interrupt", {
                    "thread_id": thread_id,
                    "payload": payload,
                    "hint": (
                        f"审批人调用 POST /approvals/{payload.get('ticket_id')}/decision，"
                        f'body: {{"decision": "approve|reject", "approver": "姓名"}}，'
                        f"图会从断点自动恢复"
                    ) if isinstance(payload, dict) else "调用审批接口恢复执行",
                })
            elif token_count == 0:
                # 没有 token 流的路径（如 Send 并行后的 summarize 节点：
                # 它返回完整 AIMessage 而不是模型流式输出），补发最终答复
                last = snap.values.get("messages", [])
                if last:
                    yield sse("message", {"content": last[-1].content})
        except GraphRecursionError:
            logger.error("触发图循环上限 recursion_limit=%s", settings.recursion_limit)
            yield sse("error", {"message": "本次处理超过循环上限已终止，请换个说法重试。"})
        except Exception:
            logger.exception("处理请求发生未预期异常")
            yield sse("error", {"message": "服务内部错误，请稍后重试。"})
        yield sse("done", {})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/threads")
async def list_threads(request: Request):
    """会话列表：从检查点表取所有 thread_id 及其超步数（运维/排查用）"""
    from app.models.db import get_pool

    pool = get_pool()
    rows = await pool.fetch(
        "SELECT thread_id, COUNT(*) AS steps FROM checkpoints "
        "GROUP BY thread_id ORDER BY thread_id LIMIT 100"
    )
    return {"threads": [{"thread_id": r["thread_id"], "steps": r["steps"]} for r in rows]}
