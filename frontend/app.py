# -*- coding: utf-8 -*-
"""
前端入口：全局页面配置、共享侧边栏（接口地址 / Key / 新会话）、三页导航。

启动（本地）：streamlit run app.py
部署：compose 里的 ui 服务，浏览器访问 http://localhost:8501
"""
import streamlit as st

from api_client import APIError
from ui import (
    DEFAULT_API_BASE,
    DEFAULT_API_KEY,
    get_api,
    init_state,
    inject_css,
    reset_session,
)

st.set_page_config(
    page_title="售后智能助手 · 运营工作台",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 状态与样式先就绪，后续页面脚本共用同一份 session_state
init_state()
inject_css()

with st.sidebar:
    st.markdown(
        '<div class="brand">'
        '<div class="brand-title">售后智能助手</div>'
        '<div class="brand-sub">内部运营工作台 · 对话 / 审批 / 运维</div>'
        "</div>",
        unsafe_allow_html=True,
    )

    # 直接绑定 session_state 的 key：改动即生效，无需额外回调
    st.text_input(
        "API 地址",
        key="api_base",
        placeholder=DEFAULT_API_BASE,
        help="容器内为 http://app:8000；本机运行为 http://127.0.0.1:8000",
    )
    st.text_input(
        "API Key",
        key="api_key",
        type="password",
        placeholder=DEFAULT_API_KEY,
    )

    # 输入框被清空是最常见的"连不上"原因，就地提示比报错更有用
    if not (st.session_state.api_base or "").strip():
        st.warning("API 地址为空，请填写或点「恢复默认」")

    def restore_defaults() -> None:
        """在回调里改 widget 值：脚本体内直接赋值会被 Streamlit 拒绝"""
        st.session_state.api_base = DEFAULT_API_BASE
        st.session_state.api_key = DEFAULT_API_KEY

    connect_col, reset_col = st.columns(2)
    with connect_col:
        if st.button("测试连接", width="stretch"):
            try:
                result = get_api().health()
                st.success(f"连接正常，数据库 {result.get('db')}")
            except APIError as exc:
                st.error(exc.message)
    with reset_col:
        st.button(
            "恢复默认",
            width="stretch",
            help="把地址与 Key 重置为部署时的默认值",
            on_click=restore_defaults,
        )

    st.divider()

    if st.button("新会话", width="stretch", type="primary"):
        reset_session()
        st.rerun()

    st.caption("内网/演示定位：API Key 可现场覆盖，请勿将本页面直接暴露公网。")

st.navigation(
    [
        st.Page("pages/chat.py", title="对话台", default=True),
        st.Page("pages/approvals.py", title="审批工作台"),
        st.Page("pages/ops.py", title="运行状态"),
    ]
).run()
