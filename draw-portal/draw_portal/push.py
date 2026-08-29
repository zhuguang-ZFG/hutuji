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
) -> PublishResult:
    """写入 MCP output/ 并生成公网 URL。

    必须刷新 mtime：MCP `/files/` 按磁盘 mtime 判 600s TTL。
    `shutil.copy2` 会保留图库旧 mtime，导致「图库重发」立刻被当过期 404，
    设备预览下载失败后清屏（用户感知：预览没完成就退出）。
    """
    if not gcode_src.is_file() or not png_src.is_file():
        raise FileNotFoundError("job 产物不完整")
    base = basename or _new_basename()
    mcp_output_dir.mkdir(parents=True, exist_ok=True)
    gcode_dst = mcp_output_dir / f"{base}.gcode"
    png_dst = mcp_output_dir / f"{base}.png"
    shutil.copyfile(gcode_src, gcode_dst)
    shutil.copyfile(png_src, png_dst)
    now = time.time()
    os.utime(gcode_dst, (now, now))
    os.utime(png_dst, (now, now))
    prefix = public_base_url.rstrip("/")
    return PublishResult(
        gcode_url=f"{prefix}/files/{base}.gcode",
        preview_url=f"{prefix}/files/{base}.png",
        basename=base,
    )


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


def push_draw_to_device(
    *,
    api_base: str,
    device_token: str,
    gcode_url: str,
    preview_url: str,
) -> tuple[bool, str]:
    import json
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    url = f"{api_base.rstrip('/')}/messaging/device/tools/call"
    body = json.dumps(
        {
            "name": "hutuji.draw",
            "arguments": {"url": gcode_url, "preview_url": preview_url},
        },
        ensure_ascii=False,
    ).encode("utf-8")
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
        with urlopen(req, timeout=30) as resp:
            if resp.status != 200:
                return False, f"HTTP {resp.status}"
            return True, "hutuji.draw ok"
    except HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except URLError as exc:
        return False, f"网络 {type(exc.reason).__name__}"


def push_for_user_binding(
    *,
    api_base: str,
    token_cipher_key: bytes,
    device_token_enc: str,
    gcode_url: str,
    preview_url: str,
) -> tuple[bool, str]:
    token = decrypt_token(token_cipher_key, device_token_enc)
    return push_draw_to_device(
        api_base=api_base,
        device_token=token,
        gcode_url=gcode_url,
        preview_url=preview_url,
    )
