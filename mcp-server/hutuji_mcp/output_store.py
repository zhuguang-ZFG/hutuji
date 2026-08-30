"""绘图产物存储：本地目录（默认）或阿里云 OSS（多 worker 共享）。"""
from __future__ import annotations

import logging
import os
import re
import stat
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterator

from .gcode_gen import GcodeError, validate_gcode

logger = logging.getLogger(__name__)


def _settings():
    from . import server

    return server.settings

_DRAW_FILE_RE = re.compile(
    r"^draw_\d{8}_\d{6}_[A-Za-z0-9_-]{22}\.(?:gcode|svg|png)$"
)
_DRAW_FILE_PARTS_RE = re.compile(
    r"^(draw_\d{8}_\d{6})_(?P<token>[A-Za-z0-9_-]{22})\.(?P<ext>gcode|svg|png)$"
)
_DRAW_SUFFIXES = (".svg", ".gcode", ".png")


class OutputNotFoundError(OSError):
    """产物不存在或不可读。"""


class StaleOutputStoreError(RuntimeError):
    def __init__(self, message: str, mtime: float) -> None:
        super().__init__(message)
        self.mtime = mtime


def _redact_name(name: str) -> str:
    match = _DRAW_FILE_PARTS_RE.fullmatch(name)
    if not match:
        return "*.gcode"
    return f"{match.group(1)}_*.{match.group('ext')}"


def _is_regular_capability_file(path: Path) -> bool:
    try:
        st = os.stat(path, follow_symlinks=False)
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and _DRAW_FILE_RE.fullmatch(path.name) is not None


def _read_regular_file(path: Path) -> tuple[bytes, float]:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    before = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise OSError("目标不是单链接普通文件")
    fd = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise OSError("目标不是单链接普通文件")
        after = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(after.st_mode) or after.st_nlink != 1:
            raise OSError("目标在读取期间被替换")
        if (before.st_dev, before.st_ino) != (st.st_dev, st.st_ino):
            raise OSError("目标在读取期间被替换")
        if (after.st_dev, after.st_ino) != (st.st_dev, st.st_ino):
            raise OSError("目标在读取期间被替换")
        f = os.fdopen(fd, "rb")
        fd = -1
        with f:
            return f.read(), st.st_mtime
    except BaseException:
        if fd >= 0:
            os.close(fd)
        raise


def _is_expired_mtime(mtime: float, *, now: float) -> bool:
    return now - mtime >= _settings().output_ttl_seconds


def _read_verified_gcode_bytes(data: bytes, mtime: float) -> tuple[bytes, float]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StaleOutputStoreError("G-code 无法按 UTF-8 解码", mtime) from exc
    try:
        validate_gcode(text, total_bytes=len(data))
    except GcodeError as exc:
        raise StaleOutputStoreError(str(exc), mtime) from exc
    return data, mtime


@dataclass(frozen=True)
class JobPaths:
    base: str
    svg_path: Path
    gcode_path: Path
    preview_path: Path


class LocalOutputStore:
    @property
    def uses_remote(self) -> bool:
        return False

    def ensure_ready(self) -> None:
        _settings().output_dir.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def job_workspace(self, base: str) -> Iterator[JobPaths]:
        root = _settings().output_dir
        paths = JobPaths(
            base=base,
            svg_path=root / f"{base}.svg",
            gcode_path=root / f"{base}.gcode",
            preview_path=root / f"{base}.png",
        )
        try:
            yield paths
        except BaseException:
            self.abort_job(paths)
            raise

    def finalize_job(self, paths: JobPaths) -> None:
        return

    def abort_job(self, paths: JobPaths) -> None:
        self.delete_stem(paths.base)

    def _read_regular_file(self, path: Path) -> tuple[bytes, float]:
        return _read_regular_file(path)

    def read_verified(self, name: str) -> tuple[bytes, float]:
        path = _settings().output_dir / name
        try:
            data, mtime = self._read_regular_file(path)
        except OSError as exc:
            raise OutputNotFoundError(str(exc)) from exc
        if name.endswith(".gcode"):
            return _read_verified_gcode_bytes(data, mtime)
        return data, mtime

    def delete_stem(self, stem: str) -> None:
        for suffix in _DRAW_SUFFIXES:
            path = _settings().output_dir / f"{stem}{suffix}"
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("删除产物失败 %s：%s", _redact_name(f"{stem}{suffix}"), exc)

    def cleanup_expired(self, *, now: float | None = None) -> int:
        root = _settings().output_dir
        if not root.is_dir():
            return 0
        now = time.time() if now is None else now
        expired_stems: set[str] = set()
        try:
            entries = list(root.iterdir())
        except OSError as exc:
            logger.warning("清理输出目录失败（iterdir）：%s", exc)
            return 0
        for path in entries:
            if _is_regular_capability_file(path):
                try:
                    if _is_expired_mtime(path.stat().st_mtime, now=now):
                        expired_stems.add(path.stem)
                except OSError as exc:
                    logger.warning("跳过清理 %s：%s", _redact_name(path.name), exc)
        deleted = 0
        for stem in expired_stems:
            for suffix in _DRAW_SUFFIXES:
                path = root / f"{stem}{suffix}"
                try:
                    path.unlink()
                    deleted += 1
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    logger.warning("删除过期产物失败 %s：%s", _redact_name(path.name), exc)
        return deleted


