"""分享令牌：HMAC 往返、过期/篡改拒绝 + 端点端到端（创建/查看/预览免会话）。"""
from __future__ import annotations

import base64
import secrets
import time
from pathlib import Path

import pytest

from draw_portal import share_token


def test_issue_parse_roundtrip():
    secret = "s3cret"
    tok = share_token.issue_token(secret, "job-1", now=1000.0)
    assert share_token.parse_token(secret, tok, now=1100.0) == "job-1"
    # 有效期 24h：23h59m 内有效，25h 失效
    assert share_token.parse_token(secret, tok, now=1000.0 + 23 * 3600) == "job-1"
    assert share_token.parse_token(secret, tok, now=1000.0 + 25 * 3600) is None


def test_parse_rejects_tamper_and_garbage():
    secret = "s3cret"
    tok = share_token.issue_token(secret, "job-1", now=1000.0)
    # 换 secret → 全废
    assert share_token.parse_token("other", tok, now=1100.0) is None
    # 篡改 payload（同长度替换）
    body, sig = tok.split(".", 1)
    tampered = share_token._b64url(b"job-2:999999") + "." + sig
    assert share_token.parse_token(secret, tampered, now=1100.0) is None
    for bad in ("", "abc", "a.b", "aaa.bbb.ccc", "!!!.###"):
        assert share_token.parse_token(secret, bad, now=1100.0) is None


def _setup_user_job(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, app_pkg=None):
    """建会话 + 一个 ready job（复用 test_app 的注册链 + 真实 job 目录文件）。"""
    import json

    from test_app import _client, _register_and_login

    monkeypatch.setenv("DRAW_PORTAL_GFONT_DIR", "/x/fonts")
    client = _client(monkeypatch, tmp_path)
    _register_and_login(client)

    from draw_portal.pipeline import DocPipelineResult
    from draw_portal.push import PublishResult

    def fake_process(text, job_dir, *, gfont_dir, layout, stroke_dir="", font_name=None, profile=None, fit_single_page=False):
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "draw.gcode").write_text("G21\n", encoding="ascii")
        (job_dir / "preview.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        (job_dir / "pages.json").write_text(
            json.dumps({"kind": "write", "mode": "article", "page_count": 1, "page_lines": [1]}),
            encoding="utf-8",
        )
        return DocPipelineResult(
            kind="write",
            mode="article",
            page_count=1,
            truncated=False,
            missing_chars=[],
            lines=10,
            bytes=100,
            page_lines=[10],
            svg_path=job_dir / "draw.svg",
            gcode_path=job_dir / "draw.gcode",
            preview_path=job_dir / "preview.png",
        )

    monkeypatch.setattr("draw_portal.app.process_write_text", fake_process)
    monkeypatch.setattr(
        "draw_portal.app.publish_doc_pages",
        lambda **kw: [
            PublishResult(
                gcode_url="https://example.test/files/w.gcode",
                preview_url="https://example.test/files/w.png",
                basename="draw_write_1",
            )
        ],
    )
    r = client.post("/api/draw/write", json={"text": "春眠不觉晓"})
    assert r.status_code == 200, r.text
    return client, r.json()["job_id"]


def test_share_flow_end_to_end(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    client, job_id = _setup_user_job(monkeypatch, tmp_path)
    r = client.post(f"/api/draw/{job_id}/share")
    assert r.status_code == 200, r.text
    token = r.json()["share_token"]
    assert r.json()["title"].startswith("写字")

    # 免会话接收者：fresh client 无 x-session
    anon = type(client)(client.app)
    view = anon.get(f"/api/draw/share/{token}")
    assert view.status_code == 200
    body = view.json()
    assert body["title"].startswith("写字")
    assert body["preview_url"] == f"/api/draw/share/{token}/preview.png"

    png = anon.get(f"/api/draw/share/{token}/preview.png")
    assert png.status_code == 200
    assert png.headers["content-type"] == "image/png"
    assert png.content[:8] == b"\x89PNG\r\n\x1a\n"

    # 坏 token / 篡改 → 404，不回显内部细节
    assert anon.get("/api/draw/share/not-a-token").status_code == 404
    bad = share_token._b64url(b"job-x:999999") + "." + token.split(".", 1)[1]
    assert anon.get(f"/api/draw/share/{bad}").status_code == 404


def test_share_requires_ready_owner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from test_app import _client, _register_and_login

    client = _client(monkeypatch, tmp_path)
    _register_and_login(client)
    assert client.post("/api/draw/no-such-job/share").status_code == 404
