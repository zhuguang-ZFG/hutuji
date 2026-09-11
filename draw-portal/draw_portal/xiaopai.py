"""小派设置（音色/模型切换）：转发本机 MCP /internal/xiaozhi/*（2026-09-12）。

portal 不持控制台凭据；控制台 JWT 只在 MCP 侧 env（与激活中转共用，token-auto
续期后 restart 即新值）。归属语义（protocol §10.4.19）：用户切换只写 portal 代管
的克隆 agent（首次个性化时 MCP 从基础 agent 整包克隆），设备经 6 位验证码绑到
克隆 agent 后生效；未个性化设备继续走共享 agent，互不影响。

错误映射：MCP 400 → 400（文案透传）；其余一律塌成 502，不回显内部细节。
INVALID_VERIFICATION_CODE 单独识别成 400（用户输错码是常态，不是内部故障）。
"""
from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_OPTIONS_TIMEOUT = 15
_WRITE_TIMEOUT = 25


class XiaopaiError(Exception):
    """小派设置转发失败；status 直接映射为 portal 端点回码。"""

    def __init__(self, message: str, *, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


def _call_json(
    *, method: str, url: str, secret: str, body: dict | None, timeout: float
) -> dict:
    req = Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8")
        if body is not None
        else None,
        headers={"Content-Type": "application/json", "X-Hutuji-Usage-Secret": secret},
        method=method,
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail", "")
        except Exception:
            detail = ""
        if "INVALID_VERIFICATION_CODE" in detail:
            raise XiaopaiError("验证码无效，请重新输入", status=400) from None
        if exc.code == 400:
            raise XiaopaiError(detail or "请求不合法", status=400) from None
        raise XiaopaiError(detail or "设置服务暂不可用", status=502) from None
    except (URLError, TimeoutError, OSError) as exc:
        raise XiaopaiError("设置服务连接失败", status=502) from exc
    except json.JSONDecodeError as exc:
        raise XiaopaiError("设置服务响应异常", status=502) from exc
    if not isinstance(payload, dict):
        raise XiaopaiError("设置服务响应异常", status=502)
    return payload


def request_options(*, base_url: str, secret: str) -> dict:
    """音色/模型清单。未配置通道（MCP 404）塌成 503。"""
    if not secret:
        raise XiaopaiError("设置通道未配置", status=503)
    payload = _call_json(
        method="GET",
        url=f"{base_url}/internal/xiaozhi/options",
        secret=secret,
        body=None,
        timeout=_OPTIONS_TIMEOUT,
    )
    voices, models = payload.get("voices"), payload.get("models")
    if not isinstance(voices, list) or not isinstance(models, list):
        raise XiaopaiError("设置服务响应异常", status=502)
    return {"voices": voices, "models": models}


def request_clone(*, base_url: str, secret: str) -> dict:
    payload = _call_json(
        method="POST",
        url=f"{base_url}/internal/xiaozhi/agent/clone",
        secret=secret,
        body={},
        timeout=_WRITE_TIMEOUT,
    )
    agent_id = payload.get("agent_id")
    if not isinstance(agent_id, str) or not agent_id:
        raise XiaopaiError("设置服务响应异常", status=502)
    return payload


def request_config(
    *, base_url: str, secret: str, agent_id: str, updates: dict
) -> dict:
    payload = _call_json(
        method="POST",
        url=f"{base_url}/internal/xiaozhi/config",
        secret=secret,
        body={"agent_id": agent_id, **updates},
        timeout=_WRITE_TIMEOUT,
    )
    if payload.get("ok") is not True:
        raise XiaopaiError("设置服务响应异常", status=502)
    return payload


def request_bind(*, base_url: str, secret: str, agent_id: str, code: str) -> dict:
    payload = _call_json(
        method="POST",
        url=f"{base_url}/internal/xiaozhi/agent/bind",
        secret=secret,
        body={"agent_id": agent_id, "verification_code": code},
        timeout=_WRITE_TIMEOUT,
    )
    if payload.get("ok") is not True:
        raise XiaopaiError("设置服务响应异常", status=502)
    return payload
