"""精品图库只读目录、完整性校验与按设备纸张转换。"""
from __future__ import annotations

from collections import Counter, OrderedDict
from concurrent.futures import Future
from dataclasses import dataclass
import io
import json
import logging
from pathlib import Path
import threading
import time
from types import MappingProxyType
from typing import Mapping

from hutuji_mcp.llm_svg import LlmError
from hutuji_mcp.output_store import StaleOutputStoreError

from hutuji_mcp.market_schema import (
    ASSET_FIELDS, ASSET_LIMITS, CATEGORIES, LICENSE_WHITELIST, MIN_CATEGORY_ITEMS,
    REQUIRE_ATTRIBUTION, read_asset, validate_entry,
)

logger = logging.getLogger(__name__)
PAGE_SIZE = 30


class MarketBusyError(RuntimeError):
    """转换名额已满；无需付费生成，稍后可以重试。"""


@dataclass(frozen=True)
class MarketItem:
    record: Mapping

    @property
    def id(self):
        return self.record["id"]

    @property
    def title(self):
        return self.record["title"]

    @property
    def category(self):
        return self.record["category"]

    def to_card(self) -> dict:
        return {"id": self.id, "title": self.title, "category": self.category,
                "license": self.record["license"],
                "revision": self.record["svg_sha256"],
                "preview_url": f"/api/market/item/{self.id}/preview.png?v={self.record['preview_sha256']}"}

    def attribution(self) -> dict:
        return {key: self.record[key] for key in ("license", "attribution", "source_url", "changes")}

    def to_detail(self) -> dict:
        return {**self.to_card(), **self.attribution()}


