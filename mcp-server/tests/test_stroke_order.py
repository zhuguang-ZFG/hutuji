"""stroke_order 标准笔顺教学链测试。

夹具数据：手写 strokes.jsonl（不依赖 6MB 真实数据集）——
- 「中」4 笔（标准笔顺笔画数）：口=3 笔 + 竖 1 笔
- 「一」1 笔
- 「龘」21 笔（>6 列×3 行首页容量 → 测续页）
几何用 1024 网格的简单线段；y 向上为正（与源数据同）。
"""
import json
import re

import pytest

from hutuji_mcp import stroke_order
from hutuji_mcp.stroke_order import StrokeData, StrokeOrderError


def _write_fixture(tmp_path):
    # 每笔一个简单折线；中：标准 4 笔（竖、横折、横、竖 自上而下）
    zhong = [
        [[512, 900], [512, 700]],                      # 丨（左竖）
        [[512, 900], [700, 900], [700, 700]],          # 𠃍
        [[512, 800], [700, 800]],                      # 一（口中横）
        [[600, 950], [600, 100]],                      # 丨（中竖通顶）
    ]
    yi = [[[200, 500], [800, 500]]]
    da = [[[100 + i * 40, 900], [100 + i * 40, 100]] for i in range(21)]  # 龘 21 笔
    # 步序数字先写（标签用）；测试字后写——「义」不是步序数字，不会被覆盖
    lines = []
    digits = "零一二三四五六七八九"
    for d in digits:
        lines.append(d + "\t" + json.dumps([[[100, 900], [900, 100]]], separators=(",", ":")))
    lines.append("十\t" + json.dumps([[[100, 500], [900, 500]], [[500, 900], [500, 100]]], separators=(",", ":")))
    lines += [
        "中\t" + json.dumps(zhong, separators=(",", ":")),
        "义\t" + json.dumps(yi, separators=(",", ":")),
        "龘\t" + json.dumps(da, separators=(",", ":")),
    ]
    path = tmp_path / "strokes.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tmp_path


def test_load_roundtrip_and_rejections(tmp_path):
    d = _write_fixture(tmp_path)
    data = StrokeData.load(str(d))
    assert len(data.medians["中"]) == 4
    assert data.medians["义"][0][0] == (200, 500)
    with pytest.raises(StrokeOrderError, match="笔顺数据缺失"):
        StrokeData.load(str(tmp_path / "nope"))
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "strokes.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(StrokeOrderError, match="笔顺数据为空"):
        StrokeData.load(str(empty))


def test_layout_progressive_panels_and_bounds(tmp_path):
    """中（4 笔）→ 1 页：大字成形格 + 4 个渐显面板；第 k 格恰含前 k 笔。"""
    data = StrokeData.load(str(_write_fixture(tmp_path)))
    res = stroke_order.layout_teach("中", data)
    assert not res.truncated and res.missing_chars == []
    assert len(res.pages) == 1
    page = res.pages[0]
    xs = [x for s in page.strokes for x, _ in s.points]
    ys = [y for s in page.strokes for _, y in s.points]
    assert min(xs) >= 0 and min(ys) >= 0
    assert max(xs) <= 190.0 and max(ys) <= 277.0  # §5 硬线
    # 面板格数 = 笔数：每个面板一个田字格外框（4 边 1 笔），外框数 = 1（大字）+4（面板）
    frames = [s for s in page.strokes if len(s.points) == 5 and s.points[0] == s.points[-1]]
    assert len(frames) == 5


def test_layout_y_flip_dot_on_top(tmp_path):
    """源数据 y 向上：中字通顶竖的 y=950 端必须落在大字格顶部（翻转正确）。"""
    data = StrokeData.load(str(_write_fixture(tmp_path)))
    res = stroke_order.layout_teach("中", data)
    page = res.pages[0]
    # 大字格外框：5 点闭合、高 30mm（面板格 34mm，以此区分）
    frames = [s for s in page.strokes if len(s.points) == 5 and s.points[0] == s.points[-1]]
    header = min(frames, key=lambda s: abs(30.0 - (max(y for _, y in s.points) - min(y for _, y in s.points))))
    fy0 = min(y for _, y in header.points)
    # 框内通顶竖笔（源 y 100..950）：翻转后顶部应贴格顶（pad 2mm 内）
    top_ys = []
    for s in page.strokes:
        for x, y in s.points:
            if abs(x - (min(x2 for x2, _ in header.points) + 15.0)) < 8.0:
                top_ys.append(y)
    assert top_ys
    assert min(top_ys) - fy0 < 3.0, "源 y=950（顶部）翻转后应贴大字格顶"


