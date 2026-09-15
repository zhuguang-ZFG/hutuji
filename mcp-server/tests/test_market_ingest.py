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
    import hashlib
    from hutuji_mcp.market_schema import validate_entry, read_asset

    # 仅测试写入链，许可文本与几何是夹具，不冒充已经核对的生产素材。
    license_file = tmp_path / "fixture-license.txt"
    license_file.write_bytes(b"Test-only license fixture.\r\nSecond line.\r\n")
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100"><polyline fill="none" stroke="black" points="10,90 50,10 90,90 10,90"/></svg>'
    root = tmp_path / "market"
    entry = ingest.ingest_one(market_dir=root, svg_text=svg, item_id="triangle", title="测试三角形", category="几何装饰",
        license_="CC0-1.0", attribution="", source="manual", source_url="https://example.test/fixture",
        source_revision="fixture-v1", license_file=license_file)
    validate_entry(entry)
    assert entry["reviewed"] is False and entry["source_revision"] == "fixture-v1"
    assert entry["source_sha256"] == hashlib.sha256(svg.encode("utf-8")).hexdigest()
    assert entry["license_source_sha256"] == hashlib.sha256(license_file.read_bytes()).hexdigest()
    assert read_asset(root, entry["license_path"], max_bytes=1024, digest=entry["license_sha256"]) == license_file.read_bytes().replace(b"\r\n", b"\n")
    assert b"\r" not in read_asset(root, entry["gcode_path"], max_bytes=512*1024, digest=entry["gcode_sha256"])
    assert json.loads((root / "manifest.json").read_text(encoding="utf-8"))["version"] == 1
    assert not (root / ".ingest.lock").exists()


def test_ingest_preserves_sharp_vector_corners(tmp_path):
    from xml.etree import ElementTree

    # 六个稀疏折点已是设计几何；五点均值会把凹角移成中心点。
    source_points = "10,10 90,10 90,90 50,50 10,90 10,10"
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100"><polyline fill="none" stroke="black" points="{source_points}"/></svg>'
    license_file = tmp_path / "fixture-license.txt"
    license_file.write_text("Test-only license fixture.", encoding="utf-8")
    root = tmp_path / "market"
    entry = ingest.ingest_one(market_dir=root, svg_text=svg, item_id="notch", title="凹角几何",
        category="几何装饰", license_="CC0-1.0", attribution="", source="manual",
        source_url="https://example.test/notch", source_revision="fixture-v1", license_file=license_file)
    exported = ElementTree.fromstring((root / entry["path"]).read_text(encoding="utf-8"))
    lines = exported.findall("{http://www.w3.org/2000/svg}polyline")
    actual = [tuple(map(float, pair.split(","))) for pair in lines[0].attrib["points"].split()]
    expected = [tuple(map(float, pair.split(","))) for pair in source_points.split()]
    assert actual == expected


@pytest.mark.parametrize("front_color, front_edge_visible", [("blue", True), ("red", False)])
def test_ingest_only_draws_visible_color_boundaries(tmp_path, front_color, front_edge_visible):
    import io
    import vpype
    from shapely.geometry import MultiLineString, Point

    # 前景矩形盖住底图右边；同色相邻区域也不应留下源图层接缝。
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100"><path fill="red" d="M10 10H80V80H10Z"/><path fill="{front_color}" d="M50 20H90V70H50Z"/></svg>'
    license_file = tmp_path / "fixture-license.txt"
    license_file.write_text("Test-only license fixture.", encoding="utf-8")
    root = tmp_path / "market"
    entry = ingest.ingest_one(market_dir=root, svg_text=svg, item_id="layers", title="遮挡几何",
        category="几何装饰", license_="CC0-1.0", attribution="", source="manual",
        source_url="https://example.test/layers", source_revision="fixture-v1", license_file=license_file)
    lines, width, _ = vpype.read_svg(io.StringIO((root / entry["path"]).read_text(encoding="utf-8")), quantization=0.12)
    geometry = MultiLineString([[(z.real * 100 / width, z.imag * 100 / width) for z in line] for line in lines])
    assert geometry.distance(Point(80, 45)) > 5
    front_distance = geometry.distance(Point(50, 45))
    assert (front_distance < 0.2) if front_edge_visible else (front_distance > 5)


