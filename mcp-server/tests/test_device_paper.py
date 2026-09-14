"""设备目标、异步事务、内容缓存和公开文件的纸张契约。"""
import asyncio
from dataclasses import replace
import io
import json
from types import SimpleNamespace
import zlib

import pytest
from fastapi import HTTPException

from hutuji_mcp import draw_queue, server
from hutuji_mcp.config import load_settings
from hutuji_mcp.device_paper import DevicePaperError, DrawTarget, _portal_base
from hutuji_mcp.paper_config import PaperConfig
from hutuji_mcp.paper_artifacts import paper_path, read_paper_metadata, write_paper_metadata
from hutuji_mcp.output_store import LocalOutputStore, OssOutputStore, OutputNotFoundError, StaleOutputStoreError
from test_output_store import _minimal_gcode


def _large():
    return PaperConfig("A2", 420, 594, False, 10, False, 400, 574)


@pytest.mark.parametrize("patch", [
    {"chain_push": "false"}, {"machine": []}, {"description": None},
    {"owner_stamp": "a" * 64, "paper": None}, {"paper": {}, "chain_push": False},
    {"paper": _large().to_dict(), "owner_stamp": ""},
])
def test_bad_queue_snapshot_never_falls_back_to_legacy_push(patch):
    payload = dict(job_id="job", description="猫", public_base_url="https://example.test",
                   chain_push=True, machine="nopaper", paper=_large().to_dict(),
                   device_id="aa:bb:cc:dd:ee:ff", owner_stamp="a" * 64)
    with pytest.raises(ValueError):
        draw_queue.DrawJobPayload.from_dict(dict(payload, **patch))


def test_legacy_queue_job_is_rejected_before_generation_in_device_mode(monkeypatch):
    monkeypatch.setattr(server, "settings", SimpleNamespace(usage_ingest_secret="test-only"))
    calls = []
    async def profile(machine):
        calls.append(machine)
        raise AssertionError("未核对目标前不能进入生成链")
    monkeypatch.setattr(server, "_resolve_plotter_profile", profile)
    with pytest.raises(DevicePaperError, match="缺少当前设备"):
        asyncio.run(server._run_draw_job("猫", "https://example.test"))
    assert calls == []


def test_serve_dynamic_file_keeps_marker_crc_and_content(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "settings", replace(load_settings(), output_dir=tmp_path, output_backend="local"))
    monkeypatch.setattr("hutuji_mcp.output_store._store", LocalOutputStore())
    path = tmp_path / (server._new_basename() + ".gcode")
    raw = _minimal_gcode().replace(b"X20 Y20", b"X350 Y550")
    path.write_bytes(raw)
    paper = _large()
    write_paper_metadata(path, paper)
    response = asyncio.run(server._serve_file(path.name))
    assert response.body == raw
    assert response.headers["x-hutuji-paper"] == paper.marker
    assert response.headers["x-hutuji-crc32"] == f"{zlib.crc32(raw):08x}"
    paper_path(path).unlink()
    with pytest.raises(HTTPException) as caught:
        asyncio.run(server._serve_file(path.name))
    assert caught.value.status_code == 404


