"""精品图库入库与运行时共用的许可、来源和文件边界。"""
from __future__ import annotations

import hashlib
import math
import re
import stat
from datetime import date
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

LICENSE_WHITELIST = frozenset({"CC0-1.0", "Apache-2.0", "MIT", "CC-BY-4.0"})
REQUIRE_ATTRIBUTION = frozenset({"CC-BY-4.0"})
CATEGORIES = (
    "动物", "鸟类", "昆虫", "水生", "植物", "花卉", "食物", "水果", "蔬菜", "交通工具",
    "建筑", "日常小物", "文具", "玩具", "运动", "乐器", "节日", "字母数字", "几何装饰", "表情",
)
MIN_CATEGORY_ITEMS = 20
ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,79}")
HASH_RE = re.compile(r"[a-f0-9]{64}")
ASSET_LIMITS = {"svg": 256 * 1024, "gcode": 512 * 1024, "preview": 1024 * 1024, "license": 256 * 1024}
ASSET_FIELDS = {"svg": "path", "gcode": "gcode_path", "preview": "preview_path", "license": "license_path"}


def text_field(value: object, limit: int, *, required: bool = True) -> str:
    if (not isinstance(value, str) or len(value) > limit or
            (required and not value.strip()) or value != value.strip() or
            any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError("图库文本字段无效")
    return value


def relative_path(value: object) -> str:
    value = text_field(value, 240)
    parts = PurePosixPath(value).parts
    if (not parts or PurePosixPath(value).is_absolute() or "\\" in value or ":" in value or
            any(p in (".", "..") for p in value.split("/")) or "//" in value):
        raise ValueError("图库路径必须在数据目录内")
    return value


def validate_entry(value: object) -> dict:
    from .curated_gate import GATE_VERSION

    if not isinstance(value, dict):
        raise ValueError("图库条目必须是对象")
    entry = dict(value)
    for name, limit in (("id", 80), ("title", 60), ("category", 20), ("source", 80),
                        ("source_url", 2048), ("source_revision", 160), ("viewBox", 100)):
        text_field(entry.get(name), limit)
    if not ID_RE.fullmatch(entry["id"]) or not ID_RE.fullmatch(entry["source"]):
        raise ValueError("图库标识无效")
    if entry["category"] not in CATEGORIES or entry.get("license") not in LICENSE_WHITELIST:
        raise ValueError("图库类目或许可不允许")
    text_field(entry.get("attribution", ""), 1024, required=entry["license"] in REQUIRE_ATTRIBUTION)
    text_field(entry.get("changes"), 240)
    url = urlsplit(entry["source_url"])
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment:
        raise ValueError("图库来源须为明确的 HTTPS 地址")
    if entry.get("gate_version") != GATE_VERSION:
        raise ValueError("图库入库闸版本无效")
    if type(entry.get("reviewed")) is not bool:
        raise ValueError("图库缺少画质与展示复核记录")
    date.fromisoformat(text_field(entry.get("ingested_at"), 10))
    score = entry.get("quality_score")
    if type(score) not in (int, float) or not 0 <= score <= 1 or not math.isfinite(score):
        raise ValueError("图库质量分无效")
    if type(entry.get("bytes")) is not int or not 0 < entry["bytes"] <= ASSET_LIMITS["svg"]:
        raise ValueError("图库 SVG 大小无效")
    for kind, field in ASSET_FIELDS.items():
        relative_path(entry.get(field))
        if not isinstance(entry.get(kind + "_sha256"), str) or not HASH_RE.fullmatch(entry[kind + "_sha256"]):
            raise ValueError("图库文件缺少内容指纹")
    return entry


def read_asset(root: Path, rel: str, *, max_bytes: int, digest: str | None = None) -> bytes:
    from .output_store import _read_regular_file

    relative_path(rel)
    # 不只检查末级文件：目录链接也能逃出素材根，读取前后均核对祖先身份。
    current = root.absolute()
    parents = [current]
    for part in PurePosixPath(rel).parts[:-1]:
        current = current / part
        parents.append(current)
    identities = []
    for parent in parents:
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or parent.is_symlink() or getattr(parent, "is_junction", lambda: False)():
            raise OSError("图库目录不是普通目录")
        identities.append((info.st_dev, info.st_ino))
    raw, _ = _read_regular_file(root / rel, max_bytes=max_bytes)
    for parent, identity in zip(parents, identities):
        info = parent.lstat()
        if parent.is_symlink() or getattr(parent, "is_junction", lambda: False)() or (info.st_dev, info.st_ino) != identity:
            raise OSError("图库目录在读取期间变化")
    if digest is not None and hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("图库文件与入库记录不一致")
    return raw
