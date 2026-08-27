"""抽稀地板、排线簇抑制与 attempt 破缓存（2026-08-27 纸面闸批）。

地板语义：tol 把整幅抽到 <80 点或 <8% 原点数时逐级减半 tol 重抽（下限
0.3px），仍不足保留最温和一档——绝不回退未抽稀（阶梯致密 G1 病理）。
"""
from __future__ import annotations

import asyncio

import numpy as np

from hutuji_mcp import bitmap_svg


def _staircase(n: int) -> np.ndarray:
    xs = np.arange(n, dtype=float)
    ys = (xs % 2).astype(float)
    return np.column_stack([xs, ys])


def test_decimate_floor_skips_when_collapse_too_hard() -> None:
    """大 tol 把 400 点阶梯抽成两端点时，地板必须逐级降 tol 保住 ≥80 点，
    且确实抽稀过（< 原点数）——不回退未抽稀、不死守塌缩结果。"""
    stair = _staircase(400)
    out = bitmap_svg._decimate_polylines_with_floor([stair], 50.0)
    total = sum(len(p) for p in out)
    assert total >= bitmap_svg._DECIMATE_MIN_POINTS_AFTER
    assert total < 400, "地板不得回退未抽稀结果"


def test_decimate_floor_steps_tol_down_not_raw_revert() -> None:
    """地板命中时结果必须等于某档更温和 tol 的抽稀输出（逐级减半链上），
    而不是原始点列。"""
    stair = _staircase(400)
    out = bitmap_svg._decimate_polylines_with_floor([stair], 50.0)
    total = sum(len(p) for p in out)
    candidates = []
    eps = 50.0
    while eps >= bitmap_svg._DECIMATE_TOL_FLOOR_PX - 1e-9:
        cand = bitmap_svg._rdp_polylines([stair], eps)
        candidates.append(sum(len(p) for p in cand))
        eps = max(eps / 2.0, bitmap_svg._DECIMATE_TOL_FLOOR_PX)
        if eps == bitmap_svg._DECIMATE_TOL_FLOOR_PX:
            break
    assert total in candidates, f"地板结果 {total} 不在逐级 tol 链 {candidates}"
    raw = bitmap_svg._rdp_polylines([stair], 50.0)
    assert total != sum(len(p) for p in raw) or total >= 80

def test_decimate_floor_inactive_for_large_inputs() -> None:
    t = np.linspace(0, 120 * np.pi, 2000)
    wave = np.column_stack([np.arange(2000, dtype=float), 50 + 10 * np.sin(t)])
    floored = bitmap_svg._decimate_polylines_with_floor([wave], 1.5)
    plain = bitmap_svg._rdp_polylines([wave], 1.5)
    plain_total = sum(len(p) for p in plain)
    assert plain_total >= 80, "前提：波形在生产 tol 下不应塌缩"
    assert sum(len(p) for p in floored) == plain_total


def test_suppress_hatch_clusters_drops_dense_parallel_short() -> None:
    """12 条近同向并排短段（排线填色形态）整簇丢弃。"""
    hatches = [
        np.array([[10.0 + i * 3.0, 10.0], [10.0 + i * 3.0 + 2.0, 30.0]], dtype=float)
        for i in range(12)
    ]
    out = bitmap_svg._suppress_hatch_clusters(hatches)
    assert out == [], "排线簇必须整簇丢弃"


def test_suppress_hatch_clusters_keeps_long_and_sparse() -> None:
    """长笔画与独立短特征不进簇：原样保留。"""
    long_stroke = np.array(
        [[0.0, 0.0], [50.0, 0.0], [50.0, 50.0], [0.0, 50.0]], dtype=float
    )
    features = [
        np.array([[100.0 + i * 20.0, 100.0], [105.0 + i * 20.0, 105.0]], dtype=float)
        for i in range(3)
    ]
    out = bitmap_svg._suppress_hatch_clusters([long_stroke, *features])
    assert len(out) == 4


