"""网页文字生图（/api/draw/generate）契约：鉴权、配额、错误映射与物化。"""
from __future__ import annotations

import io
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest
from fastapi.testclient import TestClient

from draw_portal import generate
from draw_portal.app import create_app
from draw_portal.generate import GenerateError, GeneratedDraw

from test_app import _client, _register_and_login  # noqa: E402  复用既有基座


def _gen_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, daily_limit: int = 10
) -> TestClient:
    monkeypatch.setenv("DRAW_PORTAL_USAGE_INGEST_SECRET", "ingest-secret")
    monkeypatch.setenv("DRAW_PORTAL_GENERATE_DAILY_LIMIT", str(daily_limit))
    client = _client(monkeypatch, tmp_path)
    _register_and_login(client)
    return client


def _fake_result() -> dict:
    return {
        "url": "http://127.0.0.1:8300/files/draw_20260831_120000_abcdefghij0123456789ab.gcode",
        "preview_url": "http://127.0.0.1:8300/files/draw_20260831_120000_abcdefghij0123456789ab.png",
        "lines": 42,
        "bytes": 1024,
        "pen_lifts": 3,
    }


def _fake_materialize(*, base_url, result, job_dir):
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "draw.gcode").write_text("G21\n", encoding="utf-8")
    (job_dir / "preview.png").write_bytes(b"\x89PNG")
    return GeneratedDraw(lines=42, bytes=1024, pen_lifts=3)


def test_generate_requires_ingest_secret_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """未配共享密钥时 fail-closed 503（密钥即通道开关）。"""
    client = _client(monkeypatch, tmp_path)  # _env 不设 secret
    _register_and_login(client)
    r = client.post("/api/draw/generate", json={"description": "一只猫"})
    assert r.status_code == 503


