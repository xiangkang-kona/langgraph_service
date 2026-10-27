# -*- coding: utf-8 -*-
"""
审批工作台：退款等人审工单的查看与决策。

页面与后端的分工：
    GET  /approvals?status=...      拉工单列表（session_state 缓存，避免每次 rerun 都请求）
    POST /approvals/{id}/decision   决策后图从断点恢复，返回用户最终看到的答复
"""
import streamlit as st

from api_client import APIError
from ui import fmt_amount, fmt_time, get_api, init_state, page_header

init_state()
api = get_api()

STATUS_LABEL = {"pending": "待审", "approved": "已批准", "rejected": "已拒绝"}
STATUS_BADGE = {"pending": "badge-amber", "approved": "badge-green", "rejected": "badge-red"}


def reload_tickets(statuses: tuple[str, ...] = ("pending", "approved", "rejected")) -> list[str]:
    """拉取指定状态的工单；返回错误提示列表（不抛异常，避免整页崩掉）"""
    cache = st.session_state.tickets or {}
    tickets, errors = {}, []
    for status in statuses:
        try:
            tickets[status] = api.list_tickets(status).get("tickets", [])
        except APIError as exc:
            tickets[status] = cache.get(status, [])
            errors.append(exc.message)
    st.session_state.tickets = tickets
    return errors


page_header("审批工作台", "资金类操作必须人工决策；决策后会话从断点恢复，全过程写入审计日志")

header_left, header_right = st.columns([3, 1])
with header_right:
    if st.button("刷新工单", width="stretch"):
        errors = reload_tickets()
        if errors:
            st.error(errors[0])
        st.rerun()

# 首屏加载一次，后续靠按钮刷新
if st.session_state.tickets is None:
    errors = reload_tickets()
    if errors:
        st.error(errors[0])

tickets = st.session_state.tickets or {}
pending_list = tickets.get("pending", [])
approved_list = tickets.get("approved", [])
rejected_list = tickets.get("rejected", [])

# ==================== 指标区 ====================

metric_a, metric_b, metric_c = st.columns(3)
metric_a.metric("待审工单", f"{len(pending_list)} 笔")
metric_b.metric(
    "待审金额合计",
    f"{sum(float(t['amount']) for t in pending_list):,.2f} 元",
)
metric_c.metric("已批准", f"{len(approved_list)} 笔")

# ==================== 决策结果条 ====================

result = st.session_state.decision_result
if result:
    approved = result.get("status") == "approved"
    close_col, body_col = st.columns([1, 6])
    with body_col:
        st.markdown(
            f'<span class="badge {"badge-green" if approved else "badge-red"}">'
            f'{"已批准" if approved else "已拒绝"}</span> '
            f'工单 <span class="mono">#{result.get("ticket_id")}</span> · 审批人 '
            f'{result.get("approver", "-")}',
            unsafe_allow_html=True,
        )
        st.markdown(f"> {result.get('answer', '')}")
        st.caption("以上是用户在对话中应看到的最终答复（图已从断点恢复执行）")
    with close_col:
        if st.button("关闭", key="close_result"):
            st.session_state.decision_result = None
            st.rerun()

tab_pending, tab_approved, tab_rejected = st.tabs(["待审", "已批准", "已拒绝"])

# ==================== 待审：卡片 + 决策 ====================

