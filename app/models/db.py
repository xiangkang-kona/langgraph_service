# -*- coding: utf-8 -*-
"""
数据库连接池管理：应用生命周期绑定（FastAPI lifespan 里初始化/关闭）。

业务查询走 asyncpg（P0 供 /threads 查检查点、后续供工具查业务表）；
图检查点由 AsyncPostgresSaver 自己管理连接（内部用 psycopg，互不干扰）。
"""
import asyncpg

_pool: asyncpg.Pool | None = None


async def init_pool(dsn: str) -> None:
    """创建连接池（应用启动时调用）"""
    global _pool
    _pool = await asyncpg.create_pool(dsn, min_size=2, max_size=10)


async def close_pool() -> None:
    """关闭连接池（应用关闭时调用）"""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    """获取连接池（未初始化说明调用时序错误，直接暴露问题而不是兜底）"""
    if _pool is None:
        raise RuntimeError("数据库连接池未初始化：应在应用 lifespan 中完成初始化")
    return _pool
