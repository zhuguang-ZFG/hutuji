"""protocol v0.5 与公网安全基础契约的无硬件回归。"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import zlib
from pathlib import Path

import pytest
from fastapi import HTTPException

from hutuji_mcp.config import Settings
from hutuji_mcp.gcode_gen import (
    MAX_BYTES,
    PEN_LIFT_WARN_THRESHOLD,
    GcodeError,
    validate_gcode,
)
from hutuji_mcp import server


def _settings(output_dir: Path, *, ttl: int = 600) -> Settings:
    return Settings(
        llm_base_url="https://example.invalid/v1",
        llm_api_key="",
        llm_model="test",
        output_dir=output_dir,
        public_base_url="https://draw.example",
        host="127.0.0.1",
        port=8300,
        output_ttl_seconds=ttl,
    )


def _minimal_gcode(extra: str = "") -> str:
    return f"G21\nG90\nF1500\n{extra}"


def test_gcode_rejects_more_than_512_kib() -> None:
    text = _minimal_gcode(";" + "x" * MAX_BYTES)
    with pytest.raises(GcodeError, match=r"总大小 .*实际|总大小 .*字节"):
        validate_gcode(text)


def test_pen_lifts_are_reported_but_not_rejected(caplog: pytest.LogCaptureFixture) -> None:
    text = _minimal_gcode("M5\n" * (PEN_LIFT_WARN_THRESHOLD + 1))
    with caplog.at_level(logging.WARNING):
        stats = validate_gcode(text)
    assert stats.pen_lifts == PEN_LIFT_WARN_THRESHOLD + 1
    assert "仍放行" in caplog.text


def test_capability_download_has_integrity_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    name = f"{server._new_basename()}.gcode"
    data = b"G21\nG90\n"
    (tmp_path / name).write_bytes(data)

    response = asyncio.run(server._serve_file(name))

    assert response.headers["content-length"] == str(len(data))
    assert response.headers["x-hutuji-crc32"] == f"{zlib.crc32(data) & 0xFFFFFFFF:08x}"
    assert response.headers["cache-control"] == "private, no-store"


def test_legacy_or_guessable_filename_is_not_public(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    name = "draw_20260725_120000.gcode"
    (tmp_path / name).write_text("G21\nG90\n", encoding="utf-8")
    with pytest.raises(HTTPException) as caught:
        asyncio.run(server._serve_file(name))
    assert caught.value.status_code == 404


def test_expired_capability_returns_404_and_deletes_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "settings", _settings(tmp_path, ttl=600))
    base = server._new_basename()
    gcode = tmp_path / f"{base}.gcode"
    svg = tmp_path / f"{base}.svg"
    gcode.write_text("G21\nG90\n", encoding="utf-8")
    svg.write_text("<svg/>", encoding="utf-8")
    old = time.time() - 601
    os.utime(gcode, (old, old))
    os.utime(svg, (old, old))

    with pytest.raises(HTTPException) as caught:
        asyncio.run(server._serve_file(gcode.name))

    assert caught.value.status_code == 404
    assert not gcode.exists()
    assert not svg.exists()


def test_draw_limiter_rejects_rate_and_concurrency() -> None:
    now = [0.0]
    limiter = server.DrawLimiter(clock=lambda: now[0])

    asyncio.run(limiter.enter())
    with pytest.raises(server.DrawLimitError, match="每秒"):
        asyncio.run(limiter.enter())

    now[0] = 1.1
    asyncio.run(limiter.enter())
    now[0] = 2.2
    with pytest.raises(server.DrawLimitError, match="并发"):
        asyncio.run(limiter.enter())

    asyncio.run(limiter.leave())
    asyncio.run(limiter.leave())


def test_status_latest_filename_is_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    base = server._new_basename()
    name = f"{base}.gcode"
    (tmp_path / name).write_text("G21\nG90\n", encoding="utf-8")
    # basename 含 token；status 不得回显完整 capability 名。
    # token 是 URL-safe base64，本身可能含 `_`，故按结构取而不是 rsplit("_")
    # ——旧写法会在约 29% 的随机 token 上取到残缺 token，让本断言时好时坏。
    token = server._DRAW_FILE_PARTS_RE.fullmatch(name)["token"]
    assert len(token) == 22

    payload = asyncio.run(server.hutuji_status())
    import json

    data = json.loads(payload)
    assert data["gcode_files"] == 1
    assert data["latest"] is not None
    assert token not in data["latest"]
    assert data["latest"].startswith("draw_")
    assert data["latest"].endswith(".gcode")
    assert "*" in data["latest"]


def test_draw_limiter_rejects_minute_window() -> None:
    now = [0.0]
    limiter = server.DrawLimiter(
        per_second=100,
        per_minute=2,
        max_concurrent=100,
        clock=lambda: now[0],
    )
    asyncio.run(limiter.enter())
    asyncio.run(limiter.enter())
    with pytest.raises(server.DrawLimitError, match="每分钟"):
        asyncio.run(limiter.enter())



# ---- §5 校验器绕过回归（2026-07-26 深度评审）----
# 这些写法曾全部被放行：指令识别依赖空格切分，坐标正则不认 `1e3` / `.5`,
# 且 G2/G3 圆弧不限幅。校验器是下发前最后一道闸，不能只对 vpype 当前输出成立。


@pytest.mark.parametrize(
    "line, reason",
    [
        ("G0 X0 Y0", "自动换纸"),
        ("G0X0Y0", "自动换纸"),          # 无空格：整行曾是一个 token
        ("G90 G0 X0 Y0", "自动换纸"),    # 模态前缀：首个 token 曾是 G90
        ("g0x0y0", "自动换纸"),          # 小写 + 无空格
        ("G0X0Y0Z0", "自动换纸"),
        ("G28X1", "G28/G30"),
        ("G1 X1e3 Y5", "越界"),          # 科学计数法曾被截成 X1
        ("G1 X.5e3 Y5", "越界"),         # 前导小数点 + 指数
        ("G2 X999 Y999 I1 J1", "越界"),  # 圆弧曾完全不限幅
        ("G3 X5 Y999 I1 J1", "越界"),
        ("G1 X-5 Y5", "越界"),
        # 以下为 2026-07-26 二次评审新增（都是我引入 _WORD_RE 时的遗留缺陷）：
        ("G1E400 X5 Y5", "非有限"),       # int(float('1E400'))=int(inf) 曾抛 OverflowError（非 GcodeError）
        ("M1E400", "非有限"),
        ("G1 X1E400 Y5", "越界"),         # inf 坐标应判越界，不得崩栈
        ("G1 XNAN Y5", "无法解析"),       # NAN 曾让该轴坐标被静默丢弃 → 限幅失效
        ("G1 XINF Y5", "无法解析"),
        ("G1 X1.5.5 Y5", "无法解析"),     # 残缺写法曾被截成 1.5 静默放行
        ("G1 X Y5", "无法解析"),          # 缺值
        # 换纸/程序结束 M 指令必须拦截
        ("M30", "换纸/结束"),             # 程序结束触发自动换纸
        ("M2", "换纸/结束"),              # 程序结束
        ("M701", "换纸/结束"),            # 换纸系统指令
        ("M704", "换纸/结束"),
        ("M711", "换纸/结束"),
        ("M712", "换纸/结束"),
        ("M713", "换纸/结束"),
        ("M721", "换纸/结束"),
        ("G1 X50 Y50 M30", "换纸/结束"), # M30 粘在运动指令后面
    ],
)
def test_gcode_rejects_bypass_variants(line: str, reason: str) -> None:
    with pytest.raises(GcodeError, match=reason):
        validate_gcode(_minimal_gcode(line + "\n"))


def test_malformed_gcode_never_raises_non_gcode_error() -> None:
    """闸门只应抛 GcodeError；任何畸形输入都不得漏出 OverflowError/ValueError 等，
    否则会穿透 svg_to_gcode 变成 hutuji_draw 的未捕获 500。"""
    import re as _re
    for line in ["G1E400", "M9E999", "G1 X1E400 Y1E400", "G9999999999999999999 X5 Y5"]:
        try:
            validate_gcode(_minimal_gcode(line + "\n"))
        except GcodeError:
            pass  # 预期内的拒绝
        except Exception as exc:  # noqa: BLE001 —— 正是要证明这里抓不到东西
            raise AssertionError(f"{line!r} 漏出非 GcodeError 异常：{type(exc).__name__}: {exc}")


@pytest.mark.parametrize(
    "line",
    [
        "G0 X95.000 Y175.000 F2500",  # vpype segment_first 实际形状
        "G1 X100.000 Y100.000 F1500",
        "G1 X.5 Y.5",                 # 前导小数点但在范围内
        "G1 X1e-3 Y5",                # 负指数 = 0.001mm
        "G4 P0.2",
        "M5",
        "; comment",
    ],
)
def test_gcode_accepts_legit_lines(line: str) -> None:
    validate_gcode(_minimal_gcode(line + "\n"))


# 圆弧：端点在界内不代表整条弧在界内——弧中途会凸到象限点。
# 每例都先用 G0 给出起点，否则校验器会以「起点未知」拒绝。
def test_arc_bulge_outside_limit_is_rejected() -> None:
    """两端点都在界内，但 G2（顺时针）走上半圈，中途凸到 Y=195 > 190。
    只看端点会放行这条弧，实机就是撞机。"""
    text = _minimal_gcode("G0 X100 Y185\nG2 X120 Y185 I10 J0\n")
    with pytest.raises(GcodeError, match="圆弧 [XY] 包围盒"):
        validate_gcode(text)


def test_arc_fully_inside_limit_passes() -> None:
    # 同一条弧整体下移：凸点 Y=110，稳在界内
    text = _minimal_gcode("G0 X100 Y100\nG2 X120 Y100 I10 J0\n")
    stats = validate_gcode(text)
    assert stats.lines == 5


def test_arc_opposite_direction_bulges_the_other_way() -> None:
    """同样的起终点与 I/J，G3 走下半圈——凸向 Y=175，仍在界内。
    方向决定凸向，故限幅必须按实际扫过的角度区间算，不能取外接正方形。"""
    stats = validate_gcode(_minimal_gcode("G0 X100 Y185\nG3 X120 Y185 I10 J0\n"))
    assert stats.lines == 5


def test_arc_without_known_start_is_rejected() -> None:
    """圆弧的包围盒依赖起点；前面没有定位指令时必须拒，不能猜 (0,0)。"""
    with pytest.raises(GcodeError, match="缺少已知起点"):
        validate_gcode(_minimal_gcode("G2 X50 Y50 I5 J5\n"))


def test_arc_with_inconsistent_radius_is_rejected() -> None:
    """I/J 给出的圆心到起点、终点半径不一致 —— Grbl 自身也会报 invalid target
    （`GCode.cpp` delta_r > 0.5mm）。校验器须在落笔前拦住，否则包围盒本身就失真。

    起点不能用 (0,0)：那会先撞上 G0 X0 Y0 换纸禁令，测不到圆弧这条。"""
    with pytest.raises(GcodeError, match="半径不自洽"):
        validate_gcode(_minimal_gcode("G0 X10 Y10\nG2 X100 Y100 I1 J1\n"))


def test_mid_file_g91_is_rejected() -> None:
    """位置跟踪假定 G90 绝对坐标；中途切 G91 会让此后所有圆弧起点失真。"""
    with pytest.raises(GcodeError, match="G91"):
        validate_gcode(_minimal_gcode("G0 X10 Y10\nG91\nG1 X5 Y5\n"))


def test_pen_lift_counts_glued_and_padded_forms() -> None:
    # M5 / M05 / 与前一个字粘连的 M5 都要计数，M3 落笔不算
    stats = validate_gcode(_minimal_gcode("M5\nM05\nG4P0.2M5\nM3\n"))
    assert stats.pen_lifts == 3


@pytest.mark.parametrize(
    "token",
    [
        "xDNlk73FAJubla7zE_HDGg",   # 下划线在尾部
        "_iIT-11Xvigmh8ODcPG20Q",   # 下划线在首位
        "w2PMOJF_LKJ6XT-EB6DVxg",   # 下划线在中间
        "SlIdJ_-X4lLi25a4F1oR2g",   # 下划线紧跟连字符
    ],
)
def test_redaction_never_leaks_token_containing_underscore(token: str) -> None:
    """token 用 URL-safe base64，含 `_` 是常态（约 29%）。
    脱敏必须按结构切，不能靠最后一个下划线，否则 status 会回显 token 前缀。"""
    name = f"draw_20260726_010101_{token}.gcode"
    redacted = server._redact_capability_name(name)
    assert redacted == "draw_20260726_010101_*.gcode"
    assert token not in redacted
    # 逐段确认没有残留任何非平凡 token 片段
    for size in range(4, len(token) + 1):
        assert token[:size] not in redacted


def test_redaction_preserves_extension_and_rejects_unknown_names() -> None:
    base = "draw_20260726_010101_" + "a" * 22
    assert server._redact_capability_name(f"{base}.svg").endswith("_*.svg")
    assert server._redact_capability_name(f"{base}.gcode").endswith("_*.gcode")
    # 不符合 capability 命名的一律退化为通配，不回显原名
    for bad in ("../../etc/passwd", "draw_1_2_short.gcode", "random.gcode"):
        assert server._redact_capability_name(bad) == "*.gcode"