def test_layout_missing_chars_and_empty(tmp_path):
    data = StrokeData.load(str(_write_fixture(tmp_path)))
    res = stroke_order.layout_teach("中A", data)
    assert res.missing_chars == ["A"]
    assert len(res.pages) == 1
    with pytest.raises(StrokeOrderError, match="全部缺字"):
        stroke_order.layout_teach("AB", data)
    with pytest.raises(StrokeOrderError, match="没有可教"):
        stroke_order.layout_teach("  ", data)


def test_layout_paginates_many_strokes(tmp_path):
    """龘 21 笔：竖版 5 列×6 行=30 格 ≥ 21 → 全在一页。"""
    data = StrokeData.load(str(_write_fixture(tmp_path)))
    res = stroke_order.layout_teach("龘", data)
    assert len(res.pages) == 1
    assert not res.truncated
    frames_p1 = [s for s in res.pages[0].strokes
                 if len(s.points) == 5 and s.points[0] == s.points[-1]]
    assert len(frames_p1) == 1 + 21  # 大字 + 21 面板（全在一页）


def test_layout_step_labels_present(tmp_path):
    """每个面板右下角有中文步序数字（同数据源 median 渲染）。"""
    data = StrokeData.load(str(_write_fixture(tmp_path)))
    res = stroke_order.layout_teach("中", data)
    page = res.pages[0]
    # 数字「一」的 median 是 [100,900]→[900,100] 对角线：页面板上应有 4 条
    # 小对角线（每个面板一个步序标签）
    diagonals = [
        s for s in page.strokes
        if len(s.points) == 2
        and 3.0 < abs(s.points[1][0] - s.points[0][0]) < 8.0
        and 3.0 < abs(s.points[1][1] - s.points[0][1]) < 8.0
    ]
    assert len(diagonals) == 4


def test_hutuji_teach_end_to_end(tmp_path, monkeypatch):
    """工具端到端：真 vpype + §5 校验 + pages_arg 形状 + 缺字上报。"""
    import asyncio
    from hutuji_mcp import server
    from hutuji_mcp.config import Settings

    stroke_dir = _write_fixture(tmp_path)
    out_dir = (tmp_path / "out").resolve()
    out_dir.mkdir()
    patched = Settings(
        llm_base_url="http://127.0.0.1:1", llm_api_key="", llm_model="", output_dir=out_dir,
        public_base_url="http://127.0.0.1:8300", host="127.0.0.1", port=8300,
        output_ttl_seconds=600, stroke_dir=str(stroke_dir),
    )
    monkeypatch.setattr(server, "settings", patched)
    server._stroke_data_cache.clear()

    payload = json.loads(asyncio.run(server.hutuji_teach("中义")))
    assert payload["total_pages"] == 1  # 多字 2026-09-11 起集中单页字帖（用户拍板）
    assert payload["missing_chars"] == []
    # pages_arg 第 0 页必须与 url/preview_url 一致（设备幂等契约）
    assert json.loads(payload["pages_arg"])[0]["url"] == payload["url"]
    from hutuji_mcp.gcode_gen import validate_gcode
    for p in payload["pages"]:
        name = p["url"].rsplit("/", 1)[-1]
        stats = validate_gcode((out_dir / name).read_text(encoding="utf-8"))
        assert stats.lines > 10
        # 竖版转置后 G-code X = SVG y（≤277），Y = SVG x（≤190）
        xs = [float(m.group(1)) for m in re.finditer(r"X(-?[\d.]+)", (out_dir / name).read_text())]
        assert max(xs) <= 277.0


