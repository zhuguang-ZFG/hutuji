"""小程序语音对话智能体（portal 编排 LLM + 设备工具 + 画/写）。"""
from __future__ import annotations

import json
import os
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

import httpx

VOICE_DEVICE_ALLOWLIST = frozenset({
    "hutuji.status",
    "hutuji.remember",
    "hutuji.recall",
    "hutuji.forget",
    "hutuji.abort",
})
VOICE_PORTAL_ALLOWLIST = frozenset({"voice.draw", "voice.write"})
VOICE_TOOL_ALLOWLIST = VOICE_DEVICE_ALLOWLIST | VOICE_PORTAL_ALLOWLIST
MAX_HISTORY_TURNS = 6  # 一轮可含多条（user/assistant tool_calls/tool/assistant），按整轮裁剪
MAX_TOOL_ROUNDS = 3
_LLM_TIMEOUT = 60.0

_SYSTEM_PROMPT = (
    "你是「小派」，微信小程序里的 AI 助手，帮用户了解并控制已绑定的丹青绘图机。"
    "画画用 voice.draw（参数 description），写字用 voice.write（参数 text）。"
    "查询状态用 hutuji.status；「记住/以后都」用 hutuji.remember，"
    "问「你记得吗」先 hutuji.recall，忘掉用 hutuji.forget，紧急停止用 hutuji.abort。"
    "生成成功后告诉用户：已推到丹青预览，请在小程序点「开始画」或「取消」；"
    "不要假装已经开画，也不要声称自己能点确认。"
    "禁止编造设备状态；需要实时信息时必须调 hutuji.status。"
    "未绑定时提示「请先绑定丹青」。"
    "回答简短口语化中文。密码、验证码、身份证号不要记。"
)


class VoiceAgentError(Exception):
    """语音对话失败。"""


# 量产内存闸：sid 由客户端每次进页新生成，无淘汰会让会话历史（含对话隐私）
# 在单进程 uvicorn 里无限驻留——OrderedDict + LRU 256 上界，触及即提热。
_MAX_SESSIONS = 256
_sessions: "OrderedDict[str, list[dict[str, Any]]]" = OrderedDict()


def _get_session(sid: str) -> list[dict[str, Any]]:
    history = _sessions.get(sid)
    if history is None:
        if len(_sessions) >= _MAX_SESSIONS:
            _sessions.popitem(last=False)
        history = []
        _sessions[sid] = history
    else:
        _sessions.move_to_end(sid)
    return history


def llm_configured() -> bool:
    return all(
        os.environ.get(name, "").strip()
        for name in ("HUTUJI_LLM_API_KEY", "HUTUJI_LLM_BASE_URL", "HUTUJI_LLM_MODEL")
    )


def _load_llm_config() -> tuple[str, str, str]:
    api_key = os.environ.get("HUTUJI_LLM_API_KEY", "").strip()
    base_url = os.environ.get("HUTUJI_LLM_BASE_URL", "").strip()
    model = os.environ.get("HUTUJI_LLM_MODEL", "").strip()
    if not (api_key and base_url and model):
        raise VoiceAgentError("对话未配置")
    return api_key, base_url, model


def _build_tools_schema() -> list[dict[str, Any]]:
    def _tool(
        name: str, description: str, properties: dict[str, Any], required: list[str]
    ) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }

    return [
        _tool(
            "voice.draw",
            "根据描述生成一幅画并推到丹青预览（需用户在小程序确认才开画）",
            {"description": {"type": "string", "description": "要画的内容描述"}},
            ["description"],
        ),
        _tool(
            "voice.write",
            "把文本排版成手写体并推到丹青预览（需用户在小程序确认才开画）",
            {"text": {"type": "string", "description": "要写的正文"}},
            ["text"],
        ),
        _tool("hutuji.status", "查询丹青当前状态", {}, []),
        _tool("hutuji.abort", "停止当前绘图任务或取消预览", {}, []),
        _tool(
            "hutuji.remember",
            "记住用户告知的事实",
            {
                "key": {"type": "string", "description": "记忆键"},
                "value": {"type": "string", "description": "要记住的内容"},
            },
            ["key", "value"],
        ),
        _tool(
            "hutuji.recall",
            "召回已记住的内容",
            {"key": {"type": "string", "description": "记忆键"}},
            ["key"],
        ),
        _tool(
            "hutuji.forget",
            "忘记指定记忆",
            {"key": {"type": "string", "description": "记忆键，全部清除传 *"}},
            ["key"],
        ),
    ]


def _truncate_trace(text: str, limit: int = 200) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _cap_history(history: list[dict[str, Any]]) -> None:
    """按整轮（user 起头）裁剪：半截裁剪会拆散 assistant tool_calls 与 tool 回包的配对，API 直接 400。"""
    user_idx = [i for i, m in enumerate(history) if m.get("role") == "user"]
    if len(user_idx) > MAX_HISTORY_TURNS:
        del history[: user_idx[-MAX_HISTORY_TURNS]]


