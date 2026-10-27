# 电商售后智能助手

基于 **LangGraph 多智能体** 的电商售后客服系统：监督者路由 + ReAct 子图 + 动态并行，支持 SSE 流式对话、pgvector 知识库 RAG，以及退款「人机协同」审批闭环。后端 FastAPI、会话状态持久化到 PostgreSQL，前端 Streamlit 工作台，Docker Compose 一键部署。

> 本地演示：`http://localhost:8501`（`docker compose up -d --build` 后访问）

## 技术亮点

- **多智能体编排**：`supervisor` 结构化输出做意图路由，`order_agent` 是 `create_agent` 预置 ReAct 子图，多订单查询用 `Send` 按运行时订单数动态并行（map-reduce）
- **流式输出**：`astream(stream_mode="messages")` 逐 token 推送，SSE 事件包含 `token / interrupt / message / error`
- **人机协同**：退款高风险操作经 `interrupt()` 挂起并落审批工单，审批接口 `Command(resume=...)` 从断点恢复，全程审计日志
- **持久化检查点**：`AsyncPostgresSaver` 替代内存版，会话跨重启、跨实例共享
- **RAG 知识库**：pgvector + HNSW 索引，余弦距离 `<=>` 检索售后政策
- **可靠性**：tenacity 指数退避重试、超时控制、`GraphRecursionError` 友好兜底
- **安全**：金额服务端校验、工具输出消毒防间接注入、API Key 鉴权、滑动窗口限流
- **工程化**：结构化日志（thread_id / request_id 贯穿）、25 个单测、GitHub Actions CI、Docker 三服务编排

## 架构

```mermaid
flowchart TB
    subgraph 客户端
        UI[Streamlit 工作台<br/>对话台 / 审批工作台 / 运行状态]
    end
    subgraph 接入层[FastAPI 后端]
        CHAT[POST /chat SSE 流式]
        APPR[POST /approvals 审批接口]
    end
    subgraph 应用层[LangGraph 总图]
        SUP[supervisor 结构化路由]
        SUP --> CH[chat 直答]
        SUP --> KG[knowledge RAG]
        SUP --> OA[order_agent ReAct 子图]
        SUP --> SD[Send 动态并行 xN]
        OA --> INT[interrupt 工单挂起]
    end
    subgraph 数据层[PostgreSQL 16 + pgvector]
        BIZ[(orders / logistics / refunds)]
        TICKET[(approvals / audit_log)]
        VEC[(knowledge 向量)]
        CKPT[(检查点)]
    end
    UI -->|httpx / SSE| CHAT
    UI --> APPR
    CHAT --> SUP
    APPR --> INT
    SUP --> CKPT
    OA --> BIZ
    KG --> VEC
```

## 演示话术（评审 30 秒看完全部亮点）

| 输入 | 触发能力 | 预期 |
| --- | --- | --- |
| `退换货政策是什么` | RAG 知识库检索 | 命中 pgvector 召回售后政策并作答 |
| `查一下订单 B20250002 的物流` | ReAct 子图 + 工具查库 | 返回保温杯订单的运输状态 |
| `帮我查 A20250001 和 C20250003 的物流` | Send 动态并行 | 两分支并发查询后汇总 |
| `订单 A20250001 商品破损，我要退款` | interrupt 人工审批 | 生成工单挂起，等待审批 |
| 审批工作台点「批准退款」 | 断点恢复 | 返回退款确认，审计留痕 |

内置演示数据：`A20250001`（无线降噪耳机 / 329.00）、`B20250002`（不锈钢保温杯 / 89.00）、`C20250003`（机械键盘 / 459.00）。

## 快速开始

### 方式一：Docker 一键启动（推荐）

```bash
# 1. 配置密钥
cp .env.example .env           # 编辑 .env 填入真实 DASHSCOPE_API_KEY

# 2. 一键启动三服务（db / app / ui）
docker compose up -d --build

# 3. 访问
#   前端工作台  http://localhost:8501
#   接口文档    http://localhost:8000/docs
```

### 方式二：本地开发

```bash
docker compose up -d db                        # 只用容器里的数据库
python scripts/init_db.py                      # 建表 + 灌种子数据（幂等）
python run.py                                  # 启动后端（Windows 必须用 run.py）
streamlit run frontend/app.py                  # 另起终端启动前端（可选）
```

