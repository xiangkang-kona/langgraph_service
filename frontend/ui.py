# -*- coding: utf-8 -*-
"""
前端公共层：会话状态初始化、主题样式注入、展示格式化。

Streamlit 的关键约束：每次交互都会重跑整个脚本。因此所有跨轮状态必须落在
st.session_state 里，模块级变量每轮都会重建。
"""
import os
import uuid
from datetime import datetime

import streamlit as st

from api_client import SupportAPI

# 后端 SSE 里的节点名 -> 面向业务的叫法
NODE_LABELS = {
    "chat": "闲聊",
    "knowledge": "知识库",
    "model": "订单专员",
}

# 容器内由 compose 注入 API_BASE=http://app:8000；本机开发默认回环
DEFAULT_API_BASE = os.getenv("API_BASE", "http://127.0.0.1:8000")
DEFAULT_API_KEY = os.getenv("API_KEY", "dev-api-key-0001")


def init_state() -> None:
    """初始化全局会话状态（幂等，每个页面脚本开头都可调用）"""
    ss = st.session_state
    ss.setdefault("thread_id", uuid.uuid4().hex[:12])
    ss.setdefault("messages", [])        # [{"role","content","nodes"}]
    ss.setdefault("pending", None)       # 挂起的审批材料（interrupt payload）
    ss.setdefault("round_nodes", [])     # 最近一轮经过的分支
    ss.setdefault("api_base", DEFAULT_API_BASE)
    ss.setdefault("api_key", DEFAULT_API_KEY)
    ss.setdefault("tickets", None)       # {"pending":[...], "approved":[...], "rejected":[...]}
    ss.setdefault("confirm", None)       # 二次确认中的审批动作
    ss.setdefault("decision_result", None)
    ss.setdefault("health", None)
    ss.setdefault("threads", None)


def reset_session() -> None:
    """新会话：换 thread_id 并清空本地对话上下文（服务端检查点保留，便于追溯）"""
    st.session_state.thread_id = uuid.uuid4().hex[:12]
    st.session_state.messages = []
    st.session_state.pending = None
    st.session_state.round_nodes = []


def get_api() -> SupportAPI:
    """按当前侧边栏配置构造客户端（空配置在请求时由 SupportAPI 给出明确提示）"""
    return SupportAPI(
        (st.session_state.api_base or "").strip(),
        (st.session_state.api_key or "").strip(),
    )


def fmt_amount(value) -> str:
    """金额格式化：asyncpg 取回的 NUMERIC 是 Decimal，统一转 float 展示"""
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def fmt_time(value) -> str:
    """时间格式化：后端返回 datetime，空值显示占位符"""
    if isinstance(value, datetime):
        return value.strftime("%m-%d %H:%M")
    return str(value) if value else "-"


def node_badges(nodes: list[str]) -> str:
    """把节点名渲染成徽标 HTML（对话轮末展示"这轮走了哪条分支"）"""
    if not nodes:
        return ""
    parts = [
        f'<span class="badge badge-blue">{NODE_LABELS.get(n, n)}</span>' for n in nodes
    ]
    return "".join(parts)


def page_header(title: str, subtitle: str) -> None:
    """页面头部：渐变卡片，明确当前页面职责"""
    st.markdown(
        f'<div class="hero"><div class="hero-title">{title}</div>'
        f'<div class="hero-sub">{subtitle}</div></div>',
        unsafe_allow_html=True,
    )


