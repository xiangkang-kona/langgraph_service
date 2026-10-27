# -*- coding: utf-8 -*-
"""
演示客户端：在终端里跑通流式问答与退款审批闭环，不依赖浏览器。

用途：
    1. 简历/评审演示：一条命令启动，对着终端展示 SSE 流式、RAG、工单审批
    2. 本地自检：验证后端五条链路是否正常

运行（先确保后端已起，见 README 的快速开始）：
    ..\\.venv\\Scripts\\python.exe scripts\\demo.py

交互：
    直接输入问题回车，助手逐字流式输出；
    若触发退款，会打印工单号并停下，输入 approve / reject 完成人工审批；
    输入 quit 退出。
"""
import json
import os
import sys

import httpx

sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = os.getenv("API_BASE", "http://127.0.0.1:8000")
API_KEY = os.getenv("API_KEY", "dev-api-key-0001")
HEADERS = {"X-API-Key": API_KEY}
TIMEOUT = httpx.Timeout(180.0, read=180.0)


def sse_chat(question: str, thread_id: str | None) -> dict:
    """流式问答：逐 token 打印，返回会话标识与可能的审批工单号"""
    body = {"question": question}
    if thread_id:
        body["thread_id"] = thread_id

    new_thread, ticket_id = thread_id, None
    event = None
    with httpx.Client(timeout=TIMEOUT) as client:
        with client.stream(
            "POST", f"{BASE_URL}/chat", headers=HEADERS, json=body
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if line.startswith("event: "):
                    event = line[7:]
                    continue
                if not line.startswith("data: "):
                    continue
                data = json.loads(line[6:])
                if event == "thread":
                    new_thread = data["thread_id"]
                elif event == "token":
                    sys.stdout.write(data["content"])
                    sys.stdout.flush()
                elif event == "message":
                    sys.stdout.write(data["content"])
                    sys.stdout.flush()
                elif event == "interrupt":
                    ticket_id = data["payload"].get("ticket_id")
                    print(f"\n[已生成审批工单 #{ticket_id}] {data['payload']}")
                elif event == "error":
                    print(f"\n[错误] {data['message']}")
    print()
    return {"thread_id": new_thread, "ticket_id": ticket_id}


def decide(ticket_id: int, decision: str) -> str:
    """审批决策：返回图恢复后给用户的最终答复"""
    resp = httpx.post(
        f"{BASE_URL}/approvals/{ticket_id}/decision",
        headers=HEADERS,
        json={"decision": decision, "approver": "演示审批人"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["answer"]


def main() -> int:
    print("=" * 60)
    print("电商售后智能助手 · 终端演示")
    print(f"后端 {BASE_URL} | 输入 quit 退出")
    print("可试：退换货政策 / 查订单 B20250002 / 订单 A20250001 退款")
    print("=" * 60)

    thread_id = None
    while True:
        try:
            question = input("\n你: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question:
            continue
        if question.lower() == "quit":
            break

        try:
            result = sse_chat(question, thread_id)
        except httpx.HTTPError as exc:
            print(f"[连接失败] {exc}")
            continue

        thread_id = result["thread_id"]
        ticket_id = result["ticket_id"]
        if ticket_id:
            while True:
                decision = input(
                    f"审批工单 #{ticket_id}，输入 approve / reject: "
                ).strip().lower()
                if decision in ("approve", "reject"):
                    break
            try:
                answer = decide(ticket_id, decision)
            except httpx.HTTPError as exc:
                print(f"[审批失败] {exc}")
                continue
            print(f"\n助手（审批后）: {answer}")

    print("\n演示结束。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
