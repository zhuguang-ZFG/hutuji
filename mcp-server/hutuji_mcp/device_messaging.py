"""设备 messaging 链式推送：token 文件 + 401/403/404 自愈重铸。

根因（2026-09-08）：语音/自生成走 MCP `HUTUJI_XIAOZHI_DEVICE_TOKEN` 链式
`hutuji.draw`，撞 404「Device not online」只记日志不重铸；小程序 send 走
portal `push_draw_self_heal` 会重铸 messaging JWT（刷机后 client UUID 轮换）。
异步 draw 已回 accepted，云端不会再走第二步，链式失败 = 屏上永远无预览。

token 真值优先读 `/var/lib/hutuji/device_messaging.token`（hutuji 可写，
portal 自愈与 MCP 自愈共用），回落 env。每次链式调用现读文件，worker 无需重启。
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TOKEN_FILE = "/var/lib/hutuji/device_messaging.token"
DEFAULT_PORTAL_DB = "/var/lib/hutuji/draw-portal/data.db"
_KB_ADMIN_ENV = Path("/etc/hutuji/kb-admin.env")


def token_file_path() -> Path:
    return Path(os.environ.get("HUTUJI_DEVICE_TOKEN_FILE", DEFAULT_TOKEN_FILE))


def load_device_token(*, env_fallback: str = "") -> str:
    path = token_file_path()
    try:
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
    except OSError:
        pass
    return (env_fallback or os.environ.get("HUTUJI_XIAOZHI_DEVICE_TOKEN", "")).strip()


def save_device_token(token: str) -> None:
    """落盘明文 messaging JWT（600）。失败只打日志——调用方仍可用内存 token 重试。"""
    token = token.strip()
    if not token:
        return
    path = token_file_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(token + "\n", encoding="utf-8")
        os.chmod(path, 0o600)
    except OSError as exc:
        logger.warning("设备 messaging token 落盘失败（%s）", type(exc).__name__)


def load_device_mac() -> str:
    mac = os.environ.get("HUTUJI_DEVICE_MAC", "").strip()
    if mac:
        return mac
    db = Path(os.environ.get("DRAW_PORTAL_DB", DEFAULT_PORTAL_DB))
    try:
        row = sqlite3.connect(f"file:{db}?mode=ro", uri=True).execute(
            "SELECT device_mac FROM device_bindings ORDER BY bound_at DESC LIMIT 1"
        ).fetchone()
        if row and row[0]:
            return str(row[0]).strip()
    except (OSError, sqlite3.Error):
        pass
    return ""


def load_console_token() -> str:
    for key in ("HUTUJI_XIAOZHI_TOKEN", "DRAW_PORTAL_XIAOZHI_TOKEN"):
        token = os.environ.get(key, "").strip()
        if token:
            return token
    try:
        for line in _KB_ADMIN_ENV.read_text(encoding="utf-8").splitlines():
            if line.startswith("XIAOZHI_TOKEN="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def mint_messaging_token(*, api_base: str, mac: str, timeout: float = 15.0) -> str:
    """控制台 JWT → 设备 messaging JWT；失败回空串。"""
    import urllib.error
    import urllib.request

    console = load_console_token()
    if not console or not mac:
        return ""
    req = urllib.request.Request(
        f"{api_base.rstrip('/')}/agents/generate-messaging-token",
        data=json.dumps({"macAddress": mac}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {console}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return ""
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError):
        return ""
    token = payload.get("token") if isinstance(payload, dict) else None
    return token if isinstance(token, str) else ""


async def post_device_tool(
    *,
    api_base: str,
    device_token: str,
    name: str,
    arguments: dict[str, str],
    timeout: float = 15.0,
) -> tuple[int, str]:
    """返回 (status_code, 简短摘要)；不回显 token/URL。"""
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{api_base.rstrip('/')}/messaging/device/tools/call",
            json={"name": name, "arguments": arguments},
            headers={"Authorization": f"Bearer {device_token}"},
        )
    return resp.status_code, f"http_{resp.status_code}"


async def post_device_tool_json(
    *,
    api_base: str,
    device_token: str,
    name: str,
    arguments: dict[str, str],
    timeout: float = 15.0,
) -> tuple[int, dict | None]:
    """tools/call 并解析回包正文（机型探测用，2026-09-11）。

    与 post_device_tool 同一请求面，但要拿到 status 对象里的字段
    （plotter_sku），故解析信封：小智官方 data 信封优先，MCP CallToolResult
    content 次之（与 draw-portal unwrap_tool_text 同形状认知，但此处只解
    JSON 对象，不解文本，避免复制 portal 的全量信封逻辑）。
    返回 (status_code, dict|None)：HTTP 200 且信封里解出 JSON 对象才有值。
    """
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{api_base.rstrip('/')}/messaging/device/tools/call",
            json={"name": name, "arguments": arguments},
            headers={"Authorization": f"Bearer {device_token}"},
        )
    if resp.status_code != 200:
        return resp.status_code, None
    try:
        data = resp.json()
    except ValueError:
        return resp.status_code, None
    if not isinstance(data, dict):
        return resp.status_code, None
    inner: object = data.get("data", data)
    if isinstance(inner, str):
        try:
            inner = json.loads(inner)
        except ValueError:
            inner = None
    if isinstance(inner, dict) and isinstance(inner.get("text"), str):
        try:
            text_payload = json.loads(inner["text"])
        except ValueError:
            text_payload = None
        if isinstance(text_payload, dict):
            return resp.status_code, text_payload
    return resp.status_code, inner if isinstance(inner, dict) else None


async def chain_hutuji_draw(
    *,
    api_base: str,
    env_token: str,
    url: str,
    preview_url: str,
    pages_arg: str | None = None,
) -> tuple[bool, str]:
    """链式 hutuji.draw；401/403/404 自愈重铸一次（与 portal push_draw_self_heal 同口径）。"""
    arguments: dict[str, str] = {"url": url, "preview_url": preview_url}
    if pages_arg:
        arguments["pages"] = pages_arg
    token = load_device_token(env_fallback=env_token)
    if not token:
        return False, "no_token"
    status, detail = await post_device_tool(
        api_base=api_base, device_token=token, name="hutuji.draw", arguments=arguments
    )
    if status == 200:
        return True, "ok"
    if status not in (401, 403, 404):
        return False, detail
    mac = load_device_mac()
    fresh = mint_messaging_token(api_base=api_base, mac=mac)
    if not fresh:
        return False, detail + "+heal_mint_fail"
    save_device_token(fresh)
    status2, detail2 = await post_device_tool(
        api_base=api_base, device_token=fresh, name="hutuji.draw", arguments=arguments
    )
    if status2 == 200:
        logger.info("设备链式调用 hutuji.draw 已受理（token 已自愈）")
        return True, "ok_healed"
    return False, detail2 + "+heal_retry"
