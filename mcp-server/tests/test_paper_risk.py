"""纸面风险闸契约：阈值、reason token、状态机与 §5 validate 的抬笔一致性。

fixture 逐字对齐 `hutuji_gwrite.toml` 模板（segment_first = G0 空走 +
G1G90 Z5.0 落笔；segment = G1 X..Y..F8000；line_end = G1G90 Z0.0 抬笔），
保证状态机解析的是生产真实形态，而非自造格式。
"""
from __future__ import annotations

import asyncio
import json

import pytest
from hutuji_mcp import paper_risk
from hutuji_mcp.gcode_gen import validate_gcode

_HEADER = "G21\nG90\nF8000\nG1G90 Z0.0F10000\nG1G90 Z0.0F10000\n"


def _gcode(strokes: list[list[tuple[float, float]]]) -> str:
    """strokes = 每笔的落笔点序列；首点经 G0 空走到达（对齐 segment_first）。"""
    parts = [_HEADER]
    for stroke in strokes:
        parts.append(f"G0 X{stroke[0][0]:.3f}Y{stroke[0][1]:.3f}F8000\n")
        parts.append("G1G90 Z5.0F10000\n")
        for x, y in stroke[1:]:
            parts.append(f"G1 X{x:.3f}Y{y:.3f}F8000\n")
        parts.append("G1G90 Z0.0F10000\n")
    parts.append("G1G90 Z0.0F10000\n")
    return "".join(parts)


def test_version_constant() -> None:
    assert paper_risk.PAPER_RISK_GATE_VERSION == "2"


def test_pass_long_continuous_stroke() -> None:
    stroke = [(10.0 + i * 9.0, 20.0 + (i % 2) * 4.0) for i in range(20)]
    report = paper_risk.enforce_gcode(_gcode([stroke]), pen_lifts=1)
    assert report.reason is None
    assert report.pen_lifts == 1
    assert report.short_g1_ratio == 0.0
    assert report.travel_gap_p50_mm == 0.0
    assert report.ink_span_mm >= 40.0


def test_reject_high_pen_lifts() -> None:
    strokes = [
        [(5.0 + i, 10.0), (45.0 + i, 10.0)] for i in range(45)
    ]
    with pytest.raises(paper_risk.PaperRiskReject) as ei:
        paper_risk.enforce_gcode(_gcode(strokes), pen_lifts=45)
    assert ei.value.reason == "pen_lifts"
    assert ei.value.report.pen_lifts == 45


def test_reject_large_travel_gap_p50() -> None:
    # 12 笔长连续段，笔间空走 10mm → 虚线/断段形态。
    strokes = [
        [(10.0, 10.0 + i * 10.0), (20.0, 10.0 + i * 10.0), (30.0, 10.0 + i * 10.0)]
        for i in range(12)
    ]
    with pytest.raises(paper_risk.PaperRiskReject) as ei:
        paper_risk.enforce_gcode(_gcode(strokes), pen_lifts=12)
    assert ei.value.reason == "travel_gap"
    assert ei.value.report.travel_gap_p50_mm >= 10.0


def test_reject_short_g1_ratio() -> None:
    stroke = [(10.0 + i * 0.45, 10.0) for i in range(100)]
    with pytest.raises(paper_risk.PaperRiskReject) as ei:
        paper_risk.enforce_gcode(_gcode([stroke]), pen_lifts=1)
    assert ei.value.reason == "short_g1_ratio"
    assert ei.value.report.short_g1_ratio > paper_risk.MAX_SHORT_G1_RATIO


