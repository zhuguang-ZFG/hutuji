"""离线图库工具不能绕开来源、许可或 SVG 注入边界。"""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("market_ingest", ROOT / "scripts/market_ingest.py")
ingest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ingest)


@pytest.mark.parametrize("svg", [
    '<!DOCTYPE svg [<!ENTITY x "x">]><svg>&x;</svg>',
    '<svg><image href="file:///etc/passwd"/></svg>',
    '<svg><path d="M0 0L2 2" onload="run()"/></svg>',
])
def test_source_svg_is_checked_before_third_party_parser(svg):
    with pytest.raises(ValueError):
        ingest._validate_source(svg)


def test_missing_source_or_license_never_creates_manifest(tmp_path):
    args = dict(market_dir=tmp_path, svg_text="<svg/>", item_id="cat", title="小猫", category="动物",
                license_="CC0-1.0", attribution="", source="manual", source_revision="fixture-v1")
    with pytest.raises(ValueError, match="许可"):
        ingest.ingest_one(**args)
    assert not (tmp_path / "manifest.json").exists()
    with pytest.raises(ValueError, match="合法标识"):
        ingest.ingest_one(**dict(args, item_id="../escape"))


def test_ingest_keeps_license_hashes_revision_and_unreviewed_state(tmp_path):
    from hutuji_mcp.market_schema import validate_entry, read_asset

    # 仅测试写入链，许可文本与几何是夹具，不冒充已经核对的生产素材。
    license_file = tmp_path / "fixture-license.txt"
    license_file.write_text("Test-only license fixture.", encoding="utf-8")
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100"><polyline fill="none" stroke="black" points="10,90 50,10 90,90 10,90"/></svg>'
    root = tmp_path / "market"
    entry = ingest.ingest_one(market_dir=root, svg_text=svg, item_id="triangle", title="测试三角形", category="几何装饰",
        license_="CC0-1.0", attribution="", source="manual", source_url="https://example.test/fixture",
        source_revision="fixture-v1", license_file=license_file)
    validate_entry(entry)
    assert entry["reviewed"] is False and entry["source_revision"] == "fixture-v1"
    assert read_asset(root, entry["license_path"], max_bytes=1024, digest=entry["license_sha256"]) == license_file.read_bytes()
    assert json.loads((root / "manifest.json").read_text(encoding="utf-8"))["version"] == 1
    assert not (root / ".ingest.lock").exists()