class OssOutputStore:
    def __init__(
        self,
        *,
        endpoint: str,
        bucket_name: str,
        access_key_id: str,
        access_key_secret: str,
        prefix: str,
    ) -> None:
        self._endpoint = endpoint
        self._bucket_name = bucket_name
        self._access_key_id = access_key_id
        self._access_key_secret = access_key_secret
        self._prefix = prefix.strip("/")
        self._bucket = None

    @property
    def uses_remote(self) -> bool:
        return True

    def _get_bucket(self):
        if self._bucket is None:
            import oss2

            auth = oss2.Auth(self._access_key_id, self._access_key_secret)
            self._bucket = oss2.Bucket(auth, self._endpoint, self._bucket_name)
        return self._bucket

    def _object_key(self, name: str) -> str:
        if self._prefix:
            return f"{self._prefix}/{name}"
        return name

    def ensure_ready(self) -> None:
        self._get_bucket()

    @contextmanager
    def job_workspace(self, base: str) -> Iterator[JobPaths]:
        with tempfile.TemporaryDirectory(prefix="hutuji-draw-") as tmp:
            root = Path(tmp)
            yield JobPaths(
                base=base,
                svg_path=root / f"{base}.svg",
                gcode_path=root / f"{base}.gcode",
                preview_path=root / f"{base}.png",
            )

    def finalize_job(self, paths: JobPaths) -> None:
        bucket = self._get_bucket()
        created = str(time.time())
        for path in (paths.svg_path, paths.gcode_path, paths.preview_path):
            data = path.read_bytes()
            bucket.put_object(
                self._object_key(path.name),
                data,
                headers={"x-oss-meta-hutuji-created": created},
            )

    def abort_job(self, paths: JobPaths) -> None:
        self.delete_stem(paths.base)

    def _object_mtime(self, headers: dict[str, str]) -> float:
        raw = headers.get("x-oss-meta-hutuji-created")
        if raw:
            try:
                return float(raw)
            except ValueError:
                pass
        last = headers.get("Last-Modified")
        if last:
            try:
                return parsedate_to_datetime(last).timestamp()
            except (TypeError, ValueError, OverflowError):
                pass
        return time.time()

    def read_verified(self, name: str) -> tuple[bytes, float]:
        bucket = self._get_bucket()
        try:
            result = bucket.get_object(self._object_key(name))
        except Exception as exc:
            raise OutputNotFoundError(str(exc)) from exc
        data = result.read()
        headers = getattr(result, "headers", None) or {}
        mtime = self._object_mtime(dict(headers))
        if name.endswith(".gcode"):
            return _read_verified_gcode_bytes(data, mtime)
        return data, mtime

    def delete_stem(self, stem: str) -> None:
        bucket = self._get_bucket()
        for suffix in _DRAW_SUFFIXES:
            key = self._object_key(f"{stem}{suffix}")
            try:
                bucket.delete_object(key)
            except Exception as exc:
                logger.warning("OSS 删除失败 %s：%s", _redact_name(f"{stem}{suffix}"), exc)

    def cleanup_expired(self, *, now: float | None = None) -> int:
        bucket = self._get_bucket()
        now = time.time() if now is None else now
        deleted = 0
        prefix = f"{self._prefix}/" if self._prefix else ""
        marker = ""
        while True:
            result = bucket.list_objects_v2(prefix=prefix, marker=marker, max_keys=200)
            for obj in result.object_list:
                name = obj.key.rsplit("/", 1)[-1]
                if _DRAW_FILE_RE.fullmatch(name) is None:
                    continue
                mtime = obj.last_modified
                if isinstance(mtime, int):
                    ts = float(mtime)
                else:
                    ts = mtime.timestamp()
                if _is_expired_mtime(ts, now=now):
                    try:
                        bucket.delete_object(obj.key)
                        deleted += 1
                    except Exception as exc:
                        logger.warning("OSS 清理失败 %s：%s", _redact_name(name), exc)
            if not result.is_truncated:
                break
            marker = result.next_marker
        return deleted


_store: LocalOutputStore | OssOutputStore | None = None


def get_output_store() -> LocalOutputStore | OssOutputStore:
    global _store
    if _store is not None:
        return _store
    backend = getattr(_settings(), "output_backend", "local").strip().lower()
    if backend == "oss":
        cfg = _settings()
        _store = OssOutputStore(
            endpoint=cfg.oss_endpoint,
            bucket_name=cfg.oss_bucket,
            access_key_id=cfg.oss_access_key_id,
            access_key_secret=cfg.oss_access_key_secret,
            prefix=getattr(cfg, "oss_prefix", "draw"),
        )
    else:
        _store = LocalOutputStore()
    return _store


def reset_output_store_for_tests() -> None:
    global _store
    _store = None
