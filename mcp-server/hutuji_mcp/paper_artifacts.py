"""G-code 纸张快照与内容绑定；伴随文件不公开下载。"""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

from .paper_config import PaperConfig

PAPER_SUFFIX = ".paper.json"
PAPER_HEADER = "X-Hutuji-Paper"


class PaperArtifactError(ValueError):
    """产物配置损坏或不属于当前 G-code。"""


def paper_path(gcode_path: Path) -> Path:
    return gcode_path.with_suffix(PAPER_SUFFIX)


def encode_paper_metadata(config: PaperConfig, data: bytes) -> bytes:
    return json.dumps({"version": 1, "paper": config.to_dict(), "sha256": hashlib.sha256(data).hexdigest()},
                      ensure_ascii=True, separators=(",", ":")).encode("ascii")


def decode_paper_metadata(raw: bytes, data: bytes) -> PaperConfig:
    try:
        if len(raw) > 2048:
            raise ValueError("too large")
        value = json.loads(raw)
        if (not isinstance(value, dict) or set(value) != {"version", "paper", "sha256"} or
                type(value["version"]) is not int or value["version"] != 1 or
                not isinstance(value["sha256"], str) or
                not hmac.compare_digest(value["sha256"], hashlib.sha256(data).hexdigest())):
            raise ValueError("invalid metadata")
        return PaperConfig.from_dict(value["paper"])
    except (ValueError, TypeError, UnicodeError) as exc:
        raise PaperArtifactError("作品纸张配置损坏或内容不匹配，请重新生成") from exc


def write_paper_metadata(gcode_path: Path, config: PaperConfig | None, *, data: bytes | None = None) -> None:
    path = paper_path(gcode_path)
    if config is None:
        path.unlink(missing_ok=True)
        return
    path.write_bytes(encode_paper_metadata(config, data if data is not None else gcode_path.read_bytes()))


def read_paper_metadata(gcode_path: Path, *, data: bytes | None = None) -> PaperConfig | None:
    # 与 /files 共用普通文件、硬链接与读取竞态闸，不能另开一个宽松读取路径。
    from .output_store import _read_regular_file

    try:
        raw, _ = _read_regular_file(paper_path(gcode_path), max_bytes=2048)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PaperArtifactError("作品纸张配置不可读，请重新生成") from exc
    if data is None:
        data, _ = _read_regular_file(gcode_path)
    return decode_paper_metadata(raw, data)