class MarketCatalog:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.RLock()
        self._fingerprint = None
        self._snapshot: tuple[dict, dict] = ({}, {})
        self._cache: OrderedDict = OrderedDict()
        self._inflight: dict[tuple, Future] = {}
        # SVG/vpype 是 CPU 工作；最多两件并行，同配置搭车，控制请求不占此名额。
        self._slots = threading.BoundedSemaphore(2)

    def _load_locked(self) -> None:
        with self._lock:
            raw = read_asset(self.root, "manifest.json", max_bytes=4 * 1024 * 1024)
            manifest = json.loads(raw)
            if (not isinstance(manifest, dict) or type(manifest.get("version")) is not int or
                    manifest["version"] != 1 or not isinstance(manifest.get("items"), list) or
                    len(manifest["items"]) > 2000):
                raise ValueError("图库清单版本或条目列表无效")
            entries = manifest["items"]
            ids = Counter(e.get("id") for e in entries if isinstance(e, dict) and isinstance(e.get("id"), str))
            items: dict[str, MarketItem] = {}
            by_category: dict[str, list[str]] = {}
            for entry in entries:
                try:
                    entry = validate_entry(entry)
                    if ids[entry["id"]] != 1 or not entry["reviewed"]:
                        continue
                    item = MarketItem(MappingProxyType(entry))
                    self._verify_item(item)
                except (OSError, ValueError, TypeError, KeyError, LlmError, StaleOutputStoreError):
                    # 不把清单提供的任意文本/路径写进运行日志。
                    logger.warning("精品图库条目未通过来源或文件校验，已隐藏")
                    continue
                items[item.id] = item
                by_category.setdefault(item.category, []).append(item.id)
            by_category = {name: by_category[name] for name in CATEGORIES
                           if len(by_category.get(name, ())) >= MIN_CATEGORY_ITEMS}
            items = {key: item for key, item in items.items() if item.category in by_category}
            # 两个索引一次发布；分类切换与热更新不能读到不同版本。
            self._snapshot = items, by_category

    def _verify_item(self, item: MarketItem) -> None:
        from PIL import Image
        from hutuji_mcp.llm_svg import _validate_svg
        from hutuji_mcp.output_store import _read_verified_gcode_bytes

        svg = self.read_item(item, "svg")
        if len(svg) != item.record["bytes"]:
            raise ValueError("图库 SVG 字节数不一致")
        _validate_svg(svg.decode("utf-8"))
        _read_verified_gcode_bytes(self.read_item(item, "gcode"), 0)
        with Image.open(io.BytesIO(self.read_item(item, "preview"))) as img:
            if img.format != "PNG" or not 0 < max(img.size) <= 2048:
                raise ValueError("图库预览格式或像素预算无效")
            img.verify()
        if not self.read_item(item, "license").strip():
            raise ValueError("图库许可记录为空")

    def _ensure_fresh(self) -> tuple[dict, dict]:
        with self._lock:
            try:
                info = (self.root / "manifest.json").lstat()
                fingerprint = info.st_mtime_ns, info.st_size, info.st_ino
                if fingerprint != self._fingerprint:
                    self._load_locked()
                    self._fingerprint = fingerprint
            except (OSError, ValueError, TypeError):
                # 部署中的半份清单不能覆盖已经完整验证的快照；文件仍逐次验 hash。
                logger.warning("精品图库清单未就绪，保留已验证快照")
            return self._snapshot

    def get(self, item_id: str) -> MarketItem | None:
        return self._ensure_fresh()[0].get(item_id)

    def categories(self) -> dict[str, int]:
        return {name: len(ids) for name, ids in self._ensure_fresh()[1].items()}

    def list_page(self, category: str, cursor: int) -> tuple[list[MarketItem], int | None]:
        items, by_category = self._ensure_fresh()
        ids = by_category.get(category, ())
        cursor = max(0, cursor)
        page = [items[key] for key in ids[cursor:cursor + PAGE_SIZE]]
        return page, cursor + PAGE_SIZE if cursor + PAGE_SIZE < len(ids) else None

    def read_item(self, item: MarketItem, which: str) -> bytes:
        return read_asset(self.root, item.record[ASSET_FIELDS[which]], max_bytes=ASSET_LIMITS[which],
                          digest=item.record[which + "_sha256"])

    def materialize(self, item: MarketItem, job_dir: Path, paper) -> None:
        from .pipeline import process_upload_svg
        from hutuji_mcp.paper_artifacts import write_paper_metadata

        source = self.read_item(item, "svg")
        key = ("market", item.record["svg_sha256"], paper.marker)
        owner = False
        with self._lock:
            cached = self._cache.get(key)
            if cached and time.monotonic() - cached[0] < 600:
                self._cache.move_to_end(key)
                bundle = cached[1]
                pending = None
            else:
                pending = self._inflight.get(key)
                if pending is None:
                    if not self._slots.acquire(blocking=False):
                        raise MarketBusyError("图库正在准备其他作品，请稍后重试")
                    pending = self._inflight[key] = Future()
                    owner = True
        if owner:
            try:
                process_upload_svg(source, job_dir, profile=paper.to_profile())
                bundle = tuple((job_dir / name).read_bytes() for name in ("draw.svg", "draw.gcode", "preview.png"))
                with self._lock:
                    self._cache[key] = time.monotonic(), bundle
                    self._cache.move_to_end(key)
                    while len(self._cache) > 16:
                        self._cache.popitem(last=False)
                pending.set_result(bundle)
            except BaseException as exc:
                pending.set_exception(exc)
                raise
            finally:
                with self._lock:
                    self._inflight.pop(key, None)
                    self._slots.release()
        elif pending is not None:
            bundle = pending.result(timeout=90)
        job_dir.mkdir(parents=True, exist_ok=True)
        for name, raw in zip(("draw.svg", "draw.gcode", "preview.png"), bundle):
            (job_dir / name).write_bytes(raw)
        write_paper_metadata(job_dir / "draw.gcode", paper, data=bundle[1])


_catalog: MarketCatalog | None = None
_catalog_lock = threading.Lock()


def get_catalog(root: Path) -> MarketCatalog:
    global _catalog
    with _catalog_lock:
        if _catalog is None or _catalog.root != root:
            catalog = MarketCatalog(root)
            catalog._load_locked()
            _catalog = catalog
        return _catalog
