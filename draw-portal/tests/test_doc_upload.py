"""文档导入端点契约：/api/draw/upload 的 doc 分支路由 + 多页发送。

转换链（doc_ingest/排版/vpype）在 mcp-server 侧有真测试；本文件钉端点路由、
错误映射、pages.json 驱动的整篇发送，用 monkeypatch 假管线（同 test_app 惯例）。
"""
from __future__ import annotations

import json
import secrets
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from draw_portal.pipeline import DocPipelineResult
from draw_portal.push import PublishResult

from test_app import _client, _register_and_login  # noqa: E402  复用既有基座

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _fake_docx() -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", "<w/>")
    return buf.getvalue()


def _doc_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, gfont_dir: str = "/x/fonts"):
    monkeypatch.setenv("DRAW_PORTAL_GFONT_DIR", gfont_dir)
    client = _client(monkeypatch, tmp_path)
    _register_and_login(client)
    return client


def _fake_doc_result(job_dir: Path) -> DocPipelineResult:
    return DocPipelineResult(
        kind="docx",
        mode="article",
        page_count=2,
        truncated=False,
        missing_chars=["鲲"],
        lines=120,
        bytes=4567,
        page_lines=[80, 40],
        svg_path=job_dir / "draw.svg",
        gcode_path=job_dir / "draw.gcode",
        preview_path=job_dir / "preview.png",
    )