def test_layout_multichar_mixed_rows_single_page(tmp_path):
    """多字混合版式（2026-09-12 用户拍板「分解行+成形格」，supersede 09-11 成形字帖）：
    中义 → 1 页，每字一行「第 k 格 = 前 k 笔渐显 + 行尾成形格」。"""
    data = StrokeData.load(str(_write_fixture(tmp_path)))
    res = stroke_order.layout_teach("中义", data)
    assert not res.truncated and res.missing_chars == []
    assert len(res.pages) == 1
    page = res.pages[0]
    xs = [x for s in page.strokes for x, _ in s.points]
    ys = [y for s in page.strokes for _, y in s.points]
    assert min(xs) >= 0 and min(ys) >= 0
    assert max(xs) <= 190.0 and max(ys) <= 277.0
    assert page.line_count == 2  # 中、义各一行
    # 田字格外框：中 5 格（4 渐显 + 1 成形）+ 义 2 格（1 渐显 + 1 成形）= 7
    frames = [s for s in page.strokes if len(s.points) == 5 and s.points[0] == s.points[-1]]
    assert len(frames) == 7
    # 步序数字（「一」字形小对角线）：中 4 + 义 1 = 5（成形格无序号）
    diagonals = [
        s for s in page.strokes
        if len(s.points) == 2
        and 2.5 < abs(s.points[1][0] - s.points[0][0]) < 8.0
        and 2.5 < abs(s.points[1][1] - s.points[0][1]) < 8.0
    ]
    assert len(diagonals) == 5
    # 渐显逐笔递增：中行格内 = k 笔画 + 步序数字（k=1..4 → 2,3,4,5），成形格无序号=4；
    # 义行 1 笔画 + 序号 = 2，成形格 = 1
    cells = sorted(frames, key=lambda f: (round(f.points[0][1]), f.points[0][0]))
    assert [_char_strokes_in(page, f) for f in cells] == [2, 3, 4, 5, 4, 2, 1]
    # 折行（一行装不下）：龘（21 笔）+中 → 1 页 4 行 27 格（龘 3 行 9/9/4 + 中 1 行 5 格）
    res2 = stroke_order.layout_teach("龘中", data)
    assert not res2.truncated and len(res2.pages) == 1
    assert res2.pages[0].line_count == 4
    frames2 = [
        s for s in res2.pages[0].strokes
        if len(s.points) == 5 and s.points[0] == s.points[-1]
    ]
    assert len(frames2) == 27  # 龘 22 格 + 中 5 格


def _char_strokes_in(page, frame):
    """格内笔画折线数：排除本格田字格外框（5 点闭合）与 4mm 虚线段。"""
    xs = [p[0] for p in frame.points]
    ys = [p[1] for p in frame.points]
    fx0, fx1, fy0, fy1 = min(xs), max(xs), min(ys), max(ys)
    count = 0
    for s in page.strokes:
        if len(s.points) == 5 and s.points[0] == s.points[-1]:
            if min(p[0] for p in s.points) == fx0 and min(p[1] for p in s.points) == fy0:
                continue  # 本格外框
        pts = s.points
        if not all(fx0 <= x <= fx1 and fy0 <= y <= fy1 for x, y in pts):
            continue
        if len(pts) == 2:
            dx = abs(pts[1][0] - pts[0][0])
            dy = abs(pts[1][1] - pts[0][1])
            if (dx < 0.01 and abs(dy - 4.0) < 0.01) or (dy < 0.01 and abs(dx - 4.0) < 0.01):
                continue  # 十字虚线段
        count += 1
    return count


def test_hutuji_teach_rejects_overlong_and_unconfigured(tmp_path, monkeypatch):
    import asyncio
    from hutuji_mcp import server
    from hutuji_mcp.config import Settings

    out_dir = (tmp_path / "out").resolve()
    out_dir.mkdir()
    patched = Settings(
        llm_base_url="http://127.0.0.1:1", llm_api_key="", llm_model="", output_dir=out_dir,
        public_base_url="http://127.0.0.1:8300", host="127.0.0.1", port=8300,
        output_ttl_seconds=600, stroke_dir="",
    )
    monkeypatch.setattr(server, "settings", patched)
    with pytest.raises(StrokeOrderError, match="笔顺数据未配置"):
        asyncio.run(server.hutuji_teach("中"))
    patched2 = Settings(
        llm_base_url="http://127.0.0.1:1", llm_api_key="", llm_model="", output_dir=out_dir,
        public_base_url="http://127.0.0.1:8300", host="127.0.0.1", port=8300,
        output_ttl_seconds=600, stroke_dir=str(_write_fixture(tmp_path)),
    )
    monkeypatch.setattr(server, "settings", patched2)
    server._stroke_data_cache.clear()
    with pytest.raises(StrokeOrderError, match="一次最多教 20 个字"):
        asyncio.run(server.hutuji_teach("中" * 21))
