"""output_store 本地后端与 device 限流契约。"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from hutuji_mcp import draw_queue, server
from hutuji_mcp.config import Settings, load_settings
from hutuji_mcp.gcode_gen import validate_gcode
from hutuji_mcp.output_store import LocalOutputStore, get_output_store, reset_output_store_for_tests


@pytest.fixture
def memory_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    base = load_settings()
    patched = Settings(
        **{
            **base.__dict__,
            "draw_queue_url": "memory://",
            "draw_queue_max_depth": 50,
            "draw_enqueue_per_second": 100,
        }
    )
    monkeypatch.setattr("hutuji_mcp.config.settings", patched)
    monkeypatch.setattr("hutuji_mcp.draw_queue.settings", patched)
    draw_queue.reset_backend_for_tests()


def _minimal_gcode() -> bytes:
    text = "\n".join(
        [
            "G21",
            "G90",
            "F24000",
            "G1G90 Z0.0F10000",
            "G1G90 Z0.0F10000",
            "G0 X10 Y10 F24000",
            "G1G90 Z5.0F10000",
            "G1G90 X20 Y20 F24000",
            "G1G90 Z0.0F10000",
        ]
    )
    validate_gcode(text, total_bytes=len(text.encode("ascii")))
    return text.encode("ascii")


def test_local_store_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    base = load_settings()
    patched = Settings(
        **{
            **base.__dict__,
            "output_dir": tmp_path,
            "output_backend": "local",
        }
    )
    monkeypatch.setattr(server, "settings", patched)
    reset_output_store_for_tests()
    store = get_output_store()
    assert isinstance(store, LocalOutputStore)
    token = "A" * 22
    stem = f"draw_20260830_120000_{token}"
    with store.job_workspace(stem) as paths:
        paths.gcode_path.write_bytes(_minimal_gcode())
        paths.svg_path.write_text("<svg></svg>", encoding="utf-8")
        paths.preview_path.write_bytes(b"png")
        store.finalize_job(paths)
        name = paths.gcode_path.name
    data, _mtime = store.read_verified(name)
    assert data.startswith(b"G21")


def test_local_store_finalize_created_at_shifts_mtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """错峰 TTL（protocol §4.2）：finalize_job(created_at=…) 必须把三个产物的
    mtime 都设成给定时刻——TTL 判定链（_serve_file/_is_expired_mtime/cleanup）
    只读 mtime，漏掉任一后缀会让该页的 svg/png 与 gcode 窗口不一致。
    created_at=None 保持旧语义（不动时间戳）。"""
    base_settings = load_settings()
    patched = Settings(
        **{**base_settings.__dict__, "output_dir": tmp_path, "output_backend": "local"}
    )
    monkeypatch.setattr(server, "settings", patched)
    reset_output_store_for_tests()
    store = get_output_store()
    token = "B" * 22
    future = time.time() + 1200.0
    with store.job_workspace(f"draw_20260830_120000_{token}") as paths:
        paths.gcode_path.write_bytes(_minimal_gcode())
        paths.svg_path.write_text("<svg></svg>", encoding="utf-8")
        paths.preview_path.write_bytes(b"png")
        store.finalize_job(paths, created_at=future)
        shifted = [p.stat().st_mtime for p in (paths.svg_path, paths.gcode_path, paths.preview_path)]
    for mtime in shifted:
        assert abs(mtime - future) < 1.0

    with store.job_workspace(f"draw_20260830_120001_{token}") as paths:
        paths.gcode_path.write_bytes(_minimal_gcode())
        paths.svg_path.write_text("<svg></svg>", encoding="utf-8")
        paths.preview_path.write_bytes(b"png")
        store.finalize_job(paths)
        assert paths.gcode_path.stat().st_mtime < future - 600.0


def test_read_verified_accepts_nopaper_profile_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """§5 服务时复核必须按机型分档放行（§10.4.15）。

    2026-09-11 生产事故：nopaper 档产物（Y 可到 290）落盘合法，读闸却只跑
    默认档（Y≤190）——合法产物被当 stale 删掉、设备下载必 404，语音画图
    「文件已过期或不存在」全灭。读时无法知道产物出自哪台机器，故两档任一
    成立即放行；两档都不成立（盘上被改写/常量收紧旧产物）仍拒。
    """
    base = load_settings()
    patched = Settings(
        **{**base.__dict__, "output_dir": tmp_path, "output_backend": "local"}
    )
    monkeypatch.setattr(server, "settings", patched)
    reset_output_store_for_tests()
    store = get_output_store()
    token = "C" * 22
    stem = f"draw_20260911_204245_{token}"
    nopaper_body = "\n".join(
        [
            "G21",
            "G90",
            "F24000",
            "G1G90 Z0.0F10000",
            "G1G90 Z0.0F10000",
            "G0 X10 Y10 F24000",
            "G1G90 Z5.0F10000",
            "G1G90 X100 Y250 F24000",  # Y=250：默认档越界、nopaper 档合法
            "G1G90 Z0.0F10000",
        ]
    ) + "\n"
    with store.job_workspace(stem) as paths:
        paths.gcode_path.write_bytes(nopaper_body.encode("utf-8"))
        paths.svg_path.write_text("<svg></svg>", encoding="utf-8")
        paths.preview_path.write_bytes(b"png")
        store.finalize_job(paths)
        name = paths.gcode_path.name
    data, _mtime = store.read_verified(name)  # 不抛 = 放行
    assert b"Y250" in data

    # 两档都不成立仍拒（盘上被改写的旧口径产物，如 Y=300 超出任何机器）
    over = nopaper_body.replace("Y250", "Y300")
    name_over = stem.replace(token, "C" * 22) + ".gcode"
    (tmp_path / name_over).write_bytes(over.encode("utf-8"))
    with pytest.raises(Exception) as exc_info:
        store.read_verified(name_over)
    assert "Y300" in str(exc_info.value)


class _FakeObj:
    def __init__(self, key: str, last_modified: float) -> None:
        self.key = key
        self.last_modified = int(last_modified)


class _FakeListing:
    def __init__(self, objects: list[_FakeObj]) -> None:
        self.object_list = objects
        self.is_truncated = False
        self.next_marker = ""


class _FakeHead:
    def __init__(self, created: str | None) -> None:
        self.headers = {} if created is None else {"x-oss-meta-hutuji-created": created}


class _FakeBucket:
    def __init__(self, objects: dict[str, tuple[float, str | None]]) -> None:
        self._objects = objects
        self.deleted: list[str] = []
        self.head_calls: list[str] = []

    def list_objects_v2(self, *, prefix, marker, max_keys):
        return _FakeListing([_FakeObj(k, v[0]) for k, v in self._objects.items()])

    def head_object(self, key: str) -> _FakeHead:
        self.head_calls.append(key)
        return _FakeHead(self._objects[key][1])

    def delete_object(self, key: str) -> None:
        self.deleted.append(key)


def test_oss_cleanup_honours_created_meta(monkeypatch: pytest.MonkeyPatch):
    """OSS 清理必须按 x-oss-meta-hutuji-created 判过期，不能只看 last_modified。

    错峰 TTL 把靠后页的 created 前移到未来，但 OSS 的 last_modified 恒是上传时刻；
    只看 last_modified 会在第 2 页还没被设备下载时就删掉它（多页文章必挂）。
    head 只对「按 last_modified 看已过期」的对象补发，未过期对象不额外请求。
    """
    from hutuji_mcp.output_store import OssOutputStore

    token = "C" * 22
    now = 10_000.0
    stale = f"draw/draw_20260830_120000_{token}.gcode"
    staggered = f"draw/draw_20260830_120001_{token}.gcode"
    fresh = f"draw/draw_20260830_120002_{token}.gcode"
    bucket = _FakeBucket(
        {
            stale: (now - 700.0, str(now - 700.0)),      # 上传即过期
            staggered: (now - 700.0, str(now + 500.0)),  # 上传早，窗口被前移到未来
            fresh: (now - 10.0, str(now - 10.0)),        # 明显未过期
        }
    )
    store = OssOutputStore(
        endpoint="https://oss.invalid", bucket_name="b", access_key_id="k",
        access_key_secret="s", prefix="draw",
    )
    monkeypatch.setattr(store, "_get_bucket", lambda: bucket)

    deleted = store.cleanup_expired(now=now)
    assert deleted == 1
    assert bucket.deleted == [stale]
    assert bucket.head_calls == [stale, staggered]  # fresh 不多花一次 head


def test_device_rate_limit(memory_queue, monkeypatch):
    base = load_settings()
    patched = Settings(
        **{
            **base.__dict__,
            "draw_queue_url": "memory://",
            "draw_queue_max_depth": 50,
            "draw_enqueue_per_second": 100,
            "draw_per_device_minute": 2,
        }
    )
    monkeypatch.setattr("hutuji_mcp.config.settings", patched)
    monkeypatch.setattr("hutuji_mcp.draw_queue.settings", patched)
    draw_queue.reset_backend_for_tests()

    async def run_case() -> None:
        await draw_queue.attach_or_enqueue("a", "https://hutuji.example", device_id="dev1")
        await draw_queue.attach_or_enqueue("b", "https://hutuji.example", device_id="dev1")
        with pytest.raises(draw_queue.DrawQueueBusyError):
            await draw_queue.attach_or_enqueue("c", "https://hutuji.example", device_id="dev1")

    asyncio.run(run_case())
