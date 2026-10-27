# -*- coding: utf-8 -*-
"""
服务启动入口。

背景：psycopg 异步驱动（AsyncPostgresSaver 底层）在 Windows 上拒绝
ProactorEventLoop；而 uvicorn 的 loop=auto 会自行把策略重设为 Proactor，
覆盖掉我们前置的设置。因此这里手动创建 Selector 事件循环，并用
loop="none" 跳过 uvicorn 的事件循环配置，全程由我们掌控。

启动：
    ..\\.venv\\Scripts\\python.exe run.py
"""
import asyncio
import os
import sys

from uvicorn import Config, Server

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# 监听地址走环境变量：本机开发默认 127.0.0.1，容器内需 0.0.0.0（compose 已配置）
HOST = os.getenv("SERVICE_HOST", "127.0.0.1")
PORT = int(os.getenv("SERVICE_PORT", "8000"))

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

config = Config("app.main:app", host=HOST, port=PORT, loop="none")
server = Server(config)

if __name__ == "__main__":
    loop.run_until_complete(server.serve())
