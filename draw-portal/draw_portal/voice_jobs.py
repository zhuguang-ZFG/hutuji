"""小程序语音画/写：生成产物并推预览（不自动 confirm）。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from draw_portal.generate import GenerateError, materialize_generated_job, request_internal_draw
from draw_portal.pipeline import process_write_text
from draw_portal.push import publish_doc_pages, publish_job_files, push_draw_self_heal
from draw_portal.write_layout_params import resolve_article_layout


class VoiceJobError(Exception):
    """语音生图/写字失败（白话 detail）。"""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class VoicePreview:
    kind: str  # draw | write
    job_id: str
    preview_url: str
    gcode_url: str
    pages_arg: str | None = None

    def as_dict(self) -> dict[str, str]:
        out = {
            "kind": self.kind,
            "job_id": self.job_id,
            "preview_url": self.preview_url,
            "gcode_url": self.gcode_url,
        }
        if self.pages_arg:
            out["pages_arg"] = self.pages_arg
        return out


def _push_preview(
    *,
    preview: VoicePreview,
    api_base: str,
    token_cipher_key: bytes,
    device_mac: str,
    device_token_enc: str,
    on_token_minted: Callable[[str], None] | None,
) -> None:
    ok, summary = push_draw_self_heal(
        api_base=api_base,
        token_cipher_key=token_cipher_key,
        device_mac=device_mac,
        device_token_enc=device_token_enc,
        gcode_url=preview.gcode_url,
        preview_url=preview.preview_url,
        pages_arg=preview.pages_arg,
        on_token_minted=on_token_minted,
        auto_confirm=False,
    )
    if not ok:
        raise VoiceJobError(f"推送到写字机失败：{summary}", status=502)


def run_voice_draw(
    *,
    description: str,
    user_id: str,
    job_id: str,
    job_dir: Path,
    mcp_internal_base_url: str,
    usage_ingest_secret: str,
    generate_timeout_seconds: float,
    mcp_output_dir: Path,
    public_base_url: str,
    mark_ready: Callable[[str, str], None],
    mark_failed: Callable[[str, str], None],
    api_base: str,
    token_cipher_key: bytes,
    device_mac: str,
    device_token_enc: str,
    on_token_minted: Callable[[str], None] | None = None,
) -> tuple[str, VoicePreview]:
    desc = description.strip()
    if not desc:
        raise VoiceJobError("描述不能为空")
    if not usage_ingest_secret:
        raise VoiceJobError("生图通道未配置", status=503)

    try:
        result = request_internal_draw(
            base_url=mcp_internal_base_url,
            secret=usage_ingest_secret,
            description=desc,
            timeout_seconds=generate_timeout_seconds,
        )
        materialize_generated_job(
            base_url=mcp_internal_base_url,
            result=result,
            job_dir=job_dir,
        )
        published = publish_job_files(
            job_dir=job_dir,
            mcp_output_dir=mcp_output_dir,
            public_base_url=public_base_url,
        )
        mark_ready(job_id, published.basename)
    except GenerateError as exc:
        mark_failed(job_id, "GenerateError")
        if exc.status in (400, 429):
            raise VoiceJobError(str(exc), status=exc.status) from exc
        if exc.status == 504:
            raise VoiceJobError("生图超时，请稍后再试", status=504) from exc
        raise VoiceJobError("生图服务暂不可用，请稍后再试", status=502) from exc
    except VoiceJobError:
        raise
    except Exception as exc:
        mark_failed(job_id, type(exc).__name__)
        raise VoiceJobError("生图服务异常", status=502) from exc

    preview = VoicePreview(
        kind="draw",
        job_id=job_id,
        preview_url=published.preview_url,
        gcode_url=published.gcode_url,
    )
    _push_preview(
        preview=preview,
        api_base=api_base,
        token_cipher_key=token_cipher_key,
        device_mac=device_mac,
        device_token_enc=device_token_enc,
        on_token_minted=on_token_minted,
    )
    return (
        "已生成并推到写字机预览，请在小程序点「开始画」确认；取消则点「取消」。",
        preview,
    )


def run_voice_write(
    *,
    text: str,
    user_id: str,
    job_id: str,
    job_dir: Path,
    gfont_dir: str,
    stroke_dir: str,
    mcp_output_dir: Path,
    public_base_url: str,
    mark_ready: Callable[[str, str], None],
    mark_failed: Callable[[str, str], None],
    api_base: str,
    token_cipher_key: bytes,
    device_mac: str,
    device_token_enc: str,
    on_token_minted: Callable[[str], None] | None = None,
) -> tuple[str, VoicePreview]:
    from hutuji_mcp import t2s
    from hutuji_mcp.article_pages import pages_argument
    from hutuji_mcp.write_text import WriteTextError

    if not gfont_dir:
        raise VoiceJobError("写字字库未配置", status=503)
    safe = t2s.to_simplified(text.strip())
    if not safe:
        raise VoiceJobError("文本为空")
    if len(safe) > 5000:
        raise VoiceJobError(f"文本超长：{len(safe)} 字符（上限 5000）")

    layout = resolve_article_layout(size_mm=None, line_spacing=None)
    try:
        result = process_write_text(
            safe,
            job_dir,
            gfont_dir=gfont_dir,
            layout=layout,
            stroke_dir=stroke_dir or "",
            fit_single_page=True,
        )
        published_pages = publish_doc_pages(
            job_dir=job_dir,
            mcp_output_dir=mcp_output_dir,
            public_base_url=public_base_url,
        )
        mark_ready(job_id, published_pages[0].basename)
    except WriteTextError as exc:
        mark_failed(job_id, type(exc).__name__)
        raise VoiceJobError(str(exc)[:120], status=400) from None
    except Exception as exc:
        mark_failed(job_id, type(exc).__name__)
        raise VoiceJobError("写字转换失败", status=422) from exc

    first = published_pages[0]
    urls = [{"url": p.gcode_url, "preview_url": p.preview_url} for p in published_pages]
    pages_arg = pages_argument(urls) if len(urls) > 1 else None
    preview = VoicePreview(
        kind="write",
        job_id=job_id,
        preview_url=first.preview_url,
        gcode_url=first.gcode_url,
        pages_arg=pages_arg,
    )
    _push_preview(
        preview=preview,
        api_base=api_base,
        token_cipher_key=token_cipher_key,
        device_mac=device_mac,
        device_token_enc=device_token_enc,
        on_token_minted=on_token_minted,
    )
    page_note = f"共 {result.page_count} 页。" if result.page_count > 1 else ""
    return (
        f"已排版{page_note}并推到丹青预览，请在小程序点「开始画」确认；取消则点「取消」。",
        preview,
    )


def portal_tool_name(name: str) -> bool:
    return name in ("voice.draw", "voice.write")