> Windows 上后端必须用 `python run.py` 而非 `uvicorn`：`AsyncPostgresSaver` 底层的 psycopg 异步驱动需要 Selector 事件循环。

### 方式三：终端演示（不依赖浏览器）

```bash
python scripts/demo.py        # 交互式对话，输入问题流式输出，退款时可输入 approve/reject 完成审批
```

## 目录结构

```
langgraph_service/
├── app/                    # FastAPI 后端
│   ├── main.py             # 入口：生命周期初始化连接池/检查点/图
│   ├── config.py           # pydantic-settings 环境变量配置
│   ├── api/                # chat（SSE）、approvals（审批）、deps（鉴权限流）
│   ├── graph/              # state/nodes/agent + prompts 外置
│   ├── tools/              # 订单/物流/退款工具（查库 + 消毒 + 金额校验）
│   ├── rag/                # pgvector 检索
│   └── models/             # asyncpg 数据访问 + schema.sql
├── frontend/               # Streamlit 工作台（对话台/审批工作台/运行状态）
├── scripts/                # init_db.py 建表灌种子、demo.py 演示客户端
├── tests/                  # 25 个单测（fake model，不联网）
├── docker-compose.yml      # db + app + ui 三服务编排
└── .github/workflows/      # CI：ruff + pytest + docker build
```

## 核心接口

| 接口 | 说明 |
| --- | --- |
| `GET /health` | 健康检查（无鉴权） |
| `POST /chat` | SSE 流式问答，Header `X-API-Key`；事件 `thread/token/interrupt/message/error/done` |
| `GET /approvals?status=pending` | 工单列表 |
| `POST /approvals/{id}/decision` | 审批决策 `{"decision":"approve|reject","approver":"姓名"}`，返回 `answer` |
| `GET /threads` | 会话列表（检查点统计） |

## 部署

项目通过 Docker Compose 编排三个服务（db / app / ui），可部署到任意支持 Docker 的云主机或 PaaS 平台。

1. 克隆仓库，复制 `.env.example` 为 `.env`，填入真实的 `DASHSCOPE_API_KEY` 与 `API_KEY`。
2. 构建并启动：`docker compose up -d --build`。
3. 对外暴露端口：
   - 前端工作台：`8501`
   - 后端接口：`8000`（`frontend` 通过 compose 服务名 `app` 访问后端）
4. 数据库持久化：`db` 服务使用命名卷 `support_pgdata`，生产环境请改为云数据库或挂载持久卷，避免容器重建导致数据丢失。
5. 公网暴露前：替换默认 `API_KEY`、配置 HTTPS 与域名。

> 若部署平台拆分服务后，服务间域名与 compose 内服务名不一致，请将 `frontend` 的 `API_BASE` 环境变量指向后端服务的实际内部地址。

## 配置项

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql://support:support123@localhost:5432/support` | 业务库/知识库/检查点三合一 |
| `DASHSCOPE_API_KEY` | 空 | 百炼 API Key（必填） |
| `API_KEY` | `dev-api-key-0001` | 接口鉴权 Key |
| `LLM_MODEL` | `qwen-plus` | 对话模型 |
| `EMBEDDING_MODEL` | `text-embedding-v3` | 向量模型 |
| `SERVICE_HOST` / `SERVICE_PORT` | `127.0.0.1` / `8000` | 后端监听地址 |

## 安全说明

- 真实密钥一律通过环境变量 / 平台 secrets 注入，`.env` 已被 `.gitignore` 排除，仓库只保留 `.env.example` 模板
- 工具返回文本统一消毒（过滤控制字符与注入话术）并标注「非指令数据」，防止间接 prompt injection
- 退款金额以数据库订单实付为权威，不信任模型传参
- 接口默认滑动窗口限流 20 次/分钟，公网展示前请评估是否调整阈值或接入网关

## 测试与 CI

```bash
python -m pytest -q          # 25 个单测：路由决策、Send 派发、工单流转、接口冒烟
python -m ruff check app tests frontend
```

CI 流水线（`.github/workflows/ci.yml`）：`ruff` → `pytest` → 构建后端与前端两个镜像。