def _parse_tool_arguments(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _post_chat(*, url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=_LLM_TIMEOUT) as client:
            resp = client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise VoiceAgentError(f"对话服务失败：{type(exc).__name__}") from exc
    if resp.status_code != 200:
        raise VoiceAgentError(f"对话服务返回 {resp.status_code}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise VoiceAgentError("对话服务回包异常") from exc
    if not isinstance(data, dict):
        raise VoiceAgentError("对话服务回包异常")
    return data


def _extract_assistant_message(data: dict[str, Any]) -> dict[str, Any]:
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise VoiceAgentError("对话服务回包异常") from exc
    if not isinstance(message, dict):
        raise VoiceAgentError("对话服务回包异常")
    return message


PortalToolFn = Callable[[str, dict[str, Any]], tuple[str, dict[str, str] | None]]


def _execute_tool(
    *,
    name: str,
    arguments: dict[str, Any],
    device_mac: str | None,
    push_tool: Callable[[str, dict[str, Any]], str],
    run_portal_tool: PortalToolFn | None,
) -> tuple[str, str, dict[str, str] | None]:
    if name not in VOICE_TOOL_ALLOWLIST:
        return "工具不允许", "error: 工具不允许", None
    if device_mac is None:
        return "请先绑定丹青", "error: 请先绑定丹青", None

    if name in VOICE_PORTAL_ALLOWLIST:
        if run_portal_tool is None:
            return "画写通道未接线", "error: 画写通道未接线", None
        try:
            text, preview = run_portal_tool(name, arguments)
        except Exception as exc:
            # VoiceJobError 等：把白话给模型
            msg = str(exc).strip() or type(exc).__name__
            return msg, f"error: {_truncate_trace(msg)}", None
        return text, f"ok: {_truncate_trace(text)}", preview

    try:
        result = push_tool(name, arguments)
    except Exception as exc:
        summary = f"error: {type(exc).__name__}"
        return "工具调用失败", summary, None
    text = result if isinstance(result, str) else str(result)
    return text, f"ok: {_truncate_trace(text)}", None


def chat(
    *,
    session_id: str,
    user_text: str,
    device_mac: str | None,
    push_tool: Callable[[str, dict[str, Any]], str],
    run_portal_tool: PortalToolFn | None = None,
) -> dict[str, Any]:
    if not llm_configured():
        raise VoiceAgentError("对话未配置")

    sid = session_id.strip()
    if not sid:
        raise VoiceAgentError("会话无效")
    if len(sid) > 64:
        sid = sid[:64]

    text = user_text.strip()
    if not text:
        raise VoiceAgentError("内容为空")

    api_key, base_url, model = _load_llm_config()
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"}
    tools = _build_tools_schema()

    history = _get_session(sid)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        *history,
        {"role": "user", "content": text},
    ]
    tool_traces: list[dict[str, str]] = []
    reply = ""
    last_preview: dict[str, str] | None = None

    for round_idx in range(MAX_TOOL_ROUNDS + 1):
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if round_idx < MAX_TOOL_ROUNDS:
            payload["tools"] = tools

        data = _post_chat(url=url, headers=headers, payload=payload)
        message = _extract_assistant_message(data)
        tool_calls = message.get("tool_calls")

        if not tool_calls:
            content = message.get("content")
            reply = content.strip() if isinstance(content, str) else ""
            break

        if round_idx >= MAX_TOOL_ROUNDS:
            content = message.get("content")
            reply = (
                content.strip()
                if isinstance(content, str)
                else "抱歉，我暂时无法完成这个请求。"
            )
            break

        messages.append(message)
        for tool_call in tool_calls:
            fn = tool_call.get("function") or {}
            name = fn.get("name") if isinstance(fn, dict) else None
            if not isinstance(name, str) or not name:
                continue
            args = _parse_tool_arguments(
                fn.get("arguments") if isinstance(fn, dict) else None
            )
            result_text, summary, preview = _execute_tool(
                name=name,
                arguments=args,
                device_mac=device_mac,
                push_tool=push_tool,
                run_portal_tool=run_portal_tool,
            )
            tool_traces.append({"name": name, "summary": _truncate_trace(summary)})
            if preview:
                last_preview = preview
            tool_call_id = tool_call.get("id")
            if isinstance(tool_call_id, str) and tool_call_id:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": result_text,
                    }
                )

    if not reply:
        reply = "抱歉，我暂时无法完成这个请求。"

    # 历史必须保留本轮完整的工具调用链（assistant tool_calls + tool 回包）：
    # 只存纯文本回复会让模型从「用户要画 → 纯文本应答」的假象学成不再调工具
    # （2026-09-23 生产复现：首轮后 voice.draw 永久消失）。最终文本回复不在 messages 里，单独补。
    history.extend(messages[len(history) + 1 :])  # 本轮 user 起的新增段（system+旧历史之后）
    history.append({"role": "assistant", "content": reply})
    _cap_history(history)

    out: dict[str, Any] = {"reply": reply, "tool_traces": tool_traces}
    if last_preview:
        out["preview"] = last_preview
    return out
