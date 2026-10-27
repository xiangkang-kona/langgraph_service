# -*- coding: utf-8 -*-
"""
接口依赖：API Key 认证 + 简单限流（P0 安全基线）。

认证：Header X-API-Key 与配置比对，失败 401（复用 fastapi_ai_demo.py 已验证的
      FastAPI 依赖注入套路）。生产升级路径：换 OAuth2/JWT + 用户体系。
限流：滑动窗口计数，默认 20 次/分钟/密钥，超限 429。
"""
import time

from fastapi import Header, HTTPException

from app.config import settings


class SlidingWindowLimiter:
    """滑动窗口限流器（单进程内存实现，够演示；分布式部署换 Redis）"""

    def __init__(self, max_requests: int = 20, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window = window_seconds
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        hits = [t for t in self._hits.get(key, []) if now - t < self.window]
        if len(hits) >= self.max_requests:
            self._hits[key] = hits
            return False
        hits.append(now)
        self._hits[key] = hits
        return True


limiter = SlidingWindowLimiter()


async def verify_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> str:
    """FastAPI 依赖：校验 API Key + 限流。路由声明 dependencies=[Depends(verify_api_key)] 即生效。

    注意 Header 默认值必须是 None 而不是必填：必填时缺 Header 会被框架先判 422，
    鉴权失败应当统一返回 401。
    """
    if not x_api_key or x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="无效的 API Key")
    if not limiter.allow(x_api_key):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
    return x_api_key
