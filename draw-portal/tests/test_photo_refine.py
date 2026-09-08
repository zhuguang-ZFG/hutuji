"""照片两段式契约（2026-09-08 拍板 UX）：Canny 同步先回 + 魔搭 i2i 后台精修。

- 上传照片且配 token → 响应 refined={"status":"pending"}，后台任务把精修
  跑成独立 job 后回填；/api/draw/jobs/{id} 轮询可见 ready + 精修 URL。
- 无 token / 非照片 → refined=null，响应与现状一致（单段 Canny）。
- i2i 任何失败 → refined 置 failed，原 job 的 Canny 结果不受影响（fail-open）。

测试基建注意：后台任务挂在事件循环上，TestClient 必须 `with` 进入——否则
starlette 每个请求新建事件循环，上传响应一落盘循环就关，任务随 loop 被销
毁（生产 uvicorn 单长循环无此问题，纯测试夹具陷阱）。
"""
from __future__ import annotations

import asyncio
import contextlib
import io
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from draw_portal.app import create_app
from draw_portal.pipeline import PipelineResult, is_photo_raster, normalize_raster_to_png


def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, token: str = "") -> None:
    monkeypatch.setenv("DRAW_PORTAL_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("DRAW_PORTAL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DRAW_PORTAL_SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("DRAW_PORTAL_TOKEN_CIPHER_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("DRAW_PORTAL_HOST", "127.0.0.1")
    monkeypatch.setenv("DRAW_PORTAL_PORT", "8302")
    monkeypatch.setenv("DRAW_PORTAL_MCP_OUTPUT_DIR", str(tmp_path / "mcp-out"))
    monkeypatch.setenv("DRAW_PORTAL_PUBLIC_BASE_URL", "https://example.test")
    monkeypatch.setenv("DRAW_PORTAL_SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("DRAW_PORTAL_SMTP_FROM", "noreply@example.test")
    monkeypatch.setenv("DRAW_PORTAL_EMAIL_DEV_EXPOSE_CODE", "1")
    if token:
        monkeypatch.setenv("HUTUJI_MODELSCOPE_TOKEN", token)


@contextlib.contextmanager
def _client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, token: str = ""):
    _env(monkeypatch, tmp_path, token=token)
    monkeypatch.setattr("draw_portal.app.send_verification_email", lambda **kwargs: None)
    with TestClient(create_app()) as client:
        yield client


def _register(client: TestClient) -> None:
    req = client.post(
        "/api/auth/register/request",
        json={"email": "buyer@qq.com", "password": "secret123"},
    )
    code = req.json().get("dev_code")
    assert code
    verify = client.post(
        "/api/auth/register/verify",
        json={"email": "buyer@qq.com", "code": code},
    )
    assert verify.status_code == 200


def _fake_process_upload(raw, *, content_type, filename, job_dir):
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "draw.gcode").write_text("G21\nG90\n", encoding="ascii")
    (job_dir / "preview.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return PipelineResult(2, 10, 1, job_dir / "draw.svg", job_dir / "draw.gcode", job_dir / "preview.png")


def _fake_process_upload_png(png_bytes, job_dir):
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "draw.gcode").write_text("G21\nG90\nG1 X1 Y1\n", encoding="ascii")
    (job_dir / "preview.png").write_bytes(png_bytes)
    return PipelineResult(3, 20, 1, job_dir / "draw.svg", job_dir / "draw.gcode", job_dir / "preview.png")


def _fake_publish(**kwargs):
    basename = f"draw_{kwargs['job_dir'].name}"
    return type("P", (), {
        "gcode_url": f"https://example.test/files/{basename}.gcode",
        "preview_url": f"https://example.test/files/{basename}.png",
        "basename": basename,
    })()


def _upload(client: TestClient):
    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
    return client.post("/api/draw/upload", files={"file": ("photo.png", fake_png, "image/png")})


def _poll_until(client: TestClient, job_id: str, statuses: tuple[str, ...], rounds: int = 40):
    for _ in range(rounds):
        r = client.get(f"/api/draw/jobs/{job_id}")
        assert r.status_code == 200
        refined = r.json().get("refined")
        if refined and refined["status"] in statuses:
            return r.json()
    raise AssertionError(f"refined 未进入 {statuses}")


def _patch_pipeline(monkeypatch: pytest.MonkeyPatch, *, photo: bool) -> None:
    monkeypatch.setattr("draw_portal.app.process_upload", _fake_process_upload)
    monkeypatch.setattr("draw_portal.app.process_upload_png", _fake_process_upload_png)
    monkeypatch.setattr("draw_portal.app.publish_job_files", _fake_publish)
    monkeypatch.setattr("draw_portal.app.is_photo_raster", lambda raw: photo)
    monkeypatch.setattr(
        "draw_portal.app.normalize_raster_to_png",
        lambda raw, *, line_art=True: raw,
    )


def test_photo_upload_refines_in_background(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    calls: list[bytes] = []

    async def fake_i2i(client_arg, token, image_bytes, **kwargs):
        assert token == "ms-token"
        calls.append(image_bytes)
        return b"\x89PNG\r\n\x1a\nREFINED"

    with _client(monkeypatch, tmp_path, token="ms-token") as client:
        _patch_pipeline(monkeypatch, photo=True)
        monkeypatch.setattr("hutuji_mcp.modelscope_client.generate_i2i_png", fake_i2i)
        _register(client)

        up = _upload(client)
        assert up.status_code == 200
        assert up.json()["refined"] == {"status": "pending"}
        job_id = up.json()["job_id"]

        final = _poll_until(client, job_id, ("ready",))
        refined = final["refined"]
        assert refined["job_id"]
        assert refined["gcode_url"].endswith(".gcode")
        assert refined["preview_url"].endswith(".png")
        assert calls, "精修任务必须真正调用魔搭 i2i"
        # 精修 job 本身也是完整 job：可直接查询
        r2 = client.get(f"/api/draw/jobs/{refined['job_id']}")
        assert r2.status_code == 200
        assert r2.json()["status"] == "ready"
        assert r2.json()["refined"] is None


def test_no_token_keeps_single_stage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    async def forbidden(*args, **kwargs):  # pragma: no cover - 到场即失败
        raise AssertionError("无 token 不得调用魔搭")

    with _client(monkeypatch, tmp_path) as client:  # 无 token
        _patch_pipeline(monkeypatch, photo=True)
        monkeypatch.setattr("hutuji_mcp.modelscope_client.generate_i2i_png", forbidden)
        _register(client)

        up = _upload(client)
        assert up.status_code == 200
        assert up.json()["refined"] is None
        job_id = up.json()["job_id"]
        r = client.get(f"/api/draw/jobs/{job_id}")
        assert r.json()["status"] == "ready"
        assert r.json()["refined"] is None


def test_non_photo_keeps_single_stage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """线稿上传（is_photo_raster=False）即便有 token 也不起精修。"""

    async def forbidden(*args, **kwargs):  # pragma: no cover - 到场即失败
        raise AssertionError("非照片不得调用魔搭")

    with _client(monkeypatch, tmp_path, token="ms-token") as client:
        _patch_pipeline(monkeypatch, photo=False)
        monkeypatch.setattr("hutuji_mcp.modelscope_client.generate_i2i_png", forbidden)
        _register(client)

        up = _upload(client)
        assert up.status_code == 200
        assert up.json()["refined"] is None


def test_i2i_failure_marks_failed_and_keeps_canny(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from hutuji_mcp.modelscope_client import ModelScopeError

    async def broken_i2i(*args, **kwargs):
        raise ModelScopeError("魔搭任务排队超时")

    with _client(monkeypatch, tmp_path, token="ms-token") as client:
        _patch_pipeline(monkeypatch, photo=True)
        monkeypatch.setattr("hutuji_mcp.modelscope_client.generate_i2i_png", broken_i2i)
        _register(client)

        up = _upload(client)
        assert up.status_code == 200
        job_id = up.json()["job_id"]
        canny_preview = up.json()["preview_url"]

        final = _poll_until(client, job_id, ("failed",))
        assert final["refined"] == {"status": "failed"}
        # 原 job 的 Canny 结果完好：状态 ready、URL 不变
        assert final["status"] == "ready"
        assert final["preview_url"] == canny_preview
        assert final["gcode_url"].endswith(".gcode")


def test_refine_concurrency_capped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """课堂 NAT 突发：6 个照片上传同时触发精修，在上游同时在途的不得超过
    _refine_slots 名额（4），且排队任务不丢——全部走到 ready。"""
    active = 0
    peak = 0

    async def fake_i2i(client_arg, token, image_bytes, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)  # 占住名额，迫使突发并发显形
        active -= 1
        return b"\x89PNG\r\n\x1a\nREFINED"

    with _client(monkeypatch, tmp_path, token="ms-token") as client:
        _patch_pipeline(monkeypatch, photo=True)
        monkeypatch.setattr("hutuji_mcp.modelscope_client.generate_i2i_png", fake_i2i)
        _register(client)

        job_ids = [_upload(client).json()["job_id"] for _ in range(6)]
        for jid in job_ids:
            _poll_until(client, jid, ("ready",))
    assert peak <= 4, f"并发精修越过上游名额：peak={peak}"
    assert peak >= 2, "并发闸把精修串行化同样违背设计（名额应被用满）"


def test_job_status_404_for_unknown_or_foreign(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    with _client(monkeypatch, tmp_path) as client:
        _register(client)
        r = client.get("/api/draw/jobs/nope")
        assert r.status_code == 404


# ---------------------------------------------------------------- 照片启发式


def _gradient_photo_png() -> bytes:
    """合成充满中间调的「照片」：近白像素远少于 50%。"""
    import imageio.v3 as iio
    import numpy as np

    arr = np.tile(np.linspace(40, 200, 128, dtype=np.uint8), (128, 1))
    buf = io.BytesIO()
    iio.imwrite(buf, arr, extension=".png")
    return buf.getvalue()


def _line_art_png() -> bytes:
    """白底黑框线稿：近白像素远多于 50%。"""
    import imageio.v3 as iio
    import numpy as np

    arr = np.full((128, 128), 255, dtype=np.uint8)
    arr[20:24, 20:108] = 0
    arr[104:108, 20:108] = 0
    buf = io.BytesIO()
    iio.imwrite(buf, arr, extension=".png")
    return buf.getvalue()


def test_is_photo_raster_classification() -> None:
    assert is_photo_raster(_gradient_photo_png()) is True
    assert is_photo_raster(_line_art_png()) is False
    # SVG 字节/垃圾字节判不出 → False（不起精修，维持单段）
    assert is_photo_raster(b"<svg xmlns='http://www.w3.org/2000/svg'/>") is False
    assert is_photo_raster(b"\x00" * 64) is False


def test_normalize_line_art_false_preserves_photo_tones() -> None:
    """i2i 输入必须是原始照片（中间调保留），不是 Canny 黑白稿。"""
    import imageio.v3 as iio
    import numpy as np

    raw = _gradient_photo_png()
    plain = iio.imread(normalize_raster_to_png(raw, line_art=False))
    near_white_plain = float((plain.astype(float) / 255.0 > 0.9).mean())
    assert near_white_plain < 0.5, "line_art=False 不得把照片做成近白线稿"

    cannied = iio.imread(normalize_raster_to_png(raw))
    near_white_canny = float((cannied.astype(float) / 255.0 > 0.9).mean())
    assert near_white_canny > 0.5, "默认口径仍应输出近白 Canny 线稿"