def inject_css() -> None:
    """主题样式：深色侧边栏 + 浅色内容区 + 卡片化 + 克制动效

    Streamlit 内部 class 名随版本变化，这里只用稳定的 data-testid 选择器。
    """
    st.markdown(
        """
<style>
:root {
  --brand: #2563EB;
  --brand-dark: #1D4ED8;
  --ink: #0F172A;
  --muted: #475569;
  --line: #E2E8F0;
  --amber: #B45309;
  --green: #047857;
  --red: #B91C1C;
}

html, body, .stApp, [data-testid="stSidebar"] {
  font-family: "PingFang SC", "Microsoft YaHei", "Segoe UI", -apple-system, sans-serif;
}

.stApp { background: #F6F8FB; }

/* ---------- 侧边栏：深色渐变 ---------- */
[data-testid="stSidebar"] {
  background: linear-gradient(180deg, #0B1220 0%, #111C33 58%, #0F172A 100%);
  border-right: 1px solid rgba(226, 232, 240, 0.08);
}
[data-testid="stSidebar"] * { color: #E2E8F0; }
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p { color: #CBD5E1; }
[data-testid="stSidebar"] input {
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(226, 232, 240, 0.18);
  color: #F8FAFC;
  border-radius: 8px;
}
[data-testid="stSidebar"] input:focus { border-color: #60A5FA; box-shadow: none; }
[data-testid="stSidebar"] .stButton button {
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(226, 232, 240, 0.20);
  color: #F8FAFC;
  border-radius: 8px;
  transition: background 0.18s ease, border-color 0.18s ease, transform 0.18s ease;
}
[data-testid="stSidebar"] .stButton button:hover {
  background: var(--brand);
  border-color: var(--brand);
  color: #FFFFFF;
  transform: translateY(-1px);
}
[data-testid="stSidebar"] hr { border-color: rgba(226, 232, 240, 0.12); }

/* 侧边栏品牌块 */
.brand { padding: 6px 0 14px 0; }
.brand-title { font-size: 19px; font-weight: 600; color: #F8FAFC; letter-spacing: 0.4px; }
.brand-sub { font-size: 12px; color: #94A3B8; margin-top: 4px; }

/* ---------- 页面头部渐变卡 ---------- */
.hero {
  background: linear-gradient(120deg, #0F172A 0%, #1D4ED8 62%, #2563EB 100%);
  border-radius: 16px;
  padding: 22px 26px;
  margin-bottom: 18px;
  box-shadow: 0 14px 30px rgba(37, 99, 235, 0.18);
}
.hero-title { color: #FFFFFF; font-size: 26px; font-weight: 600; letter-spacing: 0.5px; }
.hero-sub { color: #DBEAFE; font-size: 13px; margin-top: 7px; }

/* ---------- 卡片容器 ---------- */
div[data-testid="stVerticalBlockBorderWrapper"] {
  background: #FFFFFF;
  border-radius: 14px;
  border-color: var(--line);
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
  transition: box-shadow 0.2s ease, border-color 0.2s ease, transform 0.2s ease;
}
div[data-testid="stVerticalBlockBorderWrapper"]:hover {
  box-shadow: 0 10px 26px rgba(15, 23, 42, 0.09);
  border-color: #BFDBFE;
  transform: translateY(-1px);
}

/* ---------- 指标卡 ---------- */
[data-testid="stMetric"] {
  background: #FFFFFF;
  border: 1px solid var(--line);
  border-radius: 14px;
  padding: 14px 16px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
}
[data-testid="stMetricValue"] {
  font-variant-numeric: tabular-nums;
  color: var(--ink);
}
[data-testid="stMetricLabel"] p { color: var(--muted); font-size: 13px; }

/* ---------- 聊天气泡 ---------- */
[data-testid="stChatMessage"] {
  background: #FFFFFF;
  border: 1px solid var(--line);
  border-radius: 14px;
  padding: 14px 16px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
}
[data-testid="stBottomBlockContainer"] { background: #F6F8FB; }

/* ---------- 徽标 ---------- */
.badge {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
  margin-right: 6px;
  line-height: 18px;
}
.badge-blue  { background: #EFF6FF; color: #1D4ED8; border: 1px solid #BFDBFE; }
.badge-amber { background: #FFFBEB; color: #B45309; border: 1px solid #FDE68A; }
.badge-green { background: #ECFDF5; color: #047857; border: 1px solid #A7F3D0; }
.badge-red   { background: #FEF2F2; color: #B91C1C; border: 1px solid #FECACA; }
.badge-gray  { background: #F1F5F9; color: #475569; border: 1px solid #E2E8F0; }

/* 等宽：工单号、金额、会话 ID */
.mono {
  font-family: "JetBrains Mono", Consolas, "Courier New", monospace;
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.2px;
}

/* 工单信息键值对 */
.kv { display: flex; gap: 8px; align-items: baseline; margin: 3px 0; }
.kv-k { color: #475569; font-size: 13px; min-width: 62px; }
.kv-v { color: #0F172A; font-size: 14px; font-weight: 500; }

/* 主按钮渐变 */
.stButton button[kind="primary"],
.stFormSubmitButton button {
  background: linear-gradient(135deg, #2563EB, #1D4ED8);
  border: none;
  border-radius: 8px;
  transition: filter 0.18s ease, transform 0.18s ease;
}
.stButton button[kind="primary"]:hover { filter: brightness(1.08); transform: translateY(-1px); }

/* 数据表 */
[data-testid="stDataFrame"] { border-radius: 12px; border: 1px solid var(--line); }

/* 隐藏 Streamlit 默认页脚与主菜单 */
#MainMenu, footer { visibility: hidden; }
</style>
        """,
        unsafe_allow_html=True,
    )
