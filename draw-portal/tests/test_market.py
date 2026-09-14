"""精品图库真实产物、许可、配置与并发回归；素材均为测试夹具。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import os
from pathlib import Path
import threading

from PIL import Image
import pytest

from draw_portal.market import MarketCatalog
from hutuji_mcp.paper_artifacts import read_paper_metadata
from hutuji_mcp.paper_config import PaperConfig
from test_app import _announce, _client, _mock_probe_ok, _register_and_login
from test_paper_settings import _code, _status

MAC = "aa:bb:cc:dd:ee:ff"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100"><polyline stroke="black" fill="none" points="10,90 50,10 90,90 10,90"/></svg>'


def _write_item(root, item_id, category="动物", license_="CC0-1.0", attribution=""):
    stream = io.BytesIO()
    Image.new("RGB", (16, 16), "white").save(stream, format="PNG")
    files = {
        "svg": (f"svg/{category}/{item_id}.svg", SVG),
        "gcode": (f"gcode/{category}/{item_id}.gcode", _code()),
        "preview": (f"preview/{category}/{item_id}.png", stream.getvalue()),
        "license": ("licenses/test.txt", b"Test fixture only; not a real source license."),
    }
    entry = dict(id=item_id, title="测试作品 " + item_id, category=category, license=license_,
                 attribution=attribution, source="test", source_url="https://example.test/source",
                 source_revision="fixture-v1", viewBox="0 0 100 100", bytes=len(SVG),
                 quality_score=0.9, ingested_at="2026-09-14", gate_version="4",
                 changes="测试素材转为单线稿", reviewed=True)
    for kind, (rel, raw) in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        entry[{"svg": "path", "gcode": "gcode_path", "preview": "preview_path", "license": "license_path"}[kind]] = rel
        entry[kind + "_sha256"] = hashlib.sha256(raw).hexdigest()
    return entry


def _base(root, count=20, category="动物"):
    return [_write_item(root, f"base-{category}-{i}".replace("动物", "a").replace("植物", "b"), category)
            for i in range(count)]


def _write_manifest(root, entries):
    (root / "manifest.json").write_text(json.dumps({"version": 1, "items": entries}, ensure_ascii=False), encoding="utf-8")


def _market_client(monkeypatch, tmp_path, entries, *, bound=False, paper=None):
    root = tmp_path / "market"
    _write_manifest(root, entries)
    monkeypatch.setenv("DRAW_PORTAL_MARKET_DIR", str(root))
    _mock_probe_ok(monkeypatch)
    client = _client(monkeypatch, tmp_path)
    _register_and_login(client)
    monkeypatch.setattr("draw_portal.app.push_tool_call",
                        lambda **kwargs: (True, _status(paper or PaperConfig.default_for("nopaper"))))
    if bound:
        assert client.post("/api/user/bind/claim", json={"mac": MAC}).status_code == 200
        assert _announce(client).json()["bound"]
    return client


def _body(entry, *, bound=True):
    return {"revision": entry["svg_sha256"], "device_mac": MAC if bound else None}


def test_license_whitelist_and_attribution_hide_invalid_items(monkeypatch, tmp_path):
    root = tmp_path / "market"
    entries = _base(root)
    for index, license_ in enumerate(("CC-BY-SA-4.0", "CC-BY-NC-4.0", "unknown", "", "CC-BY-4.0")):
        entries.append(_write_item(root, f"bad-{index}", license_=license_))
    valid = _write_item(root, "credit", license_="CC-BY-4.0", attribution="示例作者，CC BY 4.0")
    entries.append(valid)
    client = _market_client(monkeypatch, tmp_path, entries)
    assert client.get("/api/market/categories").json()["categories"] == [{"name": "动物", "count": 21}]
    for index in range(5):
        assert client.get(f"/api/market/item/bad-{index}").status_code == 404
    assert client.get("/api/market/item/credit").json()["attribution"] == valid["attribution"]


def test_categories_pagination_and_coverage_gate(monkeypatch, tmp_path):
    root = tmp_path / "market"
    client = _market_client(monkeypatch, tmp_path, _base(root, 35) + _base(root, 19, "植物"))
    assert client.get("/api/market/categories").json()["categories"] == [{"name": "动物", "count": 35}]
    first = client.get("/api/market/list", params={"category": "动物"}).json()
    assert len(first["items"]) == 30 and first["next_cursor"] == 30
    last = client.get("/api/market/list", params={"category": "动物", "cursor": 30}).json()
    assert len(last["items"]) == 5 and last["next_cursor"] is None
    assert client.get("/api/market/list", params={"category": "植物"}).json()["items"] == []
    assert client.get("/api/market/item/base-b-0").status_code == 404


def test_disabled_and_unauthenticated(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    assert client.get("/api/market/categories").status_code == 401
    _register_and_login(client)
    assert client.get("/api/market/categories").status_code == 404


def test_manifest_bad_shapes_duplicates_and_missing_files(tmp_path):
    root = tmp_path / "market"
    entries = _base(root)
    duplicate = _write_item(root, "duplicate")
    bad = _write_item(root, "missing")
    (root / bad["path"]).unlink()
    _write_manifest(root, entries + [None, [], "bad", duplicate, dict(duplicate, category="植物"), bad])
    catalog = MarketCatalog(root)
    assert catalog.categories() == {"动物": 20}
    assert catalog.get("duplicate") is None and catalog.get("missing") is None
    (root / "manifest.json").write_text("[]", encoding="utf-8")
    assert catalog.categories() == {"动物": 20}


def test_preview_is_revision_bound_and_rejects_changed_file(monkeypatch, tmp_path):
    root = tmp_path / "market"
    entries = _base(root)
    client = _market_client(monkeypatch, tmp_path, entries)
    detail = client.get("/api/market/item/base-a-0").json()
    assert client.get(detail["preview_url"]).status_code == 200
    assert client.get("/api/market/item/base-a-0/preview.png?v=old").status_code == 404
    (root / entries[0]["preview_path"]).write_bytes(b"changed")
    assert client.get(detail["preview_url"]).status_code == 404


@pytest.mark.parametrize("kind", ["escape", "hardlink", "unreviewed", "fingerprint", "injection", "gcode"])
def test_catalog_excludes_untrusted_assets(tmp_path, kind):
    root = tmp_path / "market"
    entries = _base(root)
    entry = _write_item(root, "bad")
    if kind == "escape":
        entry["preview_path"] = "../outside.png"
    elif kind == "hardlink":
        os.link(root / entry["path"], root / "linked.svg")
    elif kind == "unreviewed":
        entry["reviewed"] = False
    elif kind == "fingerprint":
        entry["svg_sha256"] = "0" * 64
    elif kind == "gcode":
        raw = _code(300, 300)
        (root / entry["gcode_path"]).write_bytes(raw)
        entry["gcode_sha256"] = hashlib.sha256(raw).hexdigest()
    else:
        raw = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        (root / entry["path"]).write_bytes(raw)
        entry["svg_sha256"], entry["bytes"] = hashlib.sha256(raw).hexdigest(), len(raw)
    _write_manifest(root, entries + [entry])
    catalog = MarketCatalog(root)
    assert catalog.categories() == {"动物": 20} and catalog.get("bad") is None


def test_send_converts_to_current_paper_and_preserves_metadata(monkeypatch, tmp_path):
    root = tmp_path / "market"
    entries = _base(root)
    large = PaperConfig("A2", 420, 594, False, 10, False, 400, 574)
    client = _market_client(monkeypatch, tmp_path, entries, bound=True, paper=large)
    calls = []
    monkeypatch.setattr("draw_portal.app.push_draw_self_heal", lambda **kwargs: (calls.append(kwargs) or True, "ok"))
    for _ in range(2):
        result = client.post("/api/market/item/base-a-0/send", json=_body(entries[0]))
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "sent"
    artifacts = list((tmp_path / "mcp-out").glob("*.gcode"))
    assert len(artifacts) == 2
    assert all(read_paper_metadata(path) == large for path in artifacts)
    assert all(path.read_bytes() != _code() for path in artifacts)
    assert calls[0]["device_mac"] == MAC and calls[0]["auto_confirm"]
    calls[0]["before_action"]()


def test_send_rejects_changed_revision_and_unselected_device(monkeypatch, tmp_path):
    root = tmp_path / "market"
    entries = _base(root)
    client = _market_client(monkeypatch, tmp_path, entries, bound=True)
    calls = []
    monkeypatch.setattr("draw_portal.app.push_draw_self_heal", lambda **kwargs: calls.append(kwargs))
    body = _body(entries[0])
    assert client.post("/api/market/item/base-a-0/send", json=dict(body, revision="0" * 64)).status_code == 409
    assert client.post("/api/market/item/base-a-0/send", json=dict(body, device_mac=None)).status_code == 409
    assert not calls


def test_favorite_retains_credit_and_paper_and_trims_sidecars(monkeypatch, tmp_path):
    root = tmp_path / "market"
    entries = _base(root)
    entries[0]["license"], entries[0]["attribution"] = "CC-BY-4.0", "示例作者，CC BY 4.0"
    monkeypatch.setenv("DRAW_PORTAL_SAVED_PREVIEW_LIMIT", "2")
    client = _market_client(monkeypatch, tmp_path, entries, bound=True)
    results = []
    for item in (entries[1], entries[2], entries[0]):
        response = client.post(f"/api/market/item/{item['id']}/favorite", json=_body(item))
        assert response.status_code == 200, response.text
        results.append(response.json())
    saved = client.get("/api/draw/saved").json()["items"]
    assert len(saved) == 2 and saved[0]["credit"]["attribution"] == entries[0]["attribution"]
    assert client.get(f"/api/draw/saved/{results[-1]['id']}").json()["credit"]["license"] == "CC-BY-4.0"
    # 临时转换目录会收回；保留的每件收藏只有自己的一套配置伴随文件。
    codes = list((tmp_path / "data").rglob("*.gcode"))
    assert len(codes) == 2
    assert all(read_paper_metadata(path) == PaperConfig.default_for("nopaper") for path in codes)
    assert len(list((tmp_path / "data").rglob("*.context.json"))) == 2


def test_same_source_and_paper_share_one_conversion(monkeypatch, tmp_path):
    from draw_portal import pipeline
    root = tmp_path / "market"
    entries = _base(root)
    _write_manifest(root, entries)
    catalog = MarketCatalog(root)
    item = catalog.get("base-a-0")
    real = pipeline.process_upload_svg
    entered, release = threading.Event(), threading.Event()
    calls = []
    def slow(*args, **kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(10)
        return real(*args, **kwargs)
    monkeypatch.setattr(pipeline, "process_upload_svg", slow)
    paper = PaperConfig.default_for("nopaper")
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(catalog.materialize, item, tmp_path / "one", paper)
        assert entered.wait(10)
        second = pool.submit(catalog.materialize, item, tmp_path / "two", paper)
        release.set()
        first.result(timeout=30)
        second.result(timeout=30)
    assert len(calls) == 1
    assert read_paper_metadata(tmp_path / "two/draw.gcode") == paper
