from __future__ import annotations

from pathlib import Path

import pytest

from hutuji_mcp import curated_gate


def _poly_svg(points: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="190mm" height="190mm" '
        'viewBox="0 0 100 100" version="1.1">'
        f'<polyline points="{points}" fill="none" stroke="black" stroke-width="1"/>'
        "</svg>"
    )


def test_gate_accepts_simple_stroke(tmp_path: Path):
    svg = _poly_svg("10,10 90,10 90,90 10,90 10,10")
    stats = curated_gate.gate_svg(svg, work_dir=tmp_path)
    assert stats.pen_lifts <= curated_gate.MAX_PEN_LIFTS
    assert stats.lines <= curated_gate.MAX_G1_LINES


def test_gate_rejects_invalid_svg_tag(tmp_path: Path):
    bad = '<svg xmlns="http://www.w3.org/2000/svg"><script>x</script></svg>'
    with pytest.raises(curated_gate.GateReject) as ei:
        curated_gate.gate_svg(bad, work_dir=tmp_path)
    assert ei.value.reason


def test_gate_rejects_too_many_pen_lifts(tmp_path: Path, monkeypatch):
    svg = _poly_svg("0,0 50,0 50,50")

    def fake_svg_to_gcode(svg_path, gcode_path):
        from hutuji_mcp.gcode_gen import GcodeStats, svg_to_gcode as real

        stats = real(svg_path, gcode_path)
        return GcodeStats(lines=stats.lines, bytes=stats.bytes, pen_lifts=26)

    monkeypatch.setattr(curated_gate, "svg_to_gcode", fake_svg_to_gcode)
    with pytest.raises(curated_gate.GateReject) as ei:
        curated_gate.gate_svg(svg, work_dir=tmp_path)
    assert "pen_lifts" in ei.value.reason


def test_gate_version_constant():
    assert curated_gate.GATE_VERSION == "1"
