# -*- coding: utf-8 -*-
"""
FastAPI 入口：应用生命周期内初始化连接池 + 检查点 + 编译图。

启动（在 langgraph_service 目录下，先 docker compose up -d 并跑过 scripts/init_db.py）：
    ..\\.venv\\Scripts\\python.exe run.py
    （用 run.py 而非 python -m uvicorn：Windows 需先切 SelectorEventLoop，见 run.py 注释）
接口文档（自动生成）：
    http://localhost:8000/docs
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.api import approvals, chat
from app.config import settings
from app.graph.agent import build_app
from app.log import setup_logging
from app.models import db as database

setup_logging()
logger = logging.getLogger("support")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化资源，关闭时释放"""
    # 1. 业务库连接池（asyncpg）
    await database.init_pool(settings.database_url)
    logger.info("数据库连接池就绪")

    # 2. 检查点：AsyncPostgresSaver（替代 MVP 的 MemorySaver）
    #    会话状态持久化到 PG -> 跨重启保留、多实例共享。
    #    注意 from_conn_string 返回的是上下文管理器，须 async with ... as saver 取到实例
    async with AsyncPostgresSaver.from_conn_string(settings.database_url) as saver:
        await saver.setup()  # 首次运行自动建检查点表（幂等）
        # 3. 编译图并注入检查点（图在 saver 的上下文内使用）
        app.state.graph = build_app(saver)
        logger.info("LangGraph 总图编译完成，服务就绪")
        yield

    await database.close_pool()
    logger.info("服务关闭，资源已释放")


app = FastAPI(title="电商售后智能助手", version="1.0.0", lifespan=lifespan)
app.include_router(chat.router)
app.include_router(approvals.router)


@app.get("/health")
async def health():
    """健康检查（无认证，供负载均衡/容器探活使用）"""
    try:
        await database.get_pool().fetchval("SELECT 1")
        db_status = "ok"
    except Exception:
        db_status = "down"
    return {"status": "ok" if db_status == "ok" else "degraded", "db": db_status}
