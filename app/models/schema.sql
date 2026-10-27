-- -*- sql -*-
-- 数据库结构：业务表 + 审批工单 + 审计 + 知识库（pgvector）
-- P0 阶段实际使用的只有知识库和（由 AsyncPostgresSaver 自动创建的）检查点表；
-- 业务表/工单表/审计表为 P1 任务提前建好，init_db.py 同步灌入种子数据。

CREATE EXTENSION IF NOT EXISTS vector;

-- 订单表（P1 起工具从此表查询，替代进程内 mock）
CREATE TABLE IF NOT EXISTS orders (
    order_id    TEXT PRIMARY KEY,
    product     TEXT NOT NULL,
    amount      NUMERIC(10, 2) NOT NULL,
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

-- 物流表
CREATE TABLE IF NOT EXISTS logistics (
    order_id    TEXT PRIMARY KEY,
    status      TEXT NOT NULL
);

-- 退款单（审批通过后落库）
CREATE TABLE IF NOT EXISTS refunds (
    id          SERIAL PRIMARY KEY,
    order_id    TEXT NOT NULL,
    amount      NUMERIC(10, 2) NOT NULL,
    reason      TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- 审批工单（P1 人审工单化的核心表：interrupt 挂起时写入，审批接口更新）
CREATE TABLE IF NOT EXISTS approvals (
    id          SERIAL PRIMARY KEY,
    thread_id   TEXT NOT NULL,
    order_id    TEXT NOT NULL,
    amount      NUMERIC(10, 2) NOT NULL,
    reason      TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',   -- pending / approved / rejected
    payload     JSONB NOT NULL,                    -- 完整审批材料
    approver    TEXT,
    created_at  TIMESTAMPTZ DEFAULT now(),
    decided_at  TIMESTAMPTZ
);

-- 同一会话同一订单只允许一张待审工单：interrupt 恢复时工具会重跑，
-- 靠这个部分唯一索引做幂等（见 app/models/repo.py 的 ON CONFLICT 用法）
CREATE UNIQUE INDEX IF NOT EXISTS uq_approvals_pending
    ON approvals (thread_id, order_id) WHERE status = 'pending';

-- 审计日志（谁、何时、做了什么）
CREATE TABLE IF NOT EXISTS audit_log (
    id          SERIAL PRIMARY KEY,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    detail      JSONB,
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- 知识库（P2 RAG 分支使用：入库时算好 embedding，检索用余弦距离 <=>）
CREATE TABLE IF NOT EXISTS knowledge (
    id          SERIAL PRIMARY KEY,
    content     TEXT NOT NULL,
    source      TEXT NOT NULL,
    embedding   vector(1024)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_embedding
    ON knowledge USING hnsw (embedding vector_cosine_ops);
