# -*- coding: utf-8 -*-
"""
后端接口客户端：前端只通过 HTTP 调用 langgraph_service，不 import 后端 app 包。

三个设计要点：
    1. 每次调用现开 httpx.Client，用完即关。Streamlit 每次交互都重跑整个脚本，
       把长连接挂在 session 里会持续泄漏，且改 API 地址后旧连接仍在。
    2. 流式接口返回"文本生成器"：token/message 逐段 yield 交给 st.write_stream；
       thread/interrupt/error/done 这类结构化事件通过 on_event 回调交给调用方
       —— 生成器不能 return 值，且内部不能调用 st.* 渲染，只能回调上报。
    3. 所有 HTTP 异常统一转 APIError，message 直接可展示给用户，
       页面层只需 try/except 显示，不必解析状态码。
"""
import json
import logging
from collections.abc import Callable, Iterator

import httpx

logger = logging.getLogger("support.frontend")

# 流式请求：LLM 首包可能慢，read 给 180 秒；连接阶段必须快速失败
STREAM_TIMEOUT = httpx.Timeout(connect=5.0, read=180.0, write=10.0, pool=5.0)
# 普通查询接口
QUICK_TIMEOUT = httpx.Timeout(10.0)
# 审批接口要等图恢复后重跑 LLM，给足时间
DECIDE_TIMEOUT = httpx.Timeout(connect=5.0, read=180.0, write=10.0, pool=5.0)

# 状态码 -> 中文提示（后端拒绝场景已在设计上固定，不猜测未知语义）
STATUS_MESSAGE = {
    401: "API Key 无效，请在侧边栏检查配置",
    404: "记录不存在，可能已被处理",
    409: "工单已被处理，请刷新后查看最新状态",
    422: "请求参数不合法",
    429: "请求过于频繁（限流 20 次/分钟），请稍后再试",
}


class APIError(Exception):
    """接口异常：message 可直接展示，status_code 供页面做分支判断"""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class SupportAPI:
    """后端接口封装：一个实例对应一组 (地址, Key)，用完即丢"""

    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._headers = {"X-API-Key": api_key}

    # ==================== 内部工具 ====================

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        """把 HTTP 错误转成可直接展示的中文提示。

        流式响应默认没读 body，这里先 read() 再解析 FastAPI 的 detail 字段。
        """
        if resp.status_code < 400:
            return
        detail = ""
        try:
            payload = json.loads(resp.read().decode("utf-8"))
            raw = payload.get("detail") or ""
            detail = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
        except (ValueError, UnicodeDecodeError):
            detail = ""
        message = STATUS_MESSAGE.get(resp.status_code) or detail or f"请求失败（HTTP {resp.status_code}）"
        raise APIError(message, resp.status_code)

    def _check_config(self) -> None:
        """配置校验：地址/Key 为空时给出可操作提示，而不是笼统的"连接中断"。

        场景：用户把侧边栏输入框清空后，空 base_url 会让 httpx 抛
        UnsupportedProtocol，被下面的 HTTPError 分支吞成"连接中断"，误导排查。
        """
        if not self.base_url:
            raise APIError("请先在左侧填写 API 地址，例如 http://app:8000")
        if not self.base_url.startswith(("http://", "https://")):
            raise APIError("API 地址需以 http:// 或 https:// 开头")
        if not self._headers["X-API-Key"]:
            raise APIError("请先在左侧填写 API Key")

    def _request(self, method: str, path: str, timeout: httpx.Timeout, **kwargs) -> dict:
        """普通请求的公共封装：统一连接异常与状态码处理"""
        self._check_config()
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.request(method, self._url(path), headers=self._headers, **kwargs)
        except httpx.TimeoutException as exc:
            raise APIError("请求超时，请稍后重试") from exc
        except httpx.HTTPError as exc:
            raise APIError(f"无法连接后端服务（{self.base_url}），请检查接口地址") from exc
        self._raise_for_status(resp)
        return resp.json()

    # ==================== 接口方法 ====================

    def health(self) -> dict:
        """健康检查（该接口无需认证）"""
        return self._request("GET", "/health", QUICK_TIMEOUT)

    def list_tickets(self, status: str = "pending") -> dict:
        """审批工单列表：pending / approved / rejected"""
        return self._request("GET", "/approvals", QUICK_TIMEOUT, params={"status": status})

    def decide(self, ticket_id: int, decision: str, approver: str) -> dict:
        """审批决策：批准或拒绝，返回 {ticket_id, status, approver, answer}"""
        return self._request(
            "POST",
            f"/approvals/{ticket_id}/decision",
            DECIDE_TIMEOUT,
            json={"decision": decision, "approver": approver},
        )

    def list_threads(self) -> dict:
        """会话列表（检查点表统计）"""
        return self._request("GET", "/threads", QUICK_TIMEOUT)

    def stream_chat(
        self,
        question: str,
        thread_id: str | None,
        on_event: Callable[[str, dict], None],
    ) -> Iterator[str]:
        """流式问答：yield 文本片段；结构化事件走 on_event 回调。

        事件约定（与 app/api/chat.py 的 SSE 协议一一对应）：
            thread    首次请求时后端生成的会话标识
            interrupt 图挂起等人审，payload 含 ticket_id
            done      本轮结束，额外附带本轮经过的节点集合 nodes
            error     后端友好错误信息
        """
        self._check_config()
        body: dict = {"question": question}
        if thread_id:
            body["thread_id"] = thread_id

        nodes: set[str] = set()
        try:
            with httpx.Client(timeout=STREAM_TIMEOUT) as client:
                with client.stream(
                    "POST", self._url("/chat"), headers=self._headers, json=body
                ) as resp:
                    self._raise_for_status(resp)
                    event = None
                    for line in resp.iter_lines():
                        if line.startswith("event: "):
                            event = line[len("event: "):].strip()
                            continue
                        if not line.startswith("data: "):
                            continue
                        try:
                            data = json.loads(line[len("data: "):])
                        except ValueError:
                            logger.warning("SSE 数据行不是合法 JSON: %r", line[:120])
                            continue

                        if event == "token":
                            # token 事件的 node 字段即"哪个分支在说话"，用于界面徽标
                            if data.get("node"):
                                nodes.add(data["node"])
                            if data.get("content"):
                                yield data["content"]
                        elif event == "message":
                            # 非流式答复（如 Send 并行后的汇总节点），整段补发
                            if data.get("content"):
                                yield data["content"]
                        elif event == "done":
                            on_event("done", {"nodes": sorted(nodes)})
                        else:
                            on_event(event, data)
        except httpx.TimeoutException as exc:
            raise APIError("等待响应超时，请稍后重试") from exc
        except httpx.HTTPError as exc:
            raise APIError("与后端服务的连接中断，请重试") from exc
