"""一次性订阅消息通知：字段键位解析、开关门、发送载荷与 fire-and-forget 去重。"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from draw_portal import subscribe_notify
from draw_portal.pipeline import DocPipelineResult
from draw_portal.push import PublishResult

from test_app import _client, _mock_probe_ok, _register_and_login  # noqa: E402


class _FakeConfig:
    wx_appid = "wx-test-appid"
    wx_secret = "wx-test-secret"
    subscribe_template_id = "TPL-1"
    subscribe_fields = {"thing": "thing1", "phrase": "phrase2", "time": "time3"}


def test_parse_fields_default_and_partial():
    assert subscribe_notify.parse_fields("") == {
        "thing": "thing1",
        "phrase": "phrase2",
        "time": "time3",
    }
    assert subscribe_notify.parse_fields("not-json")["thing"] == "thing1"
    assert subscribe_notify.parse_fields('{"thing":"thing9"}')["thing"] == "thing9"
    # 其余键仍回落默认
    assert subscribe_notify.parse_fields('{"thing":"thing9"}')["phrase"] == "phrase2"


def test_enabled_gate():
    assert subscribe_notify.enabled(_FakeConfig()) is True
    cfg = _FakeConfig()
    cfg.subscribe_template_id = ""
    assert subscribe_notify.enabled(cfg) is False


def test_notify_payload_shape(monkeypatch: pytest.MonkeyPatch):
    sent: list[dict] = []
    monkeypatch.setattr(
        subscribe_notify,
        "_post_subscribe_send",
        lambda **kw: (sent.append(kw), 0)[1],
    )
    subscribe_notify.notify_job_result(
        _FakeConfig(),
        openid="oX-1",
        status="success",
        title="a" * 30,  # 超长截断
    )
    assert len(sent) == 1
    call = sent[0]
    assert call["openid"] == "oX-1"
    assert call["template_id"] == "TPL-1"
    data = call["data"]
    assert data["thing1"]["value"] == "a" * 20
    assert data["phrase2"]["value"] == "生成成功"
    assert data["time3"]["value"] == time.strftime("%Y-%m-%d %H:%M")


def test_notify_failure_status_and_disabled(monkeypatch: pytest.MonkeyPatch):
    sent: list[dict] = []
    monkeypatch.setattr(
        subscribe_notify,
        "_post_subscribe_send",
        lambda **kw: (sent.append(kw), 0)[1],
    )
    subscribe_notify.notify_job_result(
        _FakeConfig(), openid="oX-1", status="failed", title="画一只猫"
    )
    assert sent[0]["data"]["phrase2"]["value"] == "生成失败"
    assert sent[0]["data"]["thing1"]["value"] == "画一只猫"

    # 禁用 / 非法状态不发
    subscribe_notify.notify_job_result(
        _FakeConfig(), openid="", status="success", title="x"
    )
    subscribe_notify.notify_job_result(
        _FakeConfig(), openid="oX-1", status="bogus", title="x"
    )
    cfg = _FakeConfig()
    cfg.subscribe_template_id = ""
    subscribe_notify.notify_job_result(cfg, openid="oX-1", status="success", title="x")
    assert len(sent) == 1


def test_async_dedup_window(monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []
    monkeypatch.setattr(subscribe_notify, "enabled", lambda cfg: True)
    monkeypatch.setattr(
        subscribe_notify,
        "notify_job_result",
        lambda cfg, **kw: calls.append(kw["status"]),
    )
    cfg = _FakeConfig()
    subscribe_notify.notify_job_result_async(
        cfg, user_id="u1", openid="o1", status="success", title="t"
    )
    subscribe_notify.notify_job_result_async(
        cfg, user_id="u1", openid="o1", status="success", title="t"
    )
    deadline = time.time() + 5
    while not calls and time.time() < deadline:
        time.sleep(0.05)
    assert calls == ["success"]


def test_subscribe_config_endpoint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("DRAW_PORTAL_WX_SUBSCRIBE_TEMPLATE_ID", "TPL-9")
    monkeypatch.setenv("DRAW_PORTAL_WX_APPID", "wx-test-appid")
    monkeypatch.setenv("DRAW_PORTAL_WX_SECRET", "wx-test-secret")
    client = _client(monkeypatch, tmp_path)
    _register_and_login(client)
    r = client.get("/api/wx/subscribe-config")
    assert r.status_code == 200
    assert r.json() == {"enabled": True, "template_id": "TPL-9"}


def test_subscribe_config_endpoint_disabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    client = _client(monkeypatch, tmp_path)
    _register_and_login(client)
    r = client.get("/api/wx/subscribe-config")
    assert r.status_code == 200
    assert r.json() == {"enabled": False, "template_id": None}


def test_write_endpoint_triggers_notify(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """写字成功路径会发一条 success 通知（订阅链端到端接线验证）。"""
    monkeypatch.setenv("DRAW_PORTAL_GFONT_DIR", "/x/fonts")
    monkeypatch.setenv("DRAW_PORTAL_WX_SUBSCRIBE_TEMPLATE_ID", "TPL-1")
    monkeypatch.setenv("DRAW_PORTAL_WX_APPID", "wx-test-appid")
    monkeypatch.setenv("DRAW_PORTAL_WX_SECRET", "wx-test-secret")
    client = _client(monkeypatch, tmp_path)
    _register_and_login(client)

    recorded: list[tuple[str, str]] = []

    def fake_notify(config, store, *, user_id, status, title):
        recorded.append((status, title))

    monkeypatch.setattr("draw_portal.app._notify_subscribe", fake_notify)

    def fake_process(text, job_dir, *, gfont_dir, layout, stroke_dir="", font_name=None, profile=None, fit_single_page=False):
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "draw.gcode").write_text("G21\n", encoding="ascii")
        (job_dir / "preview.png").write_bytes(b"\x89PNG\r\n\x1a\n")
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
    assert recorded and recorded[-1][0] == "success"
    assert recorded[-1][1] == "春眠不觉晓"


def test_notify_custom_phrase(monkeypatch: pytest.MonkeyPatch):
    """自有事件（奎享完成）传自有文案；缺省仍落生成成功/失败。"""
    sent: list[dict] = []
    monkeypatch.setattr(
        subscribe_notify,
        "_post_subscribe_send",
        lambda **kw: (sent.append(kw), 0)[1],
    )
    subscribe_notify.notify_job_result(
        _FakeConfig(), openid="oX-1", status="success", title="奎享作品",
        phrase="绘制完成",
    )
    assert sent[0]["data"]["phrase2"]["value"] == "绘制完成"
    assert sent[0]["data"]["thing1"]["value"] == "奎享作品"


def test_kdraw_done_endpoint_notifies_bound_device(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DRAW_PORTAL_WX_APPID", "wx-test-appid")
    monkeypatch.setenv("DRAW_PORTAL_WX_SECRET", "wx-test-secret")
    monkeypatch.setenv("DRAW_PORTAL_WX_SUBSCRIBE_TEMPLATE_ID", "TPL-1")
    client = _client(monkeypatch, tmp_path)
    _mock_probe_ok(monkeypatch)
    _register_and_login(client)
    claim = client.post("/api/user/bind/claim", json={"mac": "aa:bb:cc:dd:ee:ff"})
    assert claim.status_code == 200
    assert client.post(
        "/api/device/bind/announce",
        json={"mac": "aa:bb:cc:dd:ee:ff", "bind_code": "ABC123",
              "device_token": "test-device-token-abcdef1234"},
    ).json()["bound"] is True

    # email 用户无 wx_openid；同 test_wx_login 先例直接落库补齐。
    import sqlite3

    conn = sqlite3.connect(tmp_path / "t.db")
    conn.execute("UPDATE users SET wx_openid = 'oX-1' WHERE email = 'buyer@qq.com'")
    conn.commit()
    conn.close()

    sent: list[dict] = []
    monkeypatch.setattr(
        subscribe_notify,
        "_post_subscribe_send",
        lambda **kw: (sent.append(kw), 0)[1],
    )
    r = client.post("/api/device/kdraw/done",
                    json={"mac": "aa:bb:cc:dd:ee:ff", "title": "一匹马"})
    assert r.status_code == 200, r.text
    assert r.json()["notified"] is True
    assert sent[0]["openid"]
    assert sent[0]["data"]["phrase2"]["value"] == "绘制完成"
    assert sent[0]["data"]["thing1"]["value"] == "一匹马"


def test_kdraw_done_rejects_unbound_and_bad_mac(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DRAW_PORTAL_WX_APPID", "wx-test-appid")
    monkeypatch.setenv("DRAW_PORTAL_WX_SECRET", "wx-test-secret")
    monkeypatch.setenv("DRAW_PORTAL_WX_SUBSCRIBE_TEMPLATE_ID", "TPL-1")
    client = _client(monkeypatch, tmp_path)
    r = client.post("/api/device/kdraw/done", json={"mac": "aa:bb:cc:dd:ee:ff"})
    assert r.status_code == 403
    r2 = client.post("/api/device/kdraw/done", json={"mac": "bad"})
    assert r2.status_code == 400