def test_ingest_preview_keeps_right_and_bottom_canvas_edges(tmp_path):
    import io
    import numpy as np
    from PIL import Image

    # 真彩图常贴满画布；SVG 边界是 width，位图最后一像素却是 width-1。
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100"><path fill="red" d="M0 0H100V100H0Z"/></svg>'
    license_file = tmp_path / "fixture-license.txt"
    license_file.write_text("Test-only license fixture.", encoding="utf-8")
    root = tmp_path / "market"
    entry = ingest.ingest_one(market_dir=root, svg_text=svg, item_id="frame", title="完整边框",
        category="几何装饰", license_="CC0-1.0", attribution="", source="manual",
        source_url="https://example.test/frame", source_revision="fixture-v1", license_file=license_file)
    pixels = np.asarray(Image.open(io.BytesIO((root / entry["preview_path"]).read_bytes())).convert("L"))
    h, w = pixels.shape
    assert pixels[h//3:2*h//3, 2*w//3:].min() < 200
    assert pixels[2*h//3:, w//3:2*w//3].min() < 200


def _source_geometry(svg):
    import io
    import vpype
    from shapely.geometry import MultiLineString

    lines, width, _ = vpype.read_svg(io.StringIO(ingest._visible_source_svg(svg)), quantization=0.12)
    return MultiLineString([[(z.real * 100 / width, z.imag * 100 / width) for z in line] for line in lines])


@pytest.mark.parametrize("rule, inner, hole", [
    ("nonzero", "M30 30V70H70V30Z", True),
    ("nonzero", "M30 30H70V70H30Z", False),
    ("evenodd", "M30 30H70V70H30Z", True),
])
def test_visible_fill_preserves_svg_holes(rule, inner, hole):
    from shapely.geometry import Point

    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><path fill="red" fill-rule="{rule}" d="M10 10H90V90H10Z {inner}"/></svg>'
    geometry = _source_geometry(svg)
    assert geometry.distance(Point(10, 50)) < 0.2
    assert (geometry.distance(Point(30, 50)) < 0.2) if hole else (geometry.distance(Point(30, 50)) > 10)


def test_nested_transforms_keep_strokes_and_remove_covered_sections():
    from shapely.geometry import Point

    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><g transform="translate(10 20)"><g transform="scale(2)"><path fill="none" stroke="black" d="M0 10H35"/><path fill="red" d="M10 0H20V20H10Z"/></g></g></svg>'
    geometry = _source_geometry(svg)
    assert geometry.distance(Point(20, 40)) < 0.2
    assert geometry.distance(Point(70, 40)) < 0.2
    assert geometry.distance(Point(40, 40)) > 5
    assert geometry.distance(Point(30, 40)) < 0.2


@pytest.mark.parametrize("attributes", ['display="none"', 'visibility="hidden"', 'opacity="0"', 'stroke-opacity="0"'])
def test_invisible_source_never_falls_back_to_visible_paths(attributes):
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><path fill="none" stroke="black" {attributes} d="M10 10H90V90Z"/></svg>'
    with pytest.raises(ValueError, match="可见"):
        ingest._visible_source_svg(svg)


@pytest.mark.parametrize("attributes", ['fill="red" fill-opacity="0.5"', 'fill="none" stroke="red" stroke-opacity="0.5"', 'fill="none" stroke="#ff000080"'])
def test_translucent_source_requires_explicit_flattening(attributes):
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><path {attributes} d="M10 10H90V90Z"/></svg>'
    with pytest.raises(ValueError, match="半透明"):
        ingest._visible_source_svg(svg)