def test_suppress_hatch_clusters_bucket_isolates_directions() -> None:
    """7+7 两个方向各自不足 8 条时不删；角度桶隔离不跨向并簇。"""
    vertical = [
        np.array([[10.0 + i * 3.0, 10.0], [10.0 + i * 3.0, 30.0]], dtype=float)
        for i in range(7)
    ]
    horizontal = [
        np.array([[100.0, 10.0 + i * 3.0], [120.0, 10.0 + i * 3.0]], dtype=float)
        for i in range(7)
    ]
    out = bitmap_svg._suppress_hatch_clusters(vertical + horizontal)
    assert len(out) == 14


def test_trace_pipeline_order_stitch_hatch_decimate(monkeypatch) -> None:
    """trace 内顺序钉死：sknw→stitch→hatch 抑制→抽稀地板（探针 PNG 真跑）。"""
    calls: list[str] = []
    real_stitch = bitmap_svg._stitch_polylines
    real_hatch = bitmap_svg._suppress_hatch_clusters
    real_dec = bitmap_svg._decimate_polylines_with_floor

    def stitch(polys, tol):
        calls.append("stitch")
        return real_stitch(polys, tol)

    def hatch(polys):
        calls.append("hatch")
        return real_hatch(polys)

    def dec(polys, tol):
        calls.append("decimate")
        return real_dec(polys, tol)

    monkeypatch.setattr(bitmap_svg, "_stitch_polylines", stitch)
    monkeypatch.setattr(bitmap_svg, "_suppress_hatch_clusters", hatch)
    monkeypatch.setattr(bitmap_svg, "_decimate_polylines_with_floor", dec)

    bitmap_svg.trace_bitmap_to_polylines(bitmap_svg._encode_probe_png())
    assert calls == ["stitch", "hatch", "decimate"]


def test_generate_bitmap_attempt_busts_cache(monkeypatch) -> None:
    """attempt>0 必须破缓存真重跑生图；同 attempt 复用缓存不重复付费。"""
    bitmap_svg._bitmap_cache.clear()
    bitmap_svg._bitmap_inflight.clear()
    calls = {"n": 0}

    async def fake_fetch(_desc: str) -> bytes:
        calls["n"] += 1
        # 每轮不同像素：attempt 间结果必须可区分
        return _probe_png_shifted(calls["n"])

    monkeypatch.setattr(bitmap_svg, "_fetch_image_png", fake_fetch)

    first = asyncio.run(bitmap_svg.generate_bitmap("纸面闸破缓存"))
    again = asyncio.run(bitmap_svg.generate_bitmap("纸面闸破缓存"))
    assert again is first or again == first
    assert calls["n"] == 1, "attempt=0 同题必须命中缓存"

    second = asyncio.run(bitmap_svg.generate_bitmap("纸面闸破缓存", attempt=1))
    assert calls["n"] == 2, "attempt=1 必须破缓存真重跑生图"
    assert second.svg != first.svg or second.preview_png != first.preview_png

    second_again = asyncio.run(bitmap_svg.generate_bitmap("纸面闸破缓存", attempt=1))
    assert calls["n"] == 2, "同 attempt 重试应命中 attempt 缓存，不再付费"
    bitmap_svg._bitmap_cache.clear()
    bitmap_svg._bitmap_inflight.clear()


def _probe_png_shifted(shift: int) -> bytes:
    """探针 ⊥ 形水平平移 shift px：保证不同 attempt 描摹结果不同。"""
    import zlib
    from io import BytesIO

    size = bitmap_svg._PROBE_SIZE_PX
    stroke = bitmap_svg._PROBE_STROKE_PX
    img = np.ones((size, size), dtype=np.uint8) * 255
    x0 = 200 + shift * 7
    img[600:600 + stroke, x0:x0 + 400] = 0
    img[200:600, x0 + 190:x0 + 190 + stroke] = 0
    raw = b"".join(b"\x00" + img[y].tobytes() for y in range(size))
    ihdr = size.to_bytes(4, "big") * 2 + b"\x08\x00\x00\x00\x00"
    buf = BytesIO()
    buf.write(b"\x89PNG\r\n\x1a\n")
    buf.write(bitmap_svg._png_chunk(b"IHDR", ihdr))
    buf.write(bitmap_svg._png_chunk(b"IDAT", zlib.compress(raw, 6)))
    buf.write(bitmap_svg._png_chunk(b"IEND", b""))
    return buf.getvalue()
