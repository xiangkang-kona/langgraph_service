# -*- coding: utf-8 -*-
"""
结构化日志：统一格式 + thread_id / request_id 上下文字段（P0 生产化改造第 2 项）。

对比 langgraph_mvp 的 print：
    1. 每条日志自带"哪个请求、哪个会话"，多用户并发时能串起完整链路
    2. 统一级别/时间戳格式，可被日志采集器（ELK/Loki）直接消费
    3. 第三方库降噪，避免 httpx/uvicorn 刷屏淹没关键信息
"""
import contextvars
import logging
import sys
import warnings

sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 GBK

# 结构化输出消息进检查点时 pydantic 会发一条无害的序列化告警（known issue），屏蔽
warnings.filterwarnings("ignore", message="Pydantic serializer warnings")

# 请求级上下文：API 层赋值，所有日志自动携带（contextvars 对 async 任务安全）
thread_id_var = contextvars.ContextVar("thread_id", default="-")
request_id_var = contextvars.ContextVar("request_id", default="-")


class ContextFormatter(logging.Formatter):
    """把上下文变量注入每条日志记录"""

    def format(self, record: logging.LogRecord) -> str:
        record.thread_id = thread_id_var.get()
        record.request_id = request_id_var.get()
        return super().format(record)


def setup_logging() -> None:
    """初始化根日志器，应用启动时调用一次"""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(ContextFormatter(
        "%(asctime)s | %(levelname)-7s | req=%(request_id)s thread=%(thread_id)s "
        "| %(name)s | %(message)s"
    ))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    # 第三方库降噪
    for name in ("httpx", "openai", "asyncpg", "uvicorn.access", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


logger = logging.getLogger("support")
