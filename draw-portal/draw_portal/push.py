from __future__ import annotations

import os
import secrets
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from draw_portal.crypto import decrypt_token


@dataclass(frozen=True)
class PublishResult:
    gcode_url: str
    preview_url: str
    basename: str


def _new_basename() -> str:
    token = secrets.token_urlsafe(16).replace("-", "_")[:22]
    return f"draw_{time.strftime('%Y%m%d_%H%M%S')}_{token}"


def _install_capability_pair(
    *,
    gcode_src: Path,
    png_src: Path,
    mcp_output_dir: Path,
    public_base_url: str,
    basename: str | None = None,
    mtime_offset: float = 0.0,
) -> PublishResult:
    """写入 MCP output/ 并生成公网 URL。

    必须刷新 mtime：MCP `/files/` 按磁盘 mtime 判 600s TTL。
    `shutil.copy2` 会保留图库旧 mtime，导致「图库重发」立刻被当过期 404，
    设备预览下载失败后清屏（用户感知：预览没完成就退出）。

    mtime_offset 用于多页文档错峰 TTL（protocol §4.2）：第 i 页 mtime 前移
    估时（未来 mtime，服务端 _serve_file 容忍），公式同源 hutuji_mcp.article_pages。
    """
    if not gcode_src.is_file() or not png_src.is_file():
        raise FileNotFoundError("job 产物不完整")
    base = basename or _new_basename()
    mcp_output_dir.mkdir(parents=True, exist_ok=True)
    gcode_dst = mcp_output_dir / f"{base}.gcode"
    png_dst = mcp_output_dir / f"{base}.png"
    shutil.copyfile(gcode_src, gcode_dst)
    shutil.copyfile(png_src, png_dst)
    now = time.time() + mtime_offset
    os.utime(gcode_dst, (now, now))
    os.utime(png_dst, (now, now))
    prefix = public_base_url.rstrip("/")
    return PublishResult(
        gcode_url=f"{prefix}/files/{base}.gcode",
        preview_url=f"{prefix}/files/{base}.png",
        basename=base,
    )


def publish_doc_pages(
    *,
    job_dir: Path,
    mcp_output_dir: Path,
    public_base_url: str,
) -> list[PublishResult]:
    """多页文档整篇发布：每页独立 capability pair，mtime 按页估时错峰。

    页估时（行数→秒）公式唯一来源是 hutuji_mcp.article_pages.page_ttl_offsets
    （语音 hutuji_write 与网页文档导入共用，防两链漂移出不同 TTL 窗口）。
    返回按页序；[0] 即第 1 页（job 主 basename）。
    """
    import json

    from hutuji_mcp.article_pages import page_ttl_offsets

    meta = json.loads((job_dir / "pages.json").read_text(encoding="utf-8"))
    offsets = page_ttl_offsets(meta["page_lines"])
    results: list[PublishResult] = []
    for i, offset in enumerate(offsets, start=1):
        results.append(
            _install_capability_pair(
                gcode_src=job_dir / "pages" / f"page_{i:03d}.gcode",
                png_src=job_dir / "pages" / f"page_{i:03d}.png",
                mcp_output_dir=mcp_output_dir,
                public_base_url=public_base_url,
                mtime_offset=offset,
            )
        )
    return results


def publish_job_files(
    *,
    job_dir: Path,
    mcp_output_dir: Path,
    public_base_url: str,
) -> PublishResult:
    """复制 job 产物到 MCP output/，生成公网 capability URL。"""
    return _install_capability_pair(
        gcode_src=job_dir / "draw.gcode",
        png_src=job_dir / "preview.png",
        mcp_output_dir=mcp_output_dir,
        public_base_url=public_base_url,
    )


