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
) -> tuple[bool, str]:
    """推送 + 401/403 自愈：铸新 messaging token → 回调回写 → 重试一次。

    on_token_minted(new_enc)：由调用方把新 token（已加密）写回绑定表。
    """
    ok, summary = push_for_user_binding(
        api_base=api_base,
        token_cipher_key=token_cipher_key,
        device_token_enc=device_token_enc,
        gcode_url=gcode_url,
        preview_url=preview_url,
        pages_arg=pages_arg,
    )
    # 404「Device not online」同样铸：整片刷机重生 client UUID 后旧 messaging token
    # 指向失效 clientId（2026-09-08 实机定性），重铸取注册表当前 clientId；
    # 设备真离线时重试自然仍败，不误报也不阻断。
    if ok or summary not in ("HTTP 401", "HTTP 403", "HTTP 404"):
        return ok, summary
    fresh = mint_messaging_token(api_base=api_base, mac=device_mac)
    if not fresh:
        return False, summary + "（自愈失败：控制台 token 不可用）"
    from draw_portal.crypto import encrypt_token

    new_enc = encrypt_token(token_cipher_key, fresh)
    if on_token_minted is not None:
        on_token_minted(new_enc)
    ok2, summary2 = push_draw_to_device(
        api_base=api_base,
        device_token=fresh,
        gcode_url=gcode_url,
        preview_url=preview_url,
        pages_arg=pages_arg,
    )
    return (True, "hutuji.draw ok（token 已自愈）") if ok2 else (False, summary2)
