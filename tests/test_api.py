# -*- coding: utf-8 -*-
"""
接口层单测：认证、参数校验、SSE 事件协议、审批接口状态码。

用 FakeGraph 注入 app.state.graph，完全绕开真实图 / LLM / 数据库。
"""
import json
from types import SimpleNamespace

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage, AIMessageChunk

from app.api import approvals, chat
from app.config import settings

HEADERS = {"X-API-Key": settings.api_key}


class FakeGraph:
    """假图：可控地产生 token 流与挂起状态"""

    def __init__(self, tokens=(), state=None):
        self.tokens = tokens
        self.state = state or SimpleNamespace(next=(), values={"messages": []}, tasks=[])

    async def astream(self, inputs, config, stream_mode=None, subgraphs=False):
        for t in self.tokens:
            yield ((), (AIMessageChunk(content=t), {"langgraph_node": "chat"}))

    async def aget_state(self, config):
        return self.state


def make_app(graph) -> FastAPI:
    """独立构建 app：不跑 lifespan（不需要数据库），只测路由逻辑"""
    app = FastAPI()
    app.include_router(chat.router)
    app.include_router(approvals.router)
    app.state.graph = graph
    return app


def parse_sse(body: str) -> dict[str, list]:
    events: dict[str, list] = {}
    event = None
    for line in body.splitlines():
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: ") and event:
            events.setdefault(event, []).append(json.loads(line[6:]))
            event = None
    return events


async def post_chat(app, payload, headers=HEADERS):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return await c.post("/chat", json=payload, headers=headers)


# ==================== 认证与参数校验 ====================

async def test_rejects_missing_api_key():
    r = await post_chat(make_app(FakeGraph()), {"question": "你好"}, headers={})
    assert r.status_code == 401


async def test_rejects_empty_question():
    r = await post_chat(make_app(FakeGraph()), {"question": ""})
    assert r.status_code == 422


# ==================== /chat SSE 事件 ====================

async def test_chat_streams_tokens_and_thread():
    app = make_app(FakeGraph(tokens=["你", "好"]))
    r = await post_chat(app, {"question": "你好"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")

    events = parse_sse(r.text)
    assert events["token"][0]["content"] == "你"
    assert len(events["thread"]) == 1
    assert events["thread"][0]["thread_id"]
    assert "done" in events


async def test_chat_emits_interrupt_event_when_suspended():
    """图挂起时要把工单信息透传给前端，且不能再发 message/done 之外的东西"""
    interrupt = SimpleNamespace(value={"ticket_id": 5, "订单号": "B20250002"})
    state = SimpleNamespace(
        next=("tools",), values={"messages": []},
        tasks=[SimpleNamespace(interrupts=[interrupt])],
    )
    events = parse_sse((await post_chat(make_app(FakeGraph(state=state)), {"question": "退款"})).text)

    assert events["interrupt"][0]["payload"]["ticket_id"] == 5
    assert "approvals/5/decision" in events["interrupt"][0]["hint"]
    assert "done" in events


async def test_chat_falls_back_to_message_event_without_tokens():
    """summarize 这类节点不产生 token 流，需要以 message 事件补发最终答复"""
    state = SimpleNamespace(
        next=(), values={"messages": [AIMessage(content="已并行查询 2 个订单")]}, tasks=[]
    )
    events = parse_sse((await post_chat(make_app(FakeGraph(state=state)), {"question": "批量查"})).text)
    assert events["message"][0]["content"] == "已并行查询 2 个订单"


# ==================== /threads 与 /approvals ====================

async def test_threads_requires_key():
    app = make_app(FakeGraph())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/threads")).status_code == 401


async def test_approval_404_when_ticket_missing(monkeypatch):
    async def fake_get_ticket(ticket_id):
        return None

    monkeypatch.setattr("app.api.approvals.repo.get_ticket", fake_get_ticket)
    app = make_app(FakeGraph())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post(
            "/approvals/999/decision",
            json={"decision": "approve", "approver": "张主管"},
            headers=HEADERS,
        )
    assert r.status_code == 404


async def test_approval_409_when_already_decided(monkeypatch):
    async def fake_get_ticket(ticket_id):
        return {"id": 1, "thread_id": "t", "status": "approved"}

    monkeypatch.setattr("app.api.approvals.repo.get_ticket", fake_get_ticket)
    app = make_app(FakeGraph())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post(
            "/approvals/1/decision",
            json={"decision": "approve", "approver": "张主管"},
            headers=HEADERS,
        )
    assert r.status_code == 409


async def test_approval_validates_decision_value():
    app = make_app(FakeGraph())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post(
            "/approvals/1/decision",
            json={"decision": "maybe", "approver": "张主管"},
            headers=HEADERS,
        )
    assert r.status_code == 422
