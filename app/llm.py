# -*- coding: utf-8 -*-
"""
模型接入层（从 langgraph_mvp/llm.py 移植，增加超时与 SDK 层重试配置）。

对比 MVP 版的变化：
    1. 模型名/密钥来自配置中心（环境变量），不再硬编码
    2. 显式配置 timeout + max_retries（MVP 版依赖默认值，网络抖动直接抛错）
"""
from langchain_openai import ChatOpenAI

from app.config import settings

llm = ChatOpenAI(
    model=settings.llm_model,
    api_key=settings.dashscope_api_key,
    base_url=settings.llm_base_url,
    timeout=settings.llm_timeout,
    max_retries=settings.llm_max_retries,
)