def test_generate_happy_path_publishes_and_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    client = _gen_client(monkeypatch, tmp_path)
    captured: dict = {}

    def fake_request(*, base_url, secret, description, timeout_seconds, machine="", paper=None, device_id=""):
        captured.update(
            base_url=base_url, secret=secret,
            description=description, timeout_seconds=timeout_seconds,
            paper=paper, device_id=device_id,
        )
        return _fake_result()

    monkeypatch.setattr("draw_portal.app.request_internal_draw", fake_request)
    monkeypatch.setattr("draw_portal.app.materialize_generated_job", _fake_materialize)

    r = client.post("/api/draw/generate", json={"description": "一只简笔画猫"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "ready"
    assert data["lines"] == 42 and data["pen_lifts"] == 3
    assert data["gcode_url"].startswith("https://example.test/files/draw_")
    assert data["preview_url"].endswith(".png")

    # 密钥与 loopback 基址原样透传；产物真的落进 MCP output（能力 URL 可下载前提）
    assert captured["secret"] == "ingest-secret"
    assert captured["base_url"] == "http://127.0.0.1:8300"
    from hutuji_mcp.paper_config import PaperConfig
    assert captured["paper"] == PaperConfig().to_dict()
    assert captured["device_id"] == ""
    basename = data["gcode_url"].rsplit("/", 1)[-1].removesuffix(".gcode")
    assert (tmp_path / "mcp-out" / f"{basename}.gcode").is_file()
    assert (tmp_path / "mcp-out" / f"{basename}.png").is_file()


def test_generate_daily_quota_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    client = _gen_client(monkeypatch, tmp_path, daily_limit=1)
    monkeypatch.setattr(
        "draw_portal.app.request_internal_draw", lambda **kw: _fake_result()
    )
    monkeypatch.setattr("draw_portal.app.materialize_generated_job", _fake_materialize)

    first = client.post("/api/draw/generate", json={"description": "第一张"})
    assert first.status_code == 200
    second = client.post("/api/draw/generate", json={"description": "第二张"})
    assert second.status_code == 429
    assert "上限" in second.json()["detail"]


def test_generate_failed_attempt_does_not_consume_quota(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """付费生图失败不罚买家：失败单不占 24h 配额。"""
    client = _gen_client(monkeypatch, tmp_path, daily_limit=1)

    def boom(**kw):
        raise GenerateError("生图服务暂不可用", status=502)

    monkeypatch.setattr("draw_portal.app.request_internal_draw", boom)
    failed = client.post("/api/draw/generate", json={"description": "失败单"})
    assert failed.status_code == 502

    monkeypatch.setattr(
        "draw_portal.app.request_internal_draw", lambda **kw: _fake_result()
    )
    monkeypatch.setattr("draw_portal.app.materialize_generated_job", _fake_materialize)
    ok = client.post("/api/draw/generate", json={"description": "随后成功单"})
    assert ok.status_code == 200


@pytest.mark.parametrize(
    "status,expect_detail",
    [(400, True), (429, True), (502, False), (504, False)],
)
def test_generate_maps_internal_error_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int, expect_detail: bool
):
    client = _gen_client(monkeypatch, tmp_path)

    def boom(**kw):
        raise GenerateError(f"err{status}", status=status)

    monkeypatch.setattr("draw_portal.app.request_internal_draw", boom)
    r = client.post("/api/draw/generate", json={"description": "一只猫"})
    assert r.status_code == status
    # 400/429 输入白话透传；504/502 固定文案，不回显内部 detail（2026-08-31 评审裁决）
    assert (f"err{status}" in r.json()["detail"]) is expect_detail


def test_generate_rejects_blank_description(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    client = _gen_client(monkeypatch, tmp_path)
    r = client.post("/api/draw/generate", json={"description": "   "})
    assert r.status_code == 400


# ---------- generate.py 单元面 ----------


def test_name_from_capability_validates():
    good = "http://127.0.0.1:8300/files/draw_20260831_120000_abcdefghij0123456789ab.png"
    assert generate._name_from_capability(good).endswith(".png")
    for bad in [
        "http://x/files/../../etc/passwd",
        "http://x/files/evil.png",
        "not-a-url",
        None,
        42,
    ]:
        with pytest.raises(GenerateError):
            generate._name_from_capability(bad)


def test_request_internal_draw_maps_http_error(monkeypatch: pytest.MonkeyPatch):
    def fake_urlopen(req, timeout):
        body = json.dumps({"detail": "服务器忙"}).encode()
        raise HTTPError(req.full_url, 429, "busy", None, io.BytesIO(body))

    monkeypatch.setattr(generate, "urlopen", fake_urlopen)
    with pytest.raises(GenerateError) as ei:
        generate.request_internal_draw(
            base_url="http://127.0.0.1:8300",
            secret="s",
            description="d",
            timeout_seconds=1,
        )
    assert ei.value.status == 429
    assert "服务器忙" in str(ei.value)


def test_materialize_cleans_partial_files_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """第二个文件下载失败时，第一个已下载文件必须一并清掉。"""
    calls: list[str] = []

    def fake_download(url, dest, *, max_bytes, timeout_seconds):
        calls.append(url)
        if url.endswith(".gcode"):
            dest.write_text("G21\n", encoding="utf-8")
        else:
            raise GenerateError("png 下载失败", status=502)

    monkeypatch.setattr(generate, "_download_limited", fake_download)
    job_dir = tmp_path / "jobs" / "j1"
    with pytest.raises(GenerateError):
        generate.materialize_generated_job(
            base_url="http://127.0.0.1:8300",
            result=_fake_result(),
            job_dir=job_dir,
        )
    assert len(calls) == 2
    assert list(job_dir.iterdir()) == []


def _notify_recorder(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    def fake_notify(config, store, *, user_id, status, title, phrase=None):
        calls.append((status, title))
        return True

    monkeypatch.setattr("draw_portal.app._notify_subscribe", fake_notify)
    return calls


def test_generate_busy_429_suppresses_failed_notification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """2026-09-12 忙批：MCP 全忙瞬态映射 429 后，portal 不推「生成失败」订阅
    消息——瞬态忙几秒重试即成，失败推送+随后的成功推送只会误导用户。"""
    client = _gen_client(monkeypatch, tmp_path)
    calls = _notify_recorder(monkeypatch)

    def boom(**kw):
        raise GenerateError("生图通道都在忙，请稍等几秒再试", status=429)

    monkeypatch.setattr("draw_portal.app.request_internal_draw", boom)
    r = client.post("/api/draw/generate", json={"description": "一只猫"})
    assert r.status_code == 429
    assert "都在忙" in r.json()["detail"]
    assert calls == []


def test_generate_permanent_failure_and_success_still_notify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """忙以外的失败（502）与成功照常推送：静默只限 429 忙类。"""
    client = _gen_client(monkeypatch, tmp_path)
    calls = _notify_recorder(monkeypatch)

    def boom(**kw):
        raise GenerateError("生图服务暂不可用", status=502)

    monkeypatch.setattr("draw_portal.app.request_internal_draw", boom)
    failed = client.post("/api/draw/generate", json={"description": "失败单"})
    assert failed.status_code == 502
    assert [status for status, _ in calls] == ["failed"]

    calls.clear()
    monkeypatch.setattr(
        "draw_portal.app.request_internal_draw", lambda **kw: _fake_result()
    )
    monkeypatch.setattr("draw_portal.app.materialize_generated_job", _fake_materialize)
    ok = client.post("/api/draw/generate", json={"description": "成功单"})
    assert ok.status_code == 200
    assert [status for status, _ in calls] == ["success"]
