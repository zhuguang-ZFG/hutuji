"""Twemoji 矢量源通道契约：映射表卫生、转换、入库闸。"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_vector():
    spec = importlib.util.spec_from_file_location(
        "curated_vector_prep",
        ROOT / "scripts" / "curated_vector_prep.py",
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_MINI_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 36 36">'
    '<circle cx="18" cy="18" r="12" fill="#fc0"/>'
    '<rect x="8" y="8" width="6" height="6" fill="#000"/>'
    "</svg>"
)


def test_mapping_wellformed() -> None:
    """映射类目必须 ⊆ QD 298 类目；code 为 twemoji 文件名形态；无空列表。"""
    import re

    from hutuji_mcp import quickdraw

    data = json.loads(
        (ROOT / "hutuji_mcp" / "twemoji_categories.json").read_text(encoding="utf-8")
    )
    mapping = {k: v for k, v in data.items() if not k.startswith("_")}
    valid = set(json.loads((ROOT / "hutuji_mcp" / "quickdraw_categories.json").read_text(encoding="utf-8")).values())
    code_re = re.compile(r"^[0-9a-f]{4,6}$")
    for cat, codes in mapping.items():
        assert cat in valid, f"映射类目不在 QD 298 内：{cat}"
        assert codes, f"{cat}: 空 code 列表"
        for code in codes:
            assert code_re.match(code), f"{cat}: 非法 code {code}"


def test_convert_produces_gated_plotter_svg(tmp_path: Path) -> None:
    """合成双形状 SVG → 转换 → 过入库闸（gate v3 不拒）。"""
    vec = _load_vector()
    from hutuji_mcp.curated_gate import gate_svg

    plotter = vec.convert_svg(_MINI_SVG, "mini")
    assert "<polyline" in plotter
    gate_svg(plotter, work_dir=tmp_path)  # 不抛即过闸


def test_convert_rejects_empty_paths() -> None:
    vec = _load_vector()
    empty = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 36 36"></svg>'
    with pytest.raises(ValueError, match="无可画路径"):
        vec.convert_svg(empty, "empty")


def test_quantization_passes_short_g1_gate(tmp_path: Path) -> None:
    """量化口径钉死：36 单位画布段长 ≥0.5mm@190mm 纸面，防 short_g1 闸误拒。"""
    vec = _load_vector()
    from hutuji_mcp.curated_gate import gate_svg

    circle = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 36 36">'
        '<circle cx="18" cy="18" r="16" fill="#0af"/></svg>'
    )
    plotter = vec.convert_svg(circle, "circle")
    gate_svg(plotter, work_dir=tmp_path)
