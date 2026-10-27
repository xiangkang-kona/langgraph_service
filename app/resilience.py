# -*- coding: utf-8 -*-
"""
LLM 调用可靠性：tenacity 指数退避重试（P0 生产化改造第 3 项）。

双层重试策略：
    第 1 层 ChatOpenAI 自带的 max_retries（OpenAI SDK 层，秒级快速重试网络抖动）
    第 2 层 本文件的 llm_retry（应用层，指数退避 2s/4s，应对偶发 5xx 和超时）
只重试"值得重试"的异常：连接错误、超时、服务端 5xx；4xx（如鉴权失败）不重试。
"""
import logging

from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("support.resilience")


def _retryable(exc: BaseException) -> bool:
    """判断异常是否值得重试"""
    import openai

    if isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError)):
        return True
    if isinstance(exc, openai.APIStatusError) and exc.status_code >= 500:
        return True
    return False


# 用法：@llm_retry 装饰需要重试的异步函数
llm_retry = retry(
    retry=_retryable,
    wait=wait_exponential(multiplier=1, min=2, max=30),  # 2s -> 4s -> 8s
    stop=stop_after_attempt(3),
    before_sleep=lambda state: logger.warning(
        "LLM 调用失败（%s），%.0f 秒后第 %s 次重试",
        state.outcome, (state.next_action.sleep if state.next_action else 0),
        state.attempt_number,
    ),
    reraise=True,
)
