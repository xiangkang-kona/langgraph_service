# -*- coding: utf-8 -*-
"""
对话台：与售后智能助手流式对话。

一次提交的完整链路：
    st.chat_input -> api.stream_chat（SSE 生成器）
        -> token 逐段交给 st.write_stream 渲染（打字机）
        -> 结构化事件进 holder（thread_id / interrupt / error / nodes）
    流结束后：写回 thread_id、挂起工单、本轮分支徽标
"""
import time

import streamlit as st

from api_client import APIError
from ui import (
    get_api,
    init_state,
    node_badges,
    page_header,
    reset_session,
)

init_state()
api = get_api()

page_header("对话台", "退换货政策、订单物流、退款申请，都可以在这里直接问")

# ==================== 会话状态条 ====================

left, right = st.columns([3, 1])
with left:
    st.markdown(
        f'<div class="kv"><span class="kv-k">当前会话</span>'
        f'<span class="kv-v mono">{st.session_state.thread_id}</span></div>',
        unsafe_allow_html=True,
    )
    if st.session_state.round_nodes:
        st.markdown(node_badges(st.session_state.round_nodes), unsafe_allow_html=True)
with right:
    if st.button("重置会话", width="stretch"):
        reset_session()
        st.rerun()

# ==================== 示例话术：点击直接发送 ====================

# 每条对应一种能力，便于评审/演示时逐个点开看效果
EXAMPLES = [
    ("退换货政策", "退换货政策是什么？"),
    ("查订单物流", "查一下订单 B20250002 的物流"),
    ("批量查物流", "帮我查 A20250001 和 C20250003 的物流"),
    ("申请退款", "订单 A20250001 商品破损，帮我申请退款"),
]

st.caption("示例话术 · 点击直接发送")
example_cols = st.columns(len(EXAMPLES))
clicked_question = None
for col, (label, example_text) in zip(example_cols, EXAMPLES):
    if col.button(label, key=f"example_{label}", width="stretch", help=example_text):
        clicked_question = example_text

# 输入框固定渲染在页面底部，这里先取值即可；处理逻辑放在历史重放之后，
# 保证新问答追加在对话末尾，而不是插到页面顶部
typed_question = st.chat_input("也可以直接输入问题，例如：退换货政策是什么")
question = clicked_question or typed_question

# ==================== 历史消息重放 ====================

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message.get("nodes"):
            st.markdown(node_badges(message["nodes"]), unsafe_allow_html=True)


def lookup_decision(pending: dict) -> str | None:
    """按会话与订单号在已处理工单里找结论（审批结果只返回给审批接口，页面用状态反查）"""
    order_id = pending.get("订单号")
    for status in ("approved", "rejected"):
        for ticket in api.list_tickets(status).get("tickets", []):
            if ticket["thread_id"] == st.session_state.thread_id and ticket["order_id"] == order_id:
                return status
    return None


# ==================== 挂起中的审批工单 ====================

pending = st.session_state.pending
if pending:
    with st.container(border=True):
        st.markdown(
            f'<span class="badge badge-amber">待人工审批</span> 退款工单 '
            f'<span class="mono">#{pending.get("ticket_id")}</span> 已生成',
            unsafe_allow_html=True,
        )
        st.caption("资金操作需审批人决策，图已在此暂停；审批完成后会话可从断点继续。")

        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown(
                f'<div class="kv"><span class="kv-k">订单号</span>'
                f'<span class="kv-v mono">{pending.get("订单号", "-")}</span></div>'
                f'<div class="kv"><span class="kv-k">商品</span>'
                f'<span class="kv-v">{pending.get("商品", "-")}</span></div>',
                unsafe_allow_html=True,
            )
        with col_b:
            st.markdown(
                f'<div class="kv"><span class="kv-k">退款金额</span>'
                f'<span class="kv-v mono">{pending.get("退款金额", "-")}</span></div>'
                f'<div class="kv"><span class="kv-k">退款原因</span>'
                f'<span class="kv-v">{pending.get("退款原因", "-")}</span></div>',
                unsafe_allow_html=True,
            )

        act_a, act_b = st.columns(2)
        with act_a:
            st.page_link("pages/approvals.py", label="前往审批工作台")
        with act_b:
            if st.button("刷新审批状态", width="stretch"):
                try:
                    status = lookup_decision(pending)
                except APIError as exc:
                    st.error(exc.message)
                else:
                    if status:
                        st.session_state.pending = None
                        st.toast(f"该退款申请已{'批准' if status == 'approved' else '拒绝'}")
                        st.rerun()
                    else:
                        st.info("工单仍在审批中，请稍后再查。")

# ==================== 流式处理 ====================

if question:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    # SSE 生成器不能返回值，结构化事件全部收进 holder
    holder: dict = {}

    def collect(event: str, data: dict) -> None:
        holder[event] = data

    text = ""
    nodes: list[str] = []
    started = time.perf_counter()
    with st.chat_message("assistant"):
        try:
            text = st.write_stream(
                api.stream_chat(question, st.session_state.thread_id, collect)
            ) or ""
        except APIError as exc:
            st.error(exc.message)
        elapsed = time.perf_counter() - started

        nodes = (holder.get("done") or {}).get("nodes") or []
        if holder.get("error"):
            st.error(holder["error"].get("message", "服务返回错误"))
        if nodes:
            st.markdown(node_badges(nodes), unsafe_allow_html=True)
            st.caption(f"本轮耗时 {elapsed:.1f} 秒")

    # 首轮由服务端生成 thread_id，之后续接同一会话
    thread = holder.get("thread") or {}
    if thread.get("thread_id"):
        st.session_state.thread_id = thread["thread_id"]

    if text:
        st.session_state.messages.append(
            {"role": "assistant", "content": text, "nodes": nodes}
        )
        # 长会话只重放最近 50 条，避免页面重绘越来越慢
        st.session_state.messages = st.session_state.messages[-50:]

    interrupt = holder.get("interrupt")
    if interrupt:
        st.session_state.pending = interrupt.get("payload") or {}
        st.session_state.round_nodes = nodes
        st.rerun()
    elif nodes:
        st.session_state.round_nodes = nodes
