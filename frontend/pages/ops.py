# -*- coding: utf-8 -*-
"""
运行状态页：健康检查、会话规模、接口与限流说明。

排查思路：对话答不出来先看这里——db 是否 down、会话是否异常膨胀、
限流是否撞上（20 次/分钟/密钥）。
"""
import streamlit as st

from api_client import APIError
from ui import get_api, init_state, page_header

init_state()
api = get_api()

page_header("运行状态", "后端与数据库连通性、会话规模、接口与限流说明")


def probe() -> None:
    """探测健康状态与会话列表；失败信息直接展示，不阻断页面"""
    try:
        st.session_state.health = api.health()
    except APIError as exc:
        st.session_state.health = {"error": exc.message}
    try:
        st.session_state.threads = api.list_threads().get("threads", [])
    except APIError as exc:
        st.session_state.threads = {"error": exc.message}


if st.session_state.health is None:
    probe()


header_left, header_right = st.columns([3, 1])
with header_right:
    if st.button("重新检测", width="stretch"):
        probe()
        st.rerun()

health = st.session_state.health or {}
threads = st.session_state.threads or []

# ==================== 健康状态 ====================

if health.get("error"):
    st.markdown(
        f'<span class="badge badge-red">不可用</span> {health["error"]}',
        unsafe_allow_html=True,
    )
else:
    db_ok = health.get("db") == "ok"
    service_ok = health.get("status") == "ok"
    st.markdown(
        f'<span class="badge {"badge-green" if service_ok else "badge-amber"}">'
        f'服务 {health.get("status", "-")}</span>'
        f'<span class="badge {"badge-green" if db_ok else "badge-red"}">'
        f'数据库 {health.get("db", "-")}</span>',
        unsafe_allow_html=True,
    )

# ==================== 会话规模 ====================

if isinstance(threads, dict):
    st.error(threads.get("error", "会话列表获取失败"))
    threads = []

steps = [t.get("steps", 0) for t in threads]
metric_a, metric_b, metric_c = st.columns(3)
metric_a.metric("会话总数", f"{len(threads)}")
metric_b.metric("最大超步数", f"{max(steps) if steps else 0}")
metric_c.metric("接口地址", st.session_state.api_base)

if threads:
    st.dataframe(
        [
            {"会话 ID": t["thread_id"], "超步数": t["steps"]}
            for t in sorted(threads, key=lambda x: x.get("steps", 0), reverse=True)
        ],
        width="stretch",
        hide_index=True,
    )
    st.caption("超步数异常大通常意味着模型在工具循环里打转，可结合后端日志的 thread 字段排查。")
else:
    st.info("还没有会话记录，先在对话台问一句试试。")

# ==================== 说明 ====================

with st.container(border=True):
    st.markdown("#### 接口与限流")
    st.markdown(
        f"- 当前后端：`{st.session_state.api_base}`（可在侧边栏修改）\n"
        "- 认证：请求头 `X-API-Key`，与后端 `API_KEY` 环境变量一致\n"
        "- 限流：20 次/分钟/密钥，超限返回 429；刷新工单会一次拉三个状态，避免连续点击\n"
        "- 审批闭环：对话台触发退款 → 生成工单并挂起 → 审批工作台决策 → 图从断点恢复"
    )
