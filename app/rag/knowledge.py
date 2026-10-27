# -*- coding: utf-8 -*-
"""
RAG 分支：售后政策类问题的知识库检索 + 生成。

链路（与 pgvector_demo.py 演示的完全一致，只是数据换成了真实库）：
    用户问题 -> embedding（百炼 text-embedding-v3）
             -> pgvector 余弦检索 top-k（knowledge 表 + HNSW 索引）
             -> 召回内容拼进系统提示词
             -> 模型生成答案（只依据资料，不编造）
"""
import logging

from langchain_core.messages import AIMessage, SystemMessage
from openai import AsyncOpenAI

from app.config import settings
from app.graph.prompts import PROMPTS
from app.llm import llm
from app.models import repo
from app.resilience import llm_retry

logger = logging.getLogger("support.rag")

# Embedding 走原生 SDK：OpenAIEmbeddings 封装带的附加参数会被百炼侧拒绝（P0 已验证）
_embedding_client = AsyncOpenAI(
    api_key=settings.dashscope_api_key,
    base_url=settings.llm_base_url,
)


async def embed_text(text: str) -> list[float]:
    """文本 -> 1024 维向量"""
    resp = await _embedding_client.embeddings.create(
        model=settings.embedding_model, input=[text]
    )
    return resp.data[0].embedding


async def knowledge_node(state) -> dict:
    """图节点：检索知识库后作答。节点名 knowledge 对应 chat.py 里可流式输出的白名单"""
    question = state["messages"][-1].content
    vec = await embed_text(question)
    hits = await repo.search_knowledge(vec, top_k=settings.rag_top_k)
    logger.info(
        "RAG 检索 question=%r 命中=%d ids=%s",
        question[:30], len(hits), [h["id"] for h in hits],
    )

    if not hits:
        return {"messages": [AIMessage(
            content="抱歉，知识库中暂未查询到相关规定，建议联系人工客服进一步确认。"
        )]}

    context = "\n".join(f"- {h['content']}" for h in hits)
    system = SystemMessage(content=PROMPTS["knowledge"].replace("{context}", context))
    resp = await _answer([system] + state["messages"])
    return {"messages": [resp]}


@llm_retry
async def _answer(messages):
    """带重试的政策问答调用"""
    return await llm.ainvoke(messages)