def test_reject_scribble_core() -> None:
    # 涂鸦团骑 1mm 桶缝（中心恰在 x=10.0/y=10.0 桶界）：0.09mm 网格 50 点
    # 密团（段长 0.09<0.5 短段），固定桶会劈半逃逸，平移不变窗必须命中。
    cluster = [
        (9.6 + (i % 10) * 0.09, 9.6 + (i // 10) * 0.09) for i in range(50)
    ]
    # 长尾蛇形拉跨度并把短段比压到阈值下（密团只贡献 ~45 短段）。
    tail: list[tuple[float, float]] = []
    for i in range(18):
        y = 12.0 + i * 3.0
        row = [(10.0 + j * 14.0, y) for j in range(7)]
        tail += row if i % 2 == 0 else row[::-1]
    with pytest.raises(paper_risk.PaperRiskReject) as ei:
        paper_risk.enforce_gcode(_gcode([cluster + tail]), pen_lifts=1)
    assert ei.value.reason == "scribble_core"
    assert ei.value.report.scribble_core_hits >= 1


def test_scribble_core_spread_short_segments_no_hit() -> None:
    # 短段但摊开的线稿不得误判涂鸦团：0.45mm 段 × 100 点一线排开，
    # 1mm 窗内至多 ~3 个中点，远低 45。
    stroke = [(10.0 + i * 0.45, 10.0 + (i % 2) * 40.0) for i in range(100)]
    report = paper_risk.evaluate_gcode(_gcode([stroke]), pen_lifts=1)
    assert report.scribble_core_hits == 0


def test_reject_ink_span() -> None:
    stroke = [(10.0 + i * 2.0, 10.0) for i in range(10)]
    with pytest.raises(paper_risk.PaperRiskReject) as ei:
        paper_risk.enforce_gcode(_gcode([stroke]), pen_lifts=1)
    assert ei.value.reason == "ink_span"
    assert ei.value.report.ink_span_mm < paper_risk.MIN_INK_SPAN_MM


def test_pen_lifts_matches_validate_gcode() -> None:
    """状态机沿计数必须与 §5 validate_gcode 的抬笔统计一致（同一生产模板）。"""
    strokes = [
        [(10.0, 10.0), (50.0, 10.0), (50.0, 50.0)],
        [(51.0, 50.0), (60.0, 50.0)],
        [(61.0, 50.0), (70.0, 50.0), (70.0, 70.0), (30.0, 70.0)],
    ]
    g = _gcode(strokes)
    stats = validate_gcode(g)
    report = paper_risk.evaluate_gcode(g, pen_lifts=stats.pen_lifts)
    # validate_gcode 数 Z0.0 行：头两次幂等抬笔 + 每笔 line_end + document_end。
    assert stats.pen_lifts == 6
    assert report.pen_lifts == 6
    assert report.g1_xy_segments == 2 + 1 + 3
    assert report.reason is None


# ---------- _run_draw_job 重试环（Task 5） ----------

_BASE_URL = "http://127.0.0.1:8300"
_AI_SVG = '<svg xmlns="http://www.w3.org/2000/svg"><path d="M3 3 L4 4"/></svg>'
_REJECT_GCODE = _gcode([[(5.0 + i, 10.0), (45.0 + i, 10.0)] for i in range(45)])
_PASS_GCODE = _gcode([[(10.0 + i * 9.0, 20.0) for i in range(20)]])


class _SlotSpy:
    def __init__(self):
        self.entries = 0

    async def __aenter__(self):
        self.entries += 1

    async def __aexit__(self, *exc):
        return False


def _job_settings(tmp_path, gate: bool = True):
    from types import SimpleNamespace

    return SimpleNamespace(
        output_dir=tmp_path,
        output_ttl_seconds=600,
        xiaozhi_device_token="",
        paper_risk_gate=gate,
    )


def _stub_job(monkeypatch, tmp_path, gcode_queue, attempts, gate=True):
    from types import SimpleNamespace

    from hutuji_mcp import curated, quickdraw, server
    from hutuji_mcp.bitmap_svg import BitmapResult

    monkeypatch.setattr(server, "settings", _job_settings(tmp_path, gate))
    monkeypatch.setattr(curated, "is_replace_active", lambda: False)
    monkeypatch.setattr(quickdraw, "lookup", lambda d: None)

    async def fake_generate(desc, *, attempt=0):
        attempts.append(attempt)
        return BitmapResult(svg=_AI_SVG, preview_png=b"ai")

    monkeypatch.setattr(server, "generate_bitmap", fake_generate)

    async def fake_gcode(svg, svg_path, gcode_path):
        text = gcode_queue.pop(0)
        gcode_path.write_text(text, encoding="utf-8")
        lifts = text.count("G1G90 Z0.0F10000")
        return SimpleNamespace(lines=10, bytes=len(text), pen_lifts=lifts)

    monkeypatch.setattr(server, "_svg_to_gcode_cached", fake_gcode)
    slot_spy = _SlotSpy()
    monkeypatch.setattr(server, "_draw_limiter", SimpleNamespace(slot=lambda: slot_spy))
    return server, slot_spy


def test_draw_job_retries_on_reject_then_passes(monkeypatch, tmp_path):
    """前两轮 Reject、第三轮 Pass：最终有 URL 且生成调用 3 次、名额只占 1。"""
    attempts: list[int] = []
    server, slot_spy = _stub_job(
        monkeypatch, tmp_path, [_REJECT_GCODE, _REJECT_GCODE, _PASS_GCODE], attempts
    )

    payload = json.loads(asyncio.run(server._run_draw_job("纸面闸重试", _BASE_URL)))

    assert attempts == [0, 1, 2]
    assert slot_spy.entries == 1
    assert payload["url"].endswith(".gcode")
    name = payload["url"].rsplit("/", 1)[-1]
    assert (tmp_path / name).exists()


def test_draw_job_exhausted_cleans_artifacts(monkeypatch, tmp_path):
    """三轮全 Reject：抛 PaperRiskExhaustedError 且无残留产物文件。"""
    attempts: list[int] = []
    server, slot_spy = _stub_job(
        monkeypatch,
        tmp_path,
        [_REJECT_GCODE] * 3,
        attempts,
    )

    with pytest.raises(server.PaperRiskExhaustedError, match="画糊"):
        asyncio.run(server._run_draw_job("纸面闸耗尽", _BASE_URL))

    assert attempts == [0, 1, 2]
    assert slot_spy.entries == 1
    assert list(tmp_path.iterdir()) == [], "耗尽不得残留半成品产物"



def test_reject_evicts_attempt_bitmap_cache(monkeypatch, tmp_path):
    """拒收位图不得留在缓存（生产实测：三轮全命中缓存、三次同败）。"""
    from types import SimpleNamespace

    from hutuji_mcp import bitmap_svg, curated, quickdraw, server
    from hutuji_mcp.bitmap_svg import BitmapResult

    monkeypatch.setattr(server, "settings", _job_settings(tmp_path, True))
    monkeypatch.setattr(curated, "is_replace_active", lambda: False)
    monkeypatch.setattr(quickdraw, "lookup", lambda d: None)

    def seed(desc: str, attempt: int) -> None:
        key = desc.strip() + (f"::attempt={attempt}" if attempt else "")
        bitmap_svg._bitmap_cache[key] = (
            bitmap_svg._bitmap_cache_clock(),
            BitmapResult(svg=_AI_SVG, preview_png=b"x"),
        )

    fresh: list[tuple[str, int]] = []

    async def fake_generate(desc, *, attempt=0):
        # 复刻真实 generate_bitmap 的缓存读路径：命中即返回、不再计 fresh——
        # evict 变 no-op 时第二 job attempt=0 会命中（fresh 少 1），RED 可判。
        key = desc.strip() + (f"::attempt={attempt}" if attempt else "")
        cached = bitmap_svg._get_cached_bitmap(key)
        if cached is not None:
            return cached
        fresh.append((desc, attempt))
        seed(desc, attempt)
        return BitmapResult(svg=_AI_SVG, preview_png=b"ai")

    monkeypatch.setattr(server, "generate_bitmap", fake_generate)

    async def fake_gcode(svg, svg_path, gcode_path):
        gcode_path.write_text(_REJECT_GCODE, encoding="utf-8")
        return SimpleNamespace(lines=10, bytes=10, pen_lifts=45)

    monkeypatch.setattr(server, "_svg_to_gcode_cached", fake_gcode)
    monkeypatch.setattr(
        server, "_draw_limiter", SimpleNamespace(slot=lambda: _SlotSpy())
    )
    bitmap_svg._bitmap_cache.clear()

    with pytest.raises(server.PaperRiskExhaustedError):
        asyncio.run(server._run_draw_job("拒收逐出猫", _BASE_URL))

    keys = [k for k in bitmap_svg._bitmap_cache if k.startswith("拒收逐出猫")]
    assert keys == [], f"拒收位图残留缓存：{keys}"

    # 第二个 job（同题重试）：attempt=0 键已被逐出 → 必须真重调（fresh 再 +1）。
    with pytest.raises(server.PaperRiskExhaustedError):
        asyncio.run(server._run_draw_job("拒收逐出猫", _BASE_URL))
    assert fresh.count(("拒收逐出猫", 0)) == 2, (
        f"逐出后同题重试必须真重生 attempt=0（no-op 时第二 job 会命中缓存），"
        f"实际 fresh {fresh}"
    )
    bitmap_svg._bitmap_cache.clear()


def test_draw_job_gate_disabled_single_attempt(monkeypatch, tmp_path, caplog):
    """HUTUJI_PAPER_RISK_GATE=0：即使 G-code 会 Reject 也只跑一轮，但照记
    shadow 指标日志（攒证据供重钉阈值）。"""
    import logging

    attempts: list[int] = []
    server, slot_spy = _stub_job(
        monkeypatch, tmp_path, [_REJECT_GCODE], attempts, gate=False
    )

    with caplog.at_level(logging.INFO):
        payload = json.loads(asyncio.run(server._run_draw_job("闸关闭", _BASE_URL)))

    assert attempts == [0]
    assert payload["url"].endswith(".gcode")
    shadow = [r.getMessage() for r in caplog.records if "paper_risk shadow" in r.getMessage()]
    assert shadow, "shadow 模式必须照记指标日志"
