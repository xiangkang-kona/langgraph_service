# -*- coding: utf-8 -*-
"""
配置中心：所有环境敏感项集中于此（P0 生产化改造第 1 项：配置与代码分离）。

读取优先级：进程环境变量 > .env 文件 > 代码默认值。
生产部署时只改环境变量/启动参数，代码零改动。
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ---- 数据库（docker-compose 里的 db 服务，业务库/知识库/检查点三合一） ----
    database_url: str = "postgresql://support:support123@localhost:5432/support"

    # ---- LLM（百炼 OpenAI 兼容模式，沿用 langgraph_mvp 的接入方式） ----
    dashscope_api_key: str = ""
    llm_model: str = "qwen-plus"
    embedding_model: str = "text-embedding-v3"
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    # ---- 接口认证（演示用 Header API Key；生产换 OAuth/JWT） ----
    api_key: str = "dev-api-key-0001"

    # ---- 可靠性 ----
    llm_timeout: float = 60.0     # 单次 LLM 请求超时（秒）
    llm_max_retries: int = 3      # OpenAI SDK 层自动重试次数（网络抖动秒级重试）
    recursion_limit: int = 30     # 图循环上限（防模型无限调工具刷爆 token）

    # ---- RAG ----
    rag_top_k: int = 3            # 知识库检索召回条数

    # ---- 长会话上下文 ----
    context_max_messages: int = 24   # 超过该条数触发摘要压缩
    context_keep_recent: int = 8     # 压缩时保留最近 N 条原文


settings = Settings()
