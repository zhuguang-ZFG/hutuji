"""网页文字生图：调本机 MCP /internal/draw 并把产物物化进 portal job 目录。

设计要点（2026-08-31）：
- portal 自己不持有生图 API key（钥匙只在 MCP 侧 env），生图统一走 MCP
  新增的 loopback+共享密钥内部端点，产出与语音链路同一条（限幅/校验/纸面闸）。
- MCP 返回的 url 是公网 capability；portal 只取文件名，从 loopback 基址
  重新拼装下载，不经公网回绕。
- 物化后的 job 产物与上传流完全一致（draw.gcode + preview.png），
  后续 publish/send/save 复用既有路径，推送由买家在页面显式触发。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# 与 mcp-server hutuji_mcp.server._DRAW_FILE_RE 同形；不 import hutuji_mcp，
# 避免拉起 MCP 配置/日志副作用（portal 是独立部署单元）。
_DRAW_FILE_RE = re.compile(r"^draw_\d{8}_\d{6}_[A-Za-z0-9_-]{22}\.(?:gcode|png)$")
# 对齐 protocol §5：单 G-code ≤512KiB；PNG 预览给 4MiB 富余。
_MAX_GCODE_BYTES = 512 * 1024
_MAX_PNG_BYTES = 4 * 1024 * 1024
# 读缓冲上限：比上限多 1 字节即可判超限，避免无限读。
_READ_CHUNK = 64 * 1024


class GenerateError(Exception):
    """网页生图失败；status 直接映射为 portal 端点回码。"""

    def __init__(self, message: str, *, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class GeneratedDraw:
    lines: int
    bytes: int
    pen_lifts: int


def request_internal_draw(
    *,
    base_url: str,
    secret: str,
    description: str,
    timeout_seconds: int,
    machine: str = "",
    paper: dict | None = None,
    device_id: str = "",
) -> dict:
    """调 MCP /internal/draw，返回解析后的结果 dict。

    错误映射与 MCP 侧约定一致：400 输入白话 / 429 限流 / 504 超时 /
    502 生图侧失败；其余（含网络）一律塌成 502，不回显内部细节。
    machine 空串 = MCP 侧按设备 status 自行探测；显式机型优先于探测
    （portal 已在本地探过一次，不再让 MCP 打第二枪）。
    """
    payload = {"description": description}
    if machine:
        payload["machine"] = machine
    if paper is not None:
        from hutuji_mcp.paper_config import PaperConfig

        payload["paper"] = PaperConfig.from_dict(paper).to_dict()
    if device_id:
        payload["device_id"] = device_id
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(
        f"{base_url}/internal/draw",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Hutuji-Usage-Secret": secret,
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout_seconds) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail", "")
        except Exception:
            detail = ""
        status = exc.code if exc.code in {400, 429, 504} else 502
        raise GenerateError(detail or "生图服务暂不可用", status=status) from None
    except URLError as exc:
        raise GenerateError(
            f"生图服务连接失败（{type(exc.reason).__name__}）", status=502
        ) from None
    except (TimeoutError, OSError) as exc:
        raise GenerateError("生图服务响应超时", status=504) from exc
    if not isinstance(payload, dict) or not payload.get("url"):
        raise GenerateError("生图服务回包异常", status=502)
    return payload


def _name_from_capability(url: object) -> str:
    """从 capability URL 提取并校验文件名；任何偏差都是异常回包，拒收。"""
    if not isinstance(url, str):
        raise GenerateError("生图服务回包缺少 url", status=502)
    name = url.rsplit("/", 1)[-1]
    if not _DRAW_FILE_RE.fullmatch(name):
        raise GenerateError("生图服务回包文件名不合契约", status=502)
    return name


def _download_limited(url: str, dest: Path, *, max_bytes: int, timeout_seconds: int) -> str:
    # /files 是匿名能力 URL（secret 只在 /internal/draw 用），这里无需带密钥。
    req = Request(url, method="GET")
    try:
        with urlopen(req, timeout=timeout_seconds) as resp:
            length = resp.headers.get("Content-Length")
            if length is not None and int(length) > max_bytes:
                raise GenerateError("生图产物超限", status=502)
            written = 0
            with dest.open("wb") as fh:
                while True:
                    chunk = resp.read(_READ_CHUNK)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        raise GenerateError("生图产物超限", status=502)
                    fh.write(chunk)
            return resp.headers.get("X-Hutuji-Paper", "")
    except GenerateError:
        raise
    except HTTPError as exc:
        raise GenerateError(f"产物下载失败（HTTP {exc.code}）", status=502) from None
    except URLError as exc:
        raise GenerateError(
            f"产物下载连接失败（{type(exc.reason).__name__}）", status=502
        ) from None
    except (ValueError, TimeoutError, OSError):
        raise GenerateError("产物下载失败，请稍后再试", status=502) from None


def materialize_generated_job(
    *,
    base_url: str,
    result: dict,
    job_dir: Path,
    timeout_seconds: int = 30,
) -> GeneratedDraw:
    """把 MCP 产物下载进 job 目录（draw.gcode + preview.png）。"""
    gcode_name = _name_from_capability(result.get("url"))
    png_name = _name_from_capability(result.get("preview_url"))
    job_dir.mkdir(parents=True, exist_ok=True)
    gcode_dest = job_dir / "draw.gcode"
    png_dest = job_dir / "preview.png"
    from hutuji_mcp.gcode_gen import GcodeError, validate_gcode
    from hutuji_mcp.paper_artifacts import paper_path, write_paper_metadata
    from hutuji_mcp.output_store import _read_verified_gcode_bytes
    from .paper_settings import read_job_context

    try:
        marker = _download_limited(
            f"{base_url}/files/{gcode_name}", gcode_dest,
            max_bytes=_MAX_GCODE_BYTES, timeout_seconds=timeout_seconds,
        )
        _download_limited(
            f"{base_url}/files/{png_name}", png_dest,
            max_bytes=_MAX_PNG_BYTES, timeout_seconds=timeout_seconds,
        )
        snapshot = read_job_context(job_dir)
        raw = gcode_dest.read_bytes()
        if snapshot is not None:
            if marker != snapshot.paper.marker or result.get("paper_marker") != snapshot.paper.marker:
                raise GenerateError("生成结果与纸张设置不一致，请重新生成")
            try:
                stats = validate_gcode(raw.decode("utf-8"), total_bytes=len(raw), profile=snapshot.paper.to_profile())
            except (GcodeError, UnicodeError):
                raise GenerateError("生成结果未通过纸张校验") from None
            write_paper_metadata(gcode_dest, snapshot.paper, data=raw)
        else:
            # 兼容旧调用只放行既有固定档；不能凭回包统计信任大幅产物。
            _read_verified_gcode_bytes(raw, 0)
            from hutuji_mcp.gcode_gen import PROFILE_NOPAPER

            try:
                stats = validate_gcode(raw.decode("utf-8"), total_bytes=len(raw))
            except GcodeError:
                stats = validate_gcode(raw.decode("utf-8"), total_bytes=len(raw), profile=PROFILE_NOPAPER)
    except Exception:
        # 半成品不得留在 job 目录：publish 会按「产物不完整」再炸一次，但
        # 失败 job 目录应干净，便于排查（也避免残留大文件）。
        for path in (gcode_dest, png_dest, paper_path(gcode_dest)):
            path.unlink(missing_ok=True)
        raise
    return GeneratedDraw(
        lines=stats.lines,
        bytes=stats.bytes,
        pen_lifts=stats.pen_lifts,
    )