with tab_pending:
    if not pending_list:
        st.info("当前没有待审工单。")

    for ticket in pending_list:
        ticket_id = ticket["id"]
        amount_text = fmt_amount(ticket["amount"])
        with st.container(border=True):
            head_left, head_right = st.columns([3, 1])
            with head_left:
                st.markdown(
                    f'<span class="badge {STATUS_BADGE["pending"]}">待审</span> '
                    f'工单 <span class="mono">#{ticket_id}</span>',
                    unsafe_allow_html=True,
                )
            with head_right:
                st.markdown(
                    f'<div style="text-align:right" class="kv-v mono">{amount_text} 元</div>',
                    unsafe_allow_html=True,
                )

            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown(
                    f'<div class="kv"><span class="kv-k">订单号</span>'
                    f'<span class="kv-v mono">{ticket["order_id"]}</span></div>'
                    f'<div class="kv"><span class="kv-k">申请时间</span>'
                    f'<span class="kv-v">{fmt_time(ticket.get("created_at"))}</span></div>',
                    unsafe_allow_html=True,
                )
            with col_b:
                st.markdown(
                    f'<div class="kv"><span class="kv-k">原因</span>'
                    f'<span class="kv-v">{ticket["reason"]}</span></div>'
                    f'<div class="kv"><span class="kv-k">所属会话</span>'
                    f'<span class="kv-v mono">{ticket["thread_id"]}</span></div>',
                    unsafe_allow_html=True,
                )

            with st.expander("查看完整审批材料"):
                st.json(
                    {
                        "工单号": ticket_id,
                        "订单号": ticket["order_id"],
                        "退款金额": f"{amount_text} 元",
                        "退款原因": ticket["reason"],
                        "所属会话": ticket["thread_id"],
                        "申请时间": fmt_time(ticket.get("created_at")),
                        "决策方式": "approve=批准退款 / reject=拒绝退款",
                    }
                )

            approver = st.text_input(
                "审批人", key=f"approver_{ticket_id}", placeholder="填写姓名，写入审计日志"
            )

            confirm = st.session_state.confirm
            if confirm and confirm["ticket_id"] == ticket_id:
                action = "批准退款" if confirm["decision"] == "approve" else "拒绝退款"
                st.warning(f"确认{action}？工单 #{ticket_id}，金额 {amount_text} 元，提交后不可撤销。")
                yes_col, no_col = st.columns(2)
                with yes_col:
                    if st.button("确认提交", type="primary", key=f"ok_{ticket_id}",
                                 width="stretch"):
                        try:
                            decision_result = api.decide(
                                ticket_id, confirm["decision"], approver.strip()
                            )
                        except APIError as exc:
                            st.error(exc.message)
                        else:
                            st.session_state.decision_result = decision_result
                            st.session_state.confirm = None
                            reload_tickets()
                            st.rerun()
                with no_col:
                    if st.button("取消", key=f"cancel_{ticket_id}", width="stretch"):
                        st.session_state.confirm = None
                        st.rerun()
            else:
                approve_col, reject_col = st.columns(2)
                with approve_col:
                    if st.button("批准退款", type="primary", key=f"approve_{ticket_id}",
                                 width="stretch"):
                        if not approver.strip():
                            st.warning("请先填写审批人姓名")
                        else:
                            st.session_state.confirm = {
                                "ticket_id": ticket_id,
                                "decision": "approve",
                            }
                            st.rerun()
                with reject_col:
                    if st.button("拒绝", key=f"reject_{ticket_id}", width="stretch"):
                        if not approver.strip():
                            st.warning("请先填写审批人姓名")
                        else:
                            st.session_state.confirm = {
                                "ticket_id": ticket_id,
                                "decision": "reject",
                            }
                            st.rerun()


def render_history(rows: list[dict], status: str) -> None:
    """历史表格：列固定、金额转 float、时间格式化"""
    if not rows:
        st.info(f"暂无{STATUS_LABEL[status]}工单。")
        return
    st.dataframe(
        [
            {
                "工单号": f"#{r['id']}",
                "订单号": r["order_id"],
                "金额（元）": float(r["amount"]),
                "原因": r["reason"],
                "审批人": r.get("approver") or "-",
                "申请时间": fmt_time(r.get("created_at")),
            }
            for r in rows
        ],
        width="stretch",
        hide_index=True,
    )


with tab_approved:
    render_history(approved_list, "approved")

with tab_rejected:
    render_history(rejected_list, "rejected")
