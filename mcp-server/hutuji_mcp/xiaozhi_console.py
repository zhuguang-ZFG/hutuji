"""小智控制台 API 客户端：小派音色/模型切换（2026-09-12，§10.4.19）。

只服务本机 /internal/xiaozhi/* 内部路由。凭据 = config.xiaozhi_console_token
（HUTUJI_XIAOZHI_TOKEN，与激活中转共用同一 JWT；token-auto 续期后 restart mcp
即换新值，本模块不缓存 token）。

用户切音色/模型**只能改自己的设备**（2026-09-12 用户拍板，推翻同日早先
「共享 agent 全局切换」口径）：控制台音色/模型是 agent 级配置，因此采用
「按需克隆 agent」——首次个性化时从基础 agent（config.xiaozhi_agent_id，
即语音路径共享的 2094251）克隆一个专属 agent（人格/KB/MCP 接入点/记忆档
整包复制），此后该用户的所有切换只写克隆 agent；设备经 6 位验证码绑定到
克隆 agent 后生效。未个性化的设备继续走共享 agent，现网零变化。

端点形状 2026-09-12 生产实测（探针建/查/写/删测试 agent，见 transcript）：
- POST /agents {"agent_name"}                    → data.id（新建默认音色小合/llm xz-lite/character 空）
- GET  /agents/{id}                              → data.agent 全量配置 dict
- POST /agents/{id}/config（11 字段整包）         → 200 CONFIG_UPDATE_SUCCESS
- POST /agents/{id}/devices {"verificationCode"} → 无效码 400 INVALID_VERIFICATION_CODE（无副作用）
- GET  /agents/{id}/devices                      → data=[...] 设备列表
- POST /agents/delete {"id"}                     → 200（清理用，不在产品流）
- config 写入对不在账号清单内的 voice 返回 400 INVALID_TTS_VOICE（服务端也有白名单闸）

安全口径：
- 只允许 https 基址（非 https 直接 XiaozhiConsoleError，fail-closed）。
- 窄异常：错误只含 HTTP 状态码/类型名/服务端 code，不回显上游响应正文
  （同 modelscope 口径）；服务端 code（INVALID_TTS_VOICE 等）可透传给上游排障。
- config 写回 = GET 全量 → 仅覆盖白名单字段（tts_voice / llm_model）→ POST
  整包；character/KB/MCP 接入点等一律原样透传，禁止顺手改。
- 清单缓存 10 分钟（进程内，键含 token；token 变化自动失效）。
"""
from __future__ import annotations

import json
import time
import urllib.request
from urllib.error import HTTPError, URLError

LIST_CACHE_TTL_SECONDS = 600.0

_CONFIG_WRITABLE_FIELDS = ("tts_voice", "llm_model")
# GET /agents/{id} 实测的整包字段（§10.4.19）；克隆时逐字段复制基础 agent。
_AGENT_BODY_FIELDS = (
    "language",
    "tts_voice",
    "character",
    "asr_speed",
    "tts_speech_speed",
    "tts_pitch",
    "llm_model",
    "memory_type",
    "teen_mode",
    "mcp_endpoints",
    "knowledge_base_ids",
)
_VOICE_LIST_TIMEOUT = 10
_AGENT_TIMEOUT = 20
_CACHE: dict[tuple[str, str], tuple[float, tuple[list[dict], list[dict]]]] = {}


class XiaozhiConsoleError(Exception):
    """控制台调用失败；文案只含状态码/类型名/服务端 code，不回显上游正文。"""


def _validate_base(base_url: str) -> str:
    from urllib.parse import urlsplit

    parts = urlsplit(base_url.strip())
    if parts.scheme != "https" or not parts.netloc or parts.username or parts.password:
        raise XiaozhiConsoleError("XIAOZHI_API 基址必须是无凭据的 https 绝对 URL")
    return base_url.rstrip("/")


def _api_json(method: str, url: str, token: str, body: dict | None, timeout: float) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        code = ""
        try:
            code = json.loads(exc.read().decode("utf-8")).get("code") or ""
        except Exception:
            code = ""
        suffix = f" ({code})" if code else ""
        raise XiaozhiConsoleError(f"控制台 HTTP {exc.code}{suffix}") from None
    except (URLError, TimeoutError, OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise XiaozhiConsoleError(f"控制台网络异常 {type(exc).__name__}") from None
    if not isinstance(payload, dict) or payload.get("success") is not True:
        code = payload.get("code") if isinstance(payload, dict) else ""
        suffix = f" ({code})" if code else ""
        raise XiaozhiConsoleError(f"控制台回包异常{suffix}")
    return payload


def _cache_lookup(key: tuple[str, str]) -> tuple[list[dict], list[dict]] | None:
    entry = _CACHE.get(key)
    if entry is None:
        return None
    at, value = entry
    if time.monotonic() - at > LIST_CACHE_TTL_SECONDS:
        _CACHE.pop(key, None)
        return None
    return value


def _cache_put(key: tuple[str, str], value: tuple[list[dict], list[dict]]) -> None:
    # 惰性清理：留最近 8 组基址即可（正常部署只有一个基址）
    if len(_CACHE) > 8:
        for k in sorted(_CACHE, key=lambda k: _CACHE[k][0])[:-8]:
            _CACHE.pop(k, None)
    _CACHE[key] = (time.monotonic(), value)


def _voices_from_payload(data: dict) -> list[dict]:
    groups = data.get("tts_voices")
    if not isinstance(groups, dict):
        raise XiaozhiConsoleError("音色清单形状异常")
    out: list[dict] = []
    for lang, items in groups.items():
        if not isinstance(lang, str) or not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            vid, vname = item.get("voice_id"), item.get("voice_name")
            if not isinstance(vid, str) or not vid or not isinstance(vname, str):
                continue
            out.append({
                "voice_id": vid,
                "voice_name": vname,
                "language": lang if isinstance(lang, str) else "",
                "is_beta": bool(item.get("is_beta")),
            })
    return out


def _models_from_payload(data: dict) -> list[dict]:
    rows = data.get("modelList")
    if not isinstance(rows, list):
        raise XiaozhiConsoleError("模型清单形状异常")
    out: list[dict] = []
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("name"), str) and row["name"]:
            out.append({"name": row["name"], "description": str(row.get("description") or "")})
    return out


