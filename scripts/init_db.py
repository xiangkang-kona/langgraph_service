# -*- coding: utf-8 -*-
"""
数据库初始化脚本：建表 + 灌种子数据（可重复执行，幂等）。

运行前提：docker compose up -d（数据库已起）
运行方式（在 langgraph_service 目录下）：
    ..\\.venv\\Scripts\\python.exe scripts\\init_db.py

种子数据内容：
    1. 订单/物流：与 langgraph_mvp 的 mock 数据一致（P1 工具切到查库后无缝衔接）
    2. 知识库：售后政策文档，入库时调 embedding 模型算好向量（P2 RAG 分支的数据源）
"""
import asyncio
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
# 让脚本能导入 app 包（从 langgraph_service 目录运行时其实不需要，兼容其他 cwd）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncpg
from openai import AsyncOpenAI

from app.config import settings

SCHEMA_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "app", "models", "schema.sql"
)

# 订单/物流种子数据（与 langgraph_mvp 的 mock 保持一致）
SEED_ORDERS = [
    ("A20250001", "无线降噪耳机", 329.00, "已签收", "2026-10-15"),
    ("B20250002", "不锈钢保温杯", 89.00, "运输中", "2026-10-17"),
    ("C20250003", "机械键盘", 459.00, "待发货", "2026-10-19"),
]
SEED_LOGISTICS = [
    ("A20250001", "已签收（10-19 送达，本人签收）"),
    ("B20250002", "运输中（当前位于武汉转运中心，预计 10-22 送达）"),
    ("C20250003", "待发货（仓库拣货中）"),
]

# 知识库种子文档（P2 RAG 分支的语料）
SEED_KNOWLEDGE = [
    ("退换货政策：支持 7 天无理由退货，商品需保持完好、吊牌未拆、包装齐全；"
     "15 天内因质量问题免费换新。", "policy"),
    ("运费规则：无理由退货由买家先行垫付运费，审核通过后按商品页标注规则退还；"
     "质量问题换新的往返运费由商家承担。", "policy"),
    ("保修服务：电子产品自签收之日起享受 12 个月官方保修，"
     "人为损坏、进水、私自拆机不在保修范围内。", "policy"),
    ("发票支持：订单完成后可在账户中心申请开具电子增值税普通发票，"
     "开具后 24 小时内发送至预留邮箱。", "policy"),
    ("会员权益：老客户享售后优先响应（2 小时内必回复）、不定期优惠券赠送、"
     "部分商品老客专享价。", "policy"),
    ("配送时效：普通快递 3-5 个工作日送达，偏远地区顺延 2 天；"
     "大促期间发货可能有 48 小时以内的延迟。", "logistics"),
]


async def main() -> None:
    print(f"连接数据库：{settings.database_url}")
    conn = await asyncpg.connect(settings.database_url)
    try:
        # 1. 建表（schema.sql 按分号拆分逐句执行；语句幂等，可重复运行）
        with open(SCHEMA_PATH, encoding="utf-8") as f:
            schema_sql = f.read()
        # 先剔除整行注释（否则按分号拆出的语句块可能以注释开头，被误判跳过）
        schema_sql = "\n".join(
            line for line in schema_sql.splitlines() if not line.strip().startswith("--")
        )
        for stmt in schema_sql.split(";"):
            stmt = stmt.strip()
            if stmt:
                await conn.execute(stmt)
        print("建表完成（含 pgvector 扩展与 HNSW 索引）")

        # 2. 灌订单/物流种子（ON CONFLICT 幂等）
        for row in SEED_ORDERS:
            await conn.execute(
                "INSERT INTO orders (order_id, product, amount, status, created_at) "
                "VALUES ($1, $2, $3, $4, $5) ON CONFLICT (order_id) DO NOTHING",
                *row,
            )
        for row in SEED_LOGISTICS:
            await conn.execute(
                "INSERT INTO logistics (order_id, status) VALUES ($1, $2) "
                "ON CONFLICT (order_id) DO NOTHING",
                *row,
            )
        print(f"订单/物流种子完成（{len(SEED_ORDERS)} 条订单）")

        # 3. 灌知识库（调 embedding 模型算向量；表空才灌，幂等）
        existing = await conn.fetchval("SELECT COUNT(*) FROM knowledge")
        if existing == 0:
            # 直接用 OpenAI SDK 调百炼兼容接口（OpenAIEmbeddings 封装带的
            # 附加参数会被百炼侧拒绝，最小可用路径是原生调用）
            client = AsyncOpenAI(
                api_key=settings.dashscope_api_key,
                base_url=settings.llm_base_url,
            )
            texts = [content for content, _ in SEED_KNOWLEDGE]
            resp = await client.embeddings.create(
                model=settings.embedding_model, input=texts
            )
            vectors = [item.embedding for item in resp.data]
            for (content, source), vec in zip(SEED_KNOWLEDGE, vectors):
                vec_literal = "[" + ",".join(f"{v:.6f}" for v in vec) + "]"
                await conn.execute(
                    "INSERT INTO knowledge (content, source, embedding) "
                    "VALUES ($1, $2, $3::vector)",
                    content, source, vec_literal,
                )
            print(f"知识库种子完成（{len(SEED_KNOWLEDGE)} 条，embedding 已入库）")
        else:
            print(f"知识库已有 {existing} 条，跳过种子")

        print("数据库初始化完成。")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
