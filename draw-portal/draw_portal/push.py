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