def fetch_options(token: str, base_url: str) -> dict:
    """音色 + 模型清单（带 10 分钟缓存）。返回 {voices, models}。"""
    if not token:
        raise XiaozhiConsoleError("未配置控制台凭据")
    base = _validate_base(base_url)
    key = (base, token)
    cached = _cache_lookup(key)
    if cached is not None:
        voices, models = cached
        return {"voices": voices, "models": models}
    voices_payload = _api_json("GET", f"{base}/user/tts-list", token, None, _VOICE_LIST_TIMEOUT)
    models_payload = _api_json("GET", f"{base}/roles/model-list", token, None, _VOICE_LIST_TIMEOUT)
    voices = _voices_from_payload(voices_payload.get("data") or {})
    models = _models_from_payload(models_payload.get("data") or {})
    _cache_put(key, (voices, models))
    return {"voices": voices, "models": models}


def get_agent_config(token: str, base_url: str, agent_id: str) -> dict:
    base = _validate_base(base_url)
    payload = _api_json("GET", f"{base}/agents/{agent_id}", token, None, _AGENT_TIMEOUT)
    agent = (payload.get("data") or {}).get("agent")
    if not isinstance(agent, dict):
        raise XiaozhiConsoleError("agent 详情形状异常")
    return agent


def update_agent_config(
    token: str, base_url: str, agent_id: str, updates: dict
) -> dict:
    """整包写回：GET 全量 → 白名单字段覆盖 → POST；返回新 {tts_voice, llm_model}。"""
    unknown = [k for k in updates if k not in _CONFIG_WRITABLE_FIELDS]
    if unknown:
        raise XiaozhiConsoleError("不可写字段: " + ",".join(sorted(unknown)))
    agent = get_agent_config(token, base_url, agent_id)
    body = {k: agent.get(k) for k in _AGENT_BODY_FIELDS}
    body.update(updates)
    base = _validate_base(base_url)
    _api_json("POST", f"{base}/agents/{agent_id}/config", token, body, _AGENT_TIMEOUT)
    return {"tts_voice": body["tts_voice"], "llm_model": body["llm_model"]}


def clone_agent(token: str, base_url: str, source_agent_id: str, name: str) -> dict:
    """克隆基础 agent：新建 → 复制 §10.4.19 整包字段 → 返回 {agent_id}。

    新建 agent 的默认值（实测）：tts=wanwanxiaohe / llm=xz-lite / character 空；
    必须 POST 整包才带得上人格与 KB/MCP 接入点。character 非法 voice 等服务端
    拒绝直接抛 XiaozhiConsoleError（此时新 agent 已存在但配置不完整，调用方
    应感知失败不再引用该 id）。
    """
    base = _validate_base(base_url)
    created = _api_json(
        "POST", f"{base}/agents", token, {"agent_name": name}, _AGENT_TIMEOUT
    )
    new_id = (created.get("data") or {}).get("id")
    if not isinstance(new_id, int):
        raise XiaozhiConsoleError("建 agent 回包形状异常")
    src = get_agent_config(token, base_url, source_agent_id)
    body = {k: src.get(k) for k in _AGENT_BODY_FIELDS}
    body["agent_name"] = name
    _api_json("POST", f"{base}/agents/{new_id}/config", token, body, _AGENT_TIMEOUT)
    return {"agent_id": str(new_id), "tts_voice": body["tts_voice"], "llm_model": body["llm_model"]}


def bind_device_by_code(token: str, base_url: str, agent_id: str, code: str) -> dict:
    """把验证码当前所属设备绑到指定 agent（控制台同款换绑语义）。"""
    base = _validate_base(base_url)
    _api_json(
        "POST",
        f"{base}/agents/{agent_id}/devices",
        token,
        {"verificationCode": code},
        _AGENT_TIMEOUT,
    )
    return {"agent_id": str(agent_id)}


def list_devices(token: str, base_url: str, agent_id: str) -> list[dict]:
    base = _validate_base(base_url)
    payload = _api_json("GET", f"{base}/agents/{agent_id}/devices", token, None, _AGENT_TIMEOUT)
    data = payload.get("data")
    if not isinstance(data, list):
        raise XiaozhiConsoleError("设备清单形状异常")
    return [d for d in data if isinstance(d, dict)]
