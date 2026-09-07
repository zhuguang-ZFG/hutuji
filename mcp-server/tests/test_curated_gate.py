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
    assert stats.lines <= curated_gate.MAX_G1_LINES
    assert stats.pen_lifts >= 1


def test_gate_rejects_invalid_svg_tag(tmp_path: Path):
    bad = '<svg xmlns="http://www.w3.org/2000/svg"><script>x</script></svg>'
    with pytest.raises(curated_gate.GateReject) as ei:
        curated_gate.gate_svg(bad, work_dir=tmp_path)
    assert ei.value.reason


def test_gate_does_not_hard_reject_on_pen_lifts_alone(tmp_path: Path, monkeypatch):
    """用户口径：抬笔多仍能画；不得单凭抬笔次数硬拒。"""
    svg = _poly_svg("0,0 50,0 50,50")

    real_validate = curated_gate.validate_gcode

    def fake_validate(text, *, total_bytes=None):
        from hutuji_mcp.gcode_gen import GcodeStats

        stats = real_validate(text, total_bytes=total_bytes)
        return GcodeStats(lines=stats.lines, bytes=stats.bytes, pen_lifts=200)

    monkeypatch.setattr(curated_gate, "validate_gcode", fake_validate)
    stats = curated_gate.gate_svg(svg, work_dir=tmp_path)
    assert stats.pen_lifts == 200


def test_gate_version_constant():
    assert curated_gate.GATE_VERSION == "4"
    assert not hasattr(curated_gate, "MAX_PEN_LIFTS")


def test_gate_rejects_dense_scribble_on_pre_collapse_text(tmp_path: Path):
    """过碎硬闸吃塌缩前原文：0.25 步进锯齿经 layout 后段长 ≈0.37mm（短段比
    ≈1 > 0.35），塌缩后终稿短段比 ~1/N 会漏——闸若退回读终稿本测试即红。"""
    pts = " ".join(
        f"{i * 0.25:.2f},{0.15 if i % 2 else 0.0:.2f}" for i in range(801)
    )
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="200mm" height="1mm" '
        'viewBox="0 0 200 1" version="1.1">'
        f'<polyline points="{pts}" fill="none" stroke="black" stroke-width="0.1"/>'
        "</svg>"
    )
    with pytest.raises(curated_gate.GateReject) as ei:
        curated_gate.gate_svg(svg, work_dir=tmp_path)
    assert ei.value.reason.startswith("short_g1_ratio")