def _patch_doc_pipeline(monkeypatch: pytest.MonkeyPatch, captured: dict | None = None):
    def fake_process(raw, kind, job_dir, *, gfont_dir, **kwargs):
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "pages").mkdir(exist_ok=True)
        for i in (1, 2):
            (job_dir / "pages" / f"page_{i:03d}.gcode").write_text("G21\n", encoding="ascii")
            (job_dir / "pages" / f"page_{i:03d}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (job_dir / "draw.gcode").write_text("G21\n", encoding="ascii")
        (job_dir / "preview.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (job_dir / "pages.json").write_text(
            json.dumps({"kind": kind, "mode": "article", "page_count": 2, "page_lines": [80, 40]}),
            encoding="utf-8",
        )
        if captured is not None:
            captured["gfont_dir"] = gfont_dir
            captured["size_mm"] = kwargs.get("size_mm")
            captured["line_spacing"] = kwargs.get("line_spacing")
        return _fake_doc_result(job_dir)

    def fake_publish(*, job_dir, mcp_output_dir, public_base_url):
        return [
            PublishResult(
                gcode_url=f"https://example.test/files/p{i}.gcode",
                preview_url=f"https://example.test/files/p{i}.png",
                basename=f"draw_test_p{i}",
            )
            for i in (1, 2)
        ]

    monkeypatch.setattr("draw_portal.app.process_upload_doc", fake_process)
    monkeypatch.setattr("draw_portal.app.publish_doc_pages", fake_publish)


def test_doc_upload_success_multipage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    client = _doc_client(monkeypatch, tmp_path)
    captured: dict = {}
    _patch_doc_pipeline(monkeypatch, captured)

    up = client.post(
        "/api/draw/upload",
        files={"file": ("作文.docx", _fake_docx(), DOCX_MIME)},
    )
    assert up.status_code == 200, up.text
    data = up.json()
    assert data["page_count"] == 2
    assert data["doc_kind"] == "docx"
    assert data["doc_mode"] == "article"
    assert data["missing_chars"] == ["鲲"]
    assert [p["url"] for p in data["pages"]] == [
        "https://example.test/files/p1.gcode",
        "https://example.test/files/p2.gcode",
    ]
    # 顶层 preview_url/gcode_url 恒等于 pages[0]（单页兼容面）
    assert data["preview_url"] == data["pages"][0]["preview_url"]
    assert data["gcode_url"] == data["pages"][0]["url"]
    assert captured["gfont_dir"] == "/x/fonts"


def test_doc_upload_passes_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    client = _doc_client(monkeypatch, tmp_path)
    captured: dict = {}
    _patch_doc_pipeline(monkeypatch, captured)

    r = client.post(
        "/api/draw/upload",
        files={"file": ("a.txt", b"hello world text\n" * 2, "text/plain")},
        data={"size_mm": "18", "line_spacing": "2"},
    )
    assert r.status_code == 200, r.text
    assert captured["size_mm"] == 18.0
    assert captured["line_spacing"] == 2.0


def test_doc_upload_invalid_layout_form_is_400(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    client = _doc_client(monkeypatch, tmp_path)
    _patch_doc_pipeline(monkeypatch)

    r = client.post(
        "/api/draw/upload",
        files={"file": ("a.txt", b"hello world text\n" * 2, "text/plain")},
        data={"size_mm": "x"},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "版式参数无效"


def test_doc_upload_layout_params_oob_is_400(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    client = _doc_client(monkeypatch, tmp_path)

    def fake_process(raw, kind, job_dir, *, gfont_dir, **kwargs):
        from draw_portal.write_layout_params import resolve_article_layout

        resolve_article_layout(
            size_mm=kwargs.get("size_mm"),
            line_spacing=kwargs.get("line_spacing"),
        )
        raise AssertionError("resolve_article_layout should have raised")

    monkeypatch.setattr("draw_portal.app.process_upload_doc", fake_process)
    r = client.post(
        "/api/draw/upload",
        files={"file": ("a.txt", b"hello world text\n" * 2, "text/plain")},
        data={"size_mm": "999"},
    )
    assert r.status_code == 400
    assert "字号" in r.json()["detail"]


def test_doc_upload_requires_gfont_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    client = _doc_client(monkeypatch, tmp_path, gfont_dir="")
    up = client.post(
        "/api/draw/upload",
        files={"file": ("作文.docx", _fake_docx(), DOCX_MIME)},
    )
    assert up.status_code == 503
    assert "字库" in up.json()["detail"]


def test_doc_upload_ingest_error_is_400_with_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    client = _doc_client(monkeypatch, tmp_path)

    def fake_process(raw, kind, job_dir, *, gfont_dir, **kwargs):
        from hutuji_mcp.doc_ingest import DocIngestError

        raise DocIngestError("加密文档不支持")

    monkeypatch.setattr("draw_portal.app.process_upload_doc", fake_process)
    up = client.post(
        "/api/draw/upload",
        files={"file": ("作文.docx", _fake_docx(), DOCX_MIME)},
    )
    assert up.status_code == 400
    assert "加密" in up.json()["detail"]

def test_doc_upload_layout_error_is_400(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    client = _doc_client(monkeypatch, tmp_path)

    def fake_process(raw, kind, job_dir, *, gfont_dir, **kwargs):
        from hutuji_mcp.write_text import WriteTextError

        raise WriteTextError("表格过宽：字号缩到 3.5mm 仍排不下")

    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("xl/workbook.xml", "<x/>")
    monkeypatch.setattr("draw_portal.app.process_upload_doc", fake_process)
    up = client.post(
        "/api/draw/upload",
        files={"file": ("成绩.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert up.status_code == 400
    assert "过宽" in up.json()["detail"]


def test_doc_upload_unexpected_error_is_422(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    client = _doc_client(monkeypatch, tmp_path)

    def fake_process(raw, kind, job_dir, *, gfont_dir, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("draw_portal.app.process_upload_doc", fake_process)
    up = client.post(
        "/api/draw/upload",
        files={"file": ("作文.docx", _fake_docx(), DOCX_MIME)},
    )
    assert up.status_code == 422
    assert "文档转换失败" in up.json()["detail"]


def _bind_device(client):
    client.post(
        "/api/device/bind/announce",
        json={
            "mac": "aa:bb:cc:dd:ee:ff",
            "bind_code": "XYZ789",
            "device_token": "test-device-token-" + secrets.token_hex(8),
        },
    )
    client.post("/api/user/bind/complete", json={"bind_code": "XYZ789"})


def test_send_multipage_uses_pages_argument(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """pages.json 在场 → 整篇发送：push 收到 §4.2 pages 紧凑 JSON。"""
    monkeypatch.setattr(
        "draw_portal.app.probe_device_token", lambda **kwargs: (True, "mock ok")
    )
    client = _doc_client(monkeypatch, tmp_path)
    _patch_doc_pipeline(monkeypatch)
    pushed: dict = {}
    monkeypatch.setattr(
        "draw_portal.app.push_draw_self_heal",
        lambda **kwargs: (pushed.update(kwargs), (True, "ok"))[1],
    )

    _bind_device(client)
    up = client.post(
        "/api/draw/upload",
        files={"file": ("作文.docx", _fake_docx(), DOCX_MIME)},
    )
    job_id = up.json()["job_id"]

    send = client.post(f"/api/draw/{job_id}/send")
    assert send.status_code == 200, send.text
    assert send.json()["status"] == "sent"
    pages_arg = pushed.get("pages_arg")
    assert pages_arg is not None
    pages = json.loads(pages_arg)
    assert [p["url"] for p in pages] == [
        "https://example.test/files/p1.gcode",
        "https://example.test/files/p2.gcode",
    ]
    # 设备 ParseArticlePages 纯扫描器：键序 url→preview_url 固定（§4.2）
    assert pages_arg.startswith('[{"url":"https://example.test/files/p1.gcode","preview_url":')
    assert pushed["gcode_url"] == "https://example.test/files/p1.gcode"


def test_send_single_page_has_no_pages_argument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """位图 job（无 pages.json）发送路径逐字节不变。"""
    from draw_portal.pipeline import PipelineResult

    monkeypatch.setattr(
        "draw_portal.app.probe_device_token", lambda **kwargs: (True, "mock ok")
    )
    client = _doc_client(monkeypatch, tmp_path)

    def fake_pipeline(raw, *, content_type, filename, job_dir):
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "draw.gcode").write_text("G21\n", encoding="ascii")
        (job_dir / "preview.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        return PipelineResult(2, 10, 1, job_dir / "draw.svg", job_dir / "draw.gcode", job_dir / "preview.png")

    monkeypatch.setattr("draw_portal.app.process_upload", fake_pipeline)
    pushed: dict = {}
    monkeypatch.setattr(
        "draw_portal.app.push_draw_self_heal",
        lambda **kwargs: (pushed.update(kwargs), (True, "ok"))[1],
    )

    _bind_device(client)
    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
    up = client.post(
        "/api/draw/upload",
        files={"file": ("line.png", fake_png, "image/png")},
    )
    assert up.status_code == 200
    send = client.post(f"/api/draw/{up.json()['job_id']}/send")
    assert send.status_code == 200
    assert pushed.get("pages_arg") is None