def push_tool_call(
    *,
    api_base: str,
    device_token: str,
    name: str,
    arguments: dict[str, str],
    timeout: int = 30,
) -> tuple[bool, str]:
    """经小智设备消息 API 调设备侧 MCP 工具（tools/call 通用面）。"""
    import json
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    url = f"{api_base.rstrip('/')}/messaging/device/tools/call"
    body = json.dumps({"name": name, "arguments": arguments}, ensure_ascii=False).encode("utf-8")
    req = Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {device_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return False, f"HTTP {resp.status}"
            return True, resp.read().decode("utf-8", "replace")
    except HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except URLError as exc:
        return False, f"网络 {type(exc.reason).__name__}"

def probe_plotter_sku(
    *,
    api_base: str,
    device_token: str,
    timeout: int = 15,
) -> str | None:
    """经 hutuji.status 探测设备机型词汇（protocol §10.4.15）。

    命中 plotter_sku 字段返回其值；字段缺席（1.0.8 旧固件）/状态异常/网络失败
    一律 None（调用方回落库值 "paper"，不写库）。
    """
    import json

    from draw_portal.mcp_result import unwrap_tool_text

    ok, raw = push_tool_call(
        api_base=api_base,
        device_token=device_token,
        name="hutuji.status",
        arguments={},
        timeout=timeout,
    )
    if not ok:
        return None
    try:
        payload = json.loads(unwrap_tool_text(raw))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    sku = payload.get("plotter_sku")
    if sku in ("nopaper", "paper"):
        return str(sku)
    return None



def push_draw_to_device(
    *,
    api_base: str,
    device_token: str,
    gcode_url: str,
    preview_url: str,
    pages_arg: str | None = None,
) -> tuple[bool, str]:
    # pages 键序/紧凑格式由 hutuji_mcp.article_pages.pages_argument 生成
    # （设备 ParseArticlePages 纯扫描器，protocol §4.2）；多页文档才带。
    arguments: dict[str, str] = {"url": gcode_url, "preview_url": preview_url}
    if pages_arg:
        arguments["pages"] = pages_arg
    ok, result = push_tool_call(
        api_base=api_base,
        device_token=device_token,
        name="hutuji.draw",
        arguments=arguments,
    )
    return (True, "hutuji.draw ok") if ok else (False, result)


def auto_confirm_after_preview(
    *,
    api_base: str,
    device_token: str,
    timeout_s: float = 90.0,
    poll_s: float = 1.5,
) -> tuple[bool, str]:
    """小程序已预览过：等设备进入 awaiting_confirmation 后自动 hutuji.confirm。

    设备契约仍要求先 draw（下载 PNG/预取 G-code），不能跳过预览态；门户侧代点
    「开始画」，用户不必再在小派屏上确认。超时/失败不撤销已推预览。
    """
    import json

    from draw_portal.mcp_result import unwrap_tool_text

    deadline = time.monotonic() + timeout_s
    last_hint = ""
    while time.monotonic() < deadline:
        ok, raw = push_tool_call(
            api_base=api_base,
            device_token=device_token,
            name="hutuji.status",
            arguments={},
            timeout=15,
        )
        if ok:
            text = unwrap_tool_text(raw)
            state = ""
            awaiting = False
            try:
                payload = json.loads(text)
                if isinstance(payload, dict):
                    state = str(payload.get("state") or "")
                    awaiting = bool(payload.get("awaiting_confirmation")) or (
                        state == "awaiting_confirmation"
                    )
            except json.JSONDecodeError:
                awaiting = "awaiting_confirmation" in text
                state = "awaiting_confirmation" if awaiting else ""
            last_hint = state or text[:80]
            if awaiting:
                cok, cres = push_tool_call(
                    api_base=api_base,
                    device_token=device_token,
                    name="hutuji.confirm",
                    arguments={},
                    timeout=30,
                )
                return (True, "hutuji.confirm ok") if cok else (False, cres)
            # 已越过确认门（重发/竞态）：视为已开画，勿再 confirm
            if state in (
                "downloading",
                "verifying",
                "streaming",
                "paused",
                "paper_change",
                "done",
            ):
                return True, f"already {state}"
        time.sleep(poll_s)
    return False, f"confirm timeout ({last_hint or 'no status'})"


def push_for_user_binding(
    *,
    api_base: str,
    token_cipher_key: bytes,
    device_token_enc: str,
    gcode_url: str,
    preview_url: str,
    pages_arg: str | None = None,
) -> tuple[bool, str]:
    token = decrypt_token(token_cipher_key, device_token_enc)
    return push_draw_to_device(
        api_base=api_base,
        device_token=token,
        gcode_url=gcode_url,
        preview_url=preview_url,
        pages_arg=pages_arg,
    )

# ---------------------------------------------------------------------------
# messaging token 终身自治（2026-09-07 用户原话「其他的全部自动化」）
#
# 背景：设备 announce 上报的 token 不可信（可能是 NVS websocket.token 或历史
# 占位 test-token），且控制台「主题配置」面板每次打开都重签作废旧 token——
# 绑定表里的 messaging JWT 天然会过期。自愈策略：
#   1) 绑定时尽量现场铸新（bind_complete 调 mint_messaging_token）；
#   2) 推送撞 401/403 时铸新 → 回写绑定表 → 重试一次（push_draw_self_heal）。
# 控制台 JWT 来源：/etc/hutuji/kb-admin.env 的 XIAOZHI_TOKEN（token-auto 已
# 自动续期，单一真值源），或 env DRAW_PORTAL_XIAOZHI_TOKEN 显式覆盖。
# 签发端点 = 官方控制台 POST /agents/generate-messaging-token {macAddress}。
# ---------------------------------------------------------------------------

_KB_ADMIN_ENV_PATH = Path("/etc/hutuji/kb-admin.env")


def load_console_token() -> str:
    """控制台（purpose=login）JWT：env 优先，kb-admin.env 兜底；找不到回 ''。"""
    token = os.environ.get("DRAW_PORTAL_XIAOZHI_TOKEN", "").strip()
    if token:
        return token
    try:
        for line in _KB_ADMIN_ENV_PATH.read_text(encoding="utf-8").splitlines():
            if line.startswith("XIAOZHI_TOKEN="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def mint_messaging_token(*, api_base: str, mac: str, timeout: int = 15) -> str:
    """用控制台 JWT 现场签发设备 messaging JWT；任何失败回 ''（调用方降级）。"""
    import json as _json
    from urllib.error import HTTPError as _HTTPError, URLError as _URLError
    from urllib.request import Request as _Request, urlopen as _urlopen

    console_token = load_console_token()
    if not console_token:
        return ""
    req = _Request(
        f"{api_base.rstrip('/')}/agents/generate-messaging-token",
        data=_json.dumps({"macAddress": mac}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {console_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with _urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return ""
            payload = _json.loads(resp.read().decode("utf-8", "replace"))
    except (_HTTPError, _URLError, ValueError, OSError):
        return ""
    token = payload.get("token")
    return token if isinstance(token, str) else ""


def push_draw_self_heal(
    *,
    api_base: str,
    token_cipher_key: bytes,
    device_mac: str,
    device_token_enc: str,
    gcode_url: str,
    preview_url: str,
    pages_arg: str | None = None,
    on_token_minted=None,
    auto_confirm: bool = False,
) -> tuple[bool, str]:
    """推送 + 401/403/404 自愈：铸新 messaging token → 回调回写 → 重试一次。

    on_token_minted(new_enc)：由调用方把新 token（已加密）写回绑定表。
    铸新成功后另写 MCP 共享明文文件（`hutuji_mcp.device_messaging.save_device_token`），
    使语音链式推送与小程序 send 不因 mcp.env 陈旧分叉。

    auto_confirm：小程序/网页已预览过时，draw 成功后轮询 status 并代调
    hutuji.confirm，设备不必再等用户点屏（2026-09-09 用户口径）。
    """
    active_token = decrypt_token(token_cipher_key, device_token_enc)
    ok, summary = push_draw_to_device(
        api_base=api_base,
        device_token=active_token,
        gcode_url=gcode_url,
        preview_url=preview_url,
        pages_arg=pages_arg,
    )
    # 404「Device not online」同样铸：整片刷机重生 client UUID 后旧 messaging token
    # 指向失效 clientId（2026-09-08 实机定性），重铸取注册表当前 clientId；
    # 设备真离线时重试自然仍败，不误报也不阻断。
    if (not ok) and summary in ("HTTP 401", "HTTP 403", "HTTP 404"):
        fresh = mint_messaging_token(api_base=api_base, mac=device_mac)
        if not fresh:
            return False, summary + "（自愈失败：控制台 token 不可用）"
        from draw_portal.crypto import encrypt_token

        new_enc = encrypt_token(token_cipher_key, fresh)
        if on_token_minted is not None:
            on_token_minted(new_enc)
        # 与 MCP 语音链式推送共用落盘真值，避免 portal 已自愈而 mcp.env 仍是旧 JWT
        # （2026-09-08：小程序能预览、小派自生成不能 —— MCP 链式 404 无自愈）。
        try:
            from hutuji_mcp.device_messaging import save_device_token

            save_device_token(fresh)
        except Exception:  # noqa: BLE001 — 落盘失败不阻断已铸成功的 portal 重试
            pass
        ok, summary2 = push_draw_to_device(
            api_base=api_base,
            device_token=fresh,
            gcode_url=gcode_url,
            preview_url=preview_url,
            pages_arg=pages_arg,
        )
        if ok:
            active_token = fresh
            summary = "hutuji.draw ok（token 已自愈）"
        else:
            return False, summary2
    if not ok:
        return False, summary
    if not auto_confirm:
        return True, summary
    cok, csum = auto_confirm_after_preview(
        api_base=api_base,
        device_token=active_token,
    )
    if cok:
        return True, summary + "；已自动开始画"
    # 预览已上屏；确认失败不把整次 send 判失败，便于用户改用控制页「开始画」
    return True, summary + f"；自动开始画未完成：{csum}"
