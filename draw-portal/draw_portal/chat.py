"""小程序文字聊天（/api/chat）：微信内容安全审核 + 转发本机 MCP /internal/chat。

- 用户输入与模型回复都过 msgSecCheck（version=2 / scene=2 评论场景）；
  审核服务不可用时 fail-closed 503——内容安全是合规闸，不是可用性配件。
  输出被拦时不报错，回落固定安全话术（用户看到的是小派接不住，不是「你违规」）。
- access_token 走 client_credential 现场取、模块级缓存（7200s 窗口提前 300s 续）；
  凭据复用 DRAW_PORTAL_WX_APPID/_WX_SECRET（wx-login 同款），不新增第三套密钥。
- MCP 侧错误映射与 generate.py 同口径：400 输入白话透传，其余固定文案不回显内部细节。
"""
from __future__ import annotations

import json
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# access_token 模块级缓存：同一进程内所有聊天请求共用一张票
_token_lock = threading.Lock()
_token_cache: dict[str, object] = {"token": "", "expires_at": 0.0}


class ChatError(Exception):
    """聊天链路失败；status 直接映射为 /api/chat 回码（与 GenerateError 同形）。"""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


class ChatContentRejected(Exception):
    """内容安全审核未通过（输入或输出）；detail 可直接给用户看。"""


def _wx_api_json(url: str, *, body: bytes | None, timeout: int) -> dict:
    try:
        req = Request(url, data=body, method="POST" if body is not None else "GET")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, OSError, json.JSONDecodeError) as exc:
        raise ChatError(503, "内容安全服务暂不可用") from exc
    if not isinstance(data, dict):
        raise ChatError(503, "内容安全服务暂不可用")
    return data


def get_wx_access_token(appid: str, secret: str, *, timeout: int = 5) -> str:
    """client_credential 取 access_token；缓存窗口 7200s 提前 300s 续。"""
    now = time.time()
    with _token_lock:
        cached = str(_token_cache.get("token") or "")
        if cached and float(_token_cache.get("expires_at") or 0) > now:
            return cached
    url = (
        "https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential"
        f"&appid={appid}&secret={secret}"
    )
    data = _wx_api_json(url, body=None, timeout=timeout)
    token = data.get("access_token")
    if not isinstance(token, str) or not token:
        raise ChatError(503, "内容安全服务暂不可用")
    expires_in = int(data.get("expires_in") or 7200)
    with _token_lock:
        _token_cache["token"] = token
        _token_cache["expires_at"] = now + max(60, expires_in - 300)
    return token


def invalidate_wx_access_token() -> None:
    with _token_lock:
        _token_cache["token"] = ""
        _token_cache["expires_at"] = 0.0


def msg_sec_check(*, appid: str, secret: str, openid: str, content: str) -> None:
    """msgSecCheck v2（scene=2 评论场景）。pass 返回；review/risky 抛 ChatContentRejected；
    审核服务本身不可用抛 ChatError(503)。token 过期（40001/42001）作废缓存重试一次。"""
    for attempt in range(2):
        token = get_wx_access_token(appid, secret)
        payload = json.dumps(
            {"openid": openid, "scene": 2, "version": 2, "content": content[:2500]},
            ensure_ascii=False,
        ).encode("utf-8")
        data = _wx_api_json(
            f"https://api.weixin.qq.com/wxa/msg_sec_check?access_token={token}",
            body=payload,
            timeout=8,
        )
        errcode = int(data.get("errcode") or 0)
        if errcode in (40001, 42001) and attempt == 0:
            invalidate_wx_access_token()
            continue
        if errcode != 0:
            raise ChatError(503, "内容安全服务暂不可用")
        result = data.get("result")
        suggest = result.get("suggest") if isinstance(result, dict) else None
        if suggest == "pass":
            return
        if suggest in ("review", "risky"):
            raise ChatContentRejected("内容未通过安全审核，换个话题试试")
        raise ChatError(503, "内容安全服务暂不可用")


def request_internal_chat(
    *, base_url: str, secret: str, message: str, history: list[dict[str, str]], timeout_seconds: int
) -> dict:
    """调 MCP /internal/chat。400 输入白话透传；其余（含网络）塌成固定文案。"""
    body = json.dumps({"message": message, "history": history}, ensure_ascii=False).encode("utf-8")
    req = Request(
        f"{base_url}/internal/chat",
        data=body,
        headers={"Content-Type": "application/json", "X-Hutuji-Usage-Secret": secret},
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout_seconds) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        detail = ""
        try:
            raw = json.loads(exc.read().decode("utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("detail"), str):
                detail = raw["detail"]
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            detail = ""
        if exc.code == 400 and detail:
            raise ChatError(400, detail) from None
        raise ChatError(502, "聊天服务暂不可用，请稍后再试") from None
    except (URLError, OSError, json.JSONDecodeError) as exc:
        raise ChatError(502, "聊天服务暂不可用，请稍后再试") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("reply"), str) or not payload["reply"].strip():
        raise ChatError(502, "聊天服务暂不可用，请稍后再试")
    intent = payload.get("draw_intent")
    if intent is not None:
        if not isinstance(intent, dict) or not isinstance(intent.get("description"), str) or not intent["description"].strip():
            intent = None  # 畸形意图按无意图处理，不透给前端
    return {"reply": payload["reply"], "draw_intent": intent}