def test_real_vpype_cache_restores_metadata_and_separates_paper(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_gcode_cache", server.OrderedDict())
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><path d="M10 10L90 90" fill="none" stroke="black"/></svg>'
    src = tmp_path / "test.svg"
    src.write_text(svg, encoding="utf-8")
    large = _large()
    small = PaperConfig.default_for("nopaper")
    async def run():
        for name, paper in [("one", large), ("two", large), ("small", small)]:
            dest = tmp_path / (name + ".gcode")
            await server._svg_to_gcode_cached(svg, src, dest, profile=paper.to_profile())
            assert read_paper_metadata(dest) == paper
        assert len(server._gcode_cache) == 2
    asyncio.run(run())
    assert (tmp_path / "one.gcode").read_bytes() == (tmp_path / "two.gcode").read_bytes()
    assert (tmp_path / "one.gcode").read_bytes() != (tmp_path / "small.gcode").read_bytes()


def test_memory_queue_isolates_device_binding_and_paper(monkeypatch):
    monkeypatch.setattr(draw_queue, "settings", replace(load_settings(), draw_enqueue_per_second=100,
        draw_per_device_minute=60, draw_per_device_day=100))
    backend = draw_queue.MemoryDrawQueueBackend()
    async def run():
        common = dict(device_id="aa:bb:cc:dd:ee:ff", paper=_large(), owner_stamp="a" * 64, machine="nopaper")
        first = await backend.attach_or_enqueue("猫", "https://example.test", **common)
        assert await backend.attach_or_enqueue("猫", "https://example.test", **common) == first
        second = await backend.attach_or_enqueue("猫", "https://example.test", **dict(common, device_id="11:22:33:44:55:66"))
        rebound = await backend.attach_or_enqueue("猫", "https://example.test", **dict(common, owner_stamp="b" * 64))
        resized = await backend.attach_or_enqueue("猫", "https://example.test", **dict(common, paper=replace(_large(), margin_mm=11)))
        assert len({first, second, rebound, resized}) == 4
        payload = await backend.dequeue(timeout=1)
        assert payload.paper == common["paper"] and payload.device_id == common["device_id"]
        await backend.clear_inflight("猫", first, **common)
        assert await backend.attach_or_enqueue("猫", "https://example.test", **common) != first
    asyncio.run(run())


def test_redis_payload_roundtrips_same_snapshot_and_cleans_own_key(monkeypatch, live_redis):
    monkeypatch.setattr(draw_queue, "settings", replace(load_settings(), draw_enqueue_per_second=100,
        draw_per_device_minute=60, draw_per_device_day=100))
    url, _ = live_redis
    backend = draw_queue.RedisDrawQueueBackend(url)
    async def run():
        values = dict(device_id="aa:bb:cc:dd:ee:ff", paper=_large(), owner_stamp="a" * 64, machine="nopaper")
        first = await backend.attach_or_enqueue("猫", "https://example.test", **values)
        other = await backend.attach_or_enqueue("猫", "https://example.test", **dict(values, owner_stamp="b" * 64))
        item = await backend.dequeue(timeout=1)
        assert (item.job_id, item.paper, item.device_id, item.owner_stamp) == (first, _large(), values["device_id"], "a" * 64)
        await backend.clear_inflight("猫", first, **values)
        assert await backend.attach_or_enqueue("猫", "https://example.test", **dict(values, owner_stamp="b" * 64)) == other
        await backend._client().aclose()
    asyncio.run(run())


def test_inprocess_shared_work_cannot_cross_device_or_paper(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_draw_inflight", {})
    monkeypatch.setattr(server, "_device_daily_limit_reached", lambda _: False)
    seen = []
    async def generate(description, base, **kwargs):
        seen.append(kwargs)
        await asyncio.sleep(0)
        return "ok"
    monkeypatch.setattr(server, "_run_draw_job", generate)
    async def run():
        args = dict(chain_push=False, paper=_large(), device_id="aa:bb:cc:dd:ee:ff", machine="nopaper")
        first = server._draw_inprocess("猫", "https://example.test", **args)
        assert server._draw_inprocess("猫", "https://example.test", **args) is first
        second = server._draw_inprocess("猫", "https://example.test", **dict(args, device_id="11:22:33:44:55:66"))
        third = server._draw_inprocess("猫", "https://example.test", **dict(args, paper=replace(_large(), margin_mm=11)))
        assert len({first, second, third}) == 3
        await asyncio.gather(first, second, third)
    asyncio.run(run())
    assert len(seen) == 3 and all(x["paper"] is not None for x in seen)


def test_target_identity_and_portal_url_reject_wrong_shapes():
    good = DrawTarget("aa:bb:cc:dd:ee:ff", "nopaper", _large(), "a" * 64)
    assert DrawTarget.from_dict(good.to_dict()) == good
    for key, value in [("device_id", ""), ("machine", "guess"), ("owner_stamp", "old"), ("paper", {})]:
        with pytest.raises(DevicePaperError):
            DrawTarget.from_dict(dict(good.to_dict(), **{key: value}))
    for url in ["https://example.test/api/internal/usage", "http://user:pass@127.0.0.1:8302/x", "file:///tmp/x"]:
        with pytest.raises(DevicePaperError):
            _portal_base(SimpleNamespace(usage_ingest_url=url))


def test_oss_metadata_roundtrip_cannot_silently_fall_back(tmp_path, monkeypatch):
    bucket_data = {}
    class Missing(Exception):
        status = 404
    class Bucket:
        def put_object(self, key, data, *, headers):
            bucket_data[key] = (data, headers)
        def get_object(self, key):
            if key not in bucket_data:
                raise Missing()
            raw, headers = bucket_data[key]
            stream = io.BytesIO(raw)
            stream.headers = headers
            return stream
    store = OssOutputStore(endpoint="https://invalid.test", bucket_name="x", access_key_id="x", access_key_secret="x", prefix="draw")
    monkeypatch.setattr(store, "_get_bucket", lambda: Bucket())
    from hutuji_mcp.output_store import JobPaths
    base = server._new_basename()
    paths = JobPaths(base, tmp_path / (base + ".svg"), tmp_path / (base + ".gcode"), tmp_path / (base + ".png"))
    paths.svg_path.write_bytes(b"svg")
    paths.preview_path.write_bytes(b"png")
    raw = _minimal_gcode().replace(b"X20 Y20", b"X350 Y550")
    paths.gcode_path.write_bytes(raw)
    write_paper_metadata(paths.gcode_path, _large())
    store.finalize_job(paths, created_at=20000)
    assert len(bucket_data) == 4
    data, mtime = store.read_verified(paths.gcode_path.name)
    assert data == raw and mtime == 20000
    assert store.paper_marker(paths.gcode_path.name, data) == _large().marker
    del bucket_data["draw/" + paper_path(paths.gcode_path).name]
    with pytest.raises(StaleOutputStoreError):
        store.read_verified(paths.gcode_path.name)
