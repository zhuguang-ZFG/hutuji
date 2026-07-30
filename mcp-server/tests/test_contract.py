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
    return f"G21\nG90\nF8000\n{extra}"


def test_gcode_rejects_more_than_512_kib() -> None:
    text = _minimal_gcode(";" + "x" * MAX_BYTES)
    with pytest.raises(GcodeError, match=r"总大小 .*实际|总大小 .*字节"):
        validate_gcode(text)


def test_pen_lifts_are_reported_but_not_rejected(caplog: pytest.LogCaptureFixture) -> None:
    # 抬笔 = Z 回 0 的运动（对齐奎享 Stepper 产物，本 profile 不产 M5）
    text = _minimal_gcode("G1G90 Z0.0F10000\n" * (PEN_LIFT_WARN_THRESHOLD + 1))
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


# ---- 2026-07-26 安全审计修复回归 ----


def test_serve_file_returns_response_not_file_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1 TOCTOU 修复：_serve_file 返回 Response（内存发送），不再是 FileResponse。"""
    from fastapi.responses import Response as _Resp

    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    name = f"{server._new_basename()}.gcode"
    (tmp_path / name).write_bytes(b"G21\nG90\n")
    resp = asyncio.run(server._serve_file(name))
    assert isinstance(resp, _Resp)
    assert resp.body == b"G21\nG90\n"
    assert resp.headers.get("content-disposition") == "attachment"


def test_serve_file_concurrent_delete_returns_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1 TOCTOU：文件在 is_file() 检查后、read_bytes() 前被删除，应返回 404。"""
    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    name = f"{server._new_basename()}.gcode"
    path = tmp_path / name
    path.write_bytes(b"G21\nG90\n")
    path.unlink()
    with pytest.raises(HTTPException) as caught:
        asyncio.run(server._serve_file(name))
    assert caught.value.status_code == 404


def test_status_does_not_leak_absolute_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#5：status 不泄露服务端绝对路径。"""
    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    import json as _json

    data = _json.loads(asyncio.run(server.hutuji_status()))
    assert "output_dir" not in data
    assert "output_dir_exists" in data


def test_svg_size_limit() -> None:
    """#2：SVG 超过 256KB 应被拒绝。"""
    from hutuji_mcp.llm_svg import LlmError, MAX_SVG_BYTES, _extract_svg

    giant = '<svg xmlns="http://www.w3.org/2000/svg">' + "x" * (MAX_SVG_BYTES + 1000) + "</svg>"
    with pytest.raises(LlmError, match="256KB"):
        _extract_svg(giant)


def test_svg_deep_nesting_rejected() -> None:
    """#7：深嵌套 SVG 应被拒绝，不得触发 RecursionError。"""
    from hutuji_mcp.llm_svg import LlmError, _extract_svg

    layers = '<g fill="none">' * 100
    close = "</g>" * 100
    svg = f'<svg xmlns="http://www.w3.org/2000/svg">{layers}{close}</svg>'
    with pytest.raises(LlmError, match="嵌套深度"):
        _extract_svg(svg)


def test_vpype_stderr_not_in_error_message(tmp_path: Path) -> None:
    """#4：vpype 失败时 stderr 不应泄露给调用方——消息只含 exit code。"""
    from hutuji_mcp.gcode_gen import GcodeError, svg_to_gcode

    bad_svg = tmp_path / "bad.svg"
    bad_svg.write_text("<not-svg/>", encoding="utf-8")
    out_gcode = tmp_path / "out.gcode"
    with pytest.raises(GcodeError, match=r"exit \d+") as caught:
        svg_to_gcode(bad_svg, out_gcode)
    msg = str(caught.value)
    assert "/" not in msg and "\\" not in msg, f"stderr 泄露了路径信息：{msg}"


def test_vpype_pipeline_keeps_simplify_and_merge(tmp_path: Path, monkeypatch) -> None:
    """回归：管线必须含 linemerge + linesimplify，且顺序在 gwrite 之前。

    这两步是「笔走停停」修复的实体（commit 2aead01）：SVG 曲线离散成 0.12mm
    步长的密点，逐行流控下每行执行 0.9ms（0.12mm @ 生产 F8000=133mm/s）
    < Telnet 往返 ~25ms，电机干等。
    若有人清理管线时删掉这两行，其余测试照样全绿，卡顿静默回归。
    """
    import subprocess as _sp

    from hutuji_mcp import gcode_gen

    captured: dict = {}

    def fake_run(cmd, **kwargs):  # noqa: ANN001, ANN202
        captured["cmd"] = cmd
        # 伪造 vpype 成功：往输出路径写一份最小合法 G-code
        Path(cmd[-1]).write_text("G21\nG90\nF8000\n", encoding="utf-8")
        return _sp.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(gcode_gen.subprocess, "run", fake_run)
    svg = tmp_path / "in.svg"
    svg.write_text("<svg/>", encoding="utf-8")
    gcode_gen.svg_to_gcode(svg, tmp_path / "out.gcode")

    cmd = captured["cmd"]
    for step in ("linemerge", "linesort", "linesimplify", "gwrite"):
        assert step in cmd, f"vpype 管线缺少 {step}"
    order = [cmd.index(s) for s in ("linemerge", "linesort", "linesimplify", "gwrite")]
    assert order == sorted(order), f"管线顺序被破坏：{cmd}"
    # 容差必须显式给出（默认容差未必等于已验证的 0.05/0.3mm）
    assert cmd[cmd.index("linesimplify") + 1] == "--tolerance"
    assert cmd[cmd.index("linemerge") + 1] == "--tolerance"
    # 数值也要锁：只断言「有 --tolerance」的话，把容差改成 5mm 照样全绿
    assert cmd[cmd.index("linesimplify") + 2] == f"{gcode_gen.LINESIMPLIFY_TOLERANCE_MM}mm"
    assert cmd[cmd.index("linemerge") + 2] == f"{gcode_gen.LINEMERGE_TOLERANCE_MM}mm"


def test_tolerances_are_pinned_to_verified_values() -> None:
    """容差直接决定行数与画质，改动必须是显式决策而非顺手调参。

    0.05mm：S3 已落地 512 字节窗口化流控（`15f73c9`），往返不再逐行串行，
    故从 0.1mm 回降，向奎享 0.02mm 画质靠拢。0.3mm：对齐奎享 nearDst。
    """
    from hutuji_mcp import gcode_gen

    assert gcode_gen.LINESIMPLIFY_TOLERANCE_MM == 0.05
    assert gcode_gen.LINEMERGE_TOLERANCE_MM == 0.3


def test_gwrite_profile_matches_production_shape() -> None:
    """生产 profile 形状锁定（docs/protocol.md §5，逐字对齐奎享唯一产物）。

    上一条管线回归把 subprocess.run 整个 mock 掉，假产物的头是随手写的最小
    合法 G-code，锁不住真实 profile；真 vpype 只在 scripts/e2e_local.py 跑，
    且只断言「有行数」。故这里直接对 hutuji_gwrite.toml 做字面量断言。
    """
    import tomllib

    from hutuji_mcp import gcode_gen

    toml_path = Path(gcode_gen.__file__).parent / "hutuji_gwrite.toml"
    profile = tomllib.loads(toml_path.read_text(encoding="utf-8"))["gwrite"]["hutuji"]

    assert profile["unit"] == "mm"
    assert profile["vertical_flip"] is True
    # 头：G21/G90/F8000 + 两次幂等抬笔（不建立 Z 基准，G92 由 S3 旁路发送）
    assert profile["document_start"] == "G21\nG90\nF8000\nG1G90 Z0.0F10000\nG1G90 Z0.0F10000\n"
    assert profile["document_start"].count("G1G90 Z0.0F10000") == 2
    # 空走 G0 F8000 → 落笔 Z5；画线 G1 F8000；抬笔 Z0
    assert profile["segment_first"] == "G0 X{x:.3f}Y{y:.3f}F8000\nG1G90 Z5.0F10000\n"
    assert profile["segment"] == "G1 X{x:.3f}Y{y:.3f}F8000\n"
    assert profile["line_end"] == "G1G90 Z0.0F10000\n"
    # 收尾只抬笔：奎享的 G90G0 X0Y0 在本机会被判为换纸触发，禁止照抄
    assert profile["document_end"] == "G1G90 Z0.0F10000\n"
    assert "G0" not in profile["document_end"], "收尾不得有 G0 空走"
    for key in ("document_start", "segment_first", "segment", "line_end", "document_end"):
        assert "G92" not in profile[key], f"{key} 不得含 G92（校验器会拒）"
        assert "M" not in profile[key], f"{key} 不得含 M 码（笔控纯 Z 运动）"
        assert "X0Y0" not in profile[key].replace(" ", ""), f"{key} 不得回原点"


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
        ("G1 X1E400 Y5", "非有限"),       # inf 坐标应明确拒绝，不得崩栈
        ("G1 XNAN Y5", "无法解析"),       # NAN 曾让该轴坐标被静默丢弃 → 限幅失效
        ("G1 XINF Y5", "无法解析"),
        ("G1 X1.5.5 Y5", "无法解析"),     # 残缺写法曾被截成 1.5 静默放行
        ("G1 X Y5", "无法解析"),          # 缺值
        # 生产文件采用允许列表：单位/坐标系/停顿/笔控/机器坐标均拒绝
        ("G20", "允许 G0"),                # 英寸模式会把合法数值放大 25.4 倍
        ("G92 X10 Y10", "允许 G0"),        # 改变工件坐标系
        ("G53 G0 X10 Y10", "允许 G0"),     # 机器坐标覆盖
        ("G4 P0.2", "不允许"),             # G4/P 都不属于生产模板
        ("M3", "不允许 M 码"),             # 当前固件会驱动 Z 落笔
        ("M5", "不允许 M 码"),             # 当前固件会驱动 Z 抬笔
        ("!", "无法解析"),                  # 实时 hold 不得混入下载文件
        ("~", "无法解析"),                  # 实时 resume
        ("?", "无法解析"),                  # 实时 status
        ("%", "无法解析"),                  # 程序分隔符
        ("G1 ZNAN", "无法解析"),            # Z 非数值不得静默忽略
        ("F1E400", "非有限"),               # 进给无穷大
        ("G1 Z999", "生产笔位"),            # 笔轴只允许 Z0/Z5
        ("F10001", "进给范围"),             # 当前生产上限 Z 轴 F10000
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
        # 生产 profile 的真实形状：X 与 Y 之间无空格、每行都带 F8000
        # （hutuji_gwrite.toml segment_first/segment；早先这两行写的
        #  F2500/F1500 是 profile 定稿前的旧口径，锁不住真实产物）
        "G0 X95.000Y175.000F8000",
        "G1 X100.000Y100.000F8000",
        "G0 X95.000 Y175.000 F8000",  # 带空格的等价写法同样必须放行
        "G1 X.5 Y.5",                 # 前导小数点但在范围内
        "G1 X1e-3 Y5",                # 负指数 = 0.001mm
        "; comment",
        "; generated by vpype-gcode",  # 注释含 Y 字母，不得误拒
        "G1 X50 Y50 ; move to XY",    # 行内注释含 X/Y
    ],
)
def test_gcode_accepts_legit_lines(line: str) -> None:
    validate_gcode(_minimal_gcode(line + "\n"))


# 圆弧：端点在界内不代表整条弧在界内——弧中途会凸到象限点。
# 每例都先用 G0 给出起点，否则校验器会以「起点未知」拒绝。
def test_arc_bulge_outside_limit_is_rejected() -> None:
    """两端点都在界内，但 G2（顺时针）走上半圈，中途凸到 Y=280 > 277。
    只看端点会放行这条弧，实机就是撞机。"""
    text = _minimal_gcode("G0 X100 Y270\nG2 X120 Y270 I10 J0\n")
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


def test_near_full_circle_arc_checks_all_quadrants() -> None:
    """#8 浮点精度：起终点几乎重合的圆弧应按整圆算包围盒，不能退化为点。
    圆心 (100,100)，半径 95 → X 最大 195 > 190（X 限幅），必须拦住。"""
    text = _minimal_gcode("G0 X100 Y5\nG2 X100.0001 Y5 I0 J95\n")
    with pytest.raises(GcodeError, match="越界"):
        validate_gcode(text)


def test_coordinate_upper_bound_uses_eps_not_1mm() -> None:
    """#9 容差对称：190.5mm 不应放行（旧 +1.0mm 容差会放行）。"""
    with pytest.raises(GcodeError, match="越界"):
        validate_gcode(_minimal_gcode("G1 X190.5 Y5\n"))


def test_coordinate_at_boundary_passes() -> None:
    """X=190 和 Y=277 恰好在各自界内。"""
    validate_gcode(_minimal_gcode("G1 X190 Y277\n"))


def test_y_over_277mm_rejected() -> None:
    """Y=277.5 超出 Y 轴限幅 277mm。"""
    with pytest.raises(GcodeError, match="越界"):
        validate_gcode(_minimal_gcode("G1 X5 Y277.5\n"))


def test_mid_file_g91_is_rejected() -> None:
    """位置跟踪假定 G90 绝对坐标；中途切 G91 会让此后所有圆弧起点失真。"""
    with pytest.raises(GcodeError, match="G91"):
        validate_gcode(_minimal_gcode("G0 X10 Y10\nG91\nG1 X5 Y5\n"))


@pytest.mark.parametrize(
    "text",
    [
        "; G21\nG20\nG90\nG1 X8 Y5\n",   # 注释伪造毫米模式，实际英寸
        "G210\nG900\nG1 X10 Y10\n",       # 子串伪造 G21/G90
        "G21.1\nG90\nG1 X10 Y10\n",      # 非整数码不得截断成 G21
        "G21\nG90.1\nG1 X10 Y10\n",      # 非整数码不得截断成 G90
        "G21\nX10 Y10\nG90\n",          # 运动前尚未建立绝对坐标模式
    ],
)
def test_gcode_requires_exact_g21_g90_before_motion(text: str) -> None:
    with pytest.raises(GcodeError, match="G21/G90|必须是整数|允许 G0"):
        validate_gcode(text)


def test_g0_with_unknown_modal_axis_is_rejected() -> None:
    """首个 G0 只给 X 时 Y 未知，不能证明实际终点不是触发换纸的 (0,0)。"""
    with pytest.raises(GcodeError, match="完整 XY|自动换纸"):
        validate_gcode(_minimal_gcode("G0 X0\n"))


def test_default_modal_axis_only_line_is_limited() -> None:
    """复位后默认模态是 G0；即使文件尚无显式运动码，裸轴字也必须限幅。"""
    with pytest.raises(GcodeError, match="越界"):
        validate_gcode(_minimal_gcode("X500 Y5\n"))


def test_default_modal_bare_origin_is_rejected() -> None:
    """裸 `X0 Y0`（不带 G 字）必须按 G0 判定并拒绝。

    Grbl 复位/上电后的默认运动模式是 G0 而不是 G1：`Grbl_Esp32/src/GCode.h`
    里 `Seek = 0, // G0 (Default: Must be zero)`，modal 结构体归零即 Seek，
    只有 M2/M30 之后才切 G1。所以这一行会被固件按 G0 执行、落到 (0,0)，触发
    自动换纸 + 失能电机（该路径只查 XY，没有一次性门）。校验器初值曾写成 G1，
    这类行会被当成普通画线放行——是能把机器搞坏的静默漏放。
    """
    with pytest.raises(GcodeError, match="自动换纸"):
        validate_gcode(_minimal_gcode("X0 Y0\n"))


def test_explicit_g1_to_origin_is_still_allowed() -> None:
    """只拦 G0 语义的 (0,0)：显式 G1 走到 (0,0) 是普通画线，不走换纸路径。"""
    validate_gcode(_minimal_gcode("G1 X0 Y0\n"))


def test_inherited_modal_g1_axis_only_line_is_limited() -> None:
    """显式 G1 后的裸轴字仍是 G1，不能因本行没有 G 字而绕过限幅。"""
    with pytest.raises(GcodeError, match="越界"):
        validate_gcode(_minimal_gcode("G1 X100 Y100\nX500 Y500\n"))


def test_inherited_modal_g0_origin_is_rejected() -> None:
    """G0 X0 继承上一行 Y=0，实际落点是 (0,0)，会触发固件自动换纸。"""
    with pytest.raises(GcodeError, match="自动换纸"):
        validate_gcode(_minimal_gcode("G1 X50 Y0\nG0 X0\n"))


def test_inherited_modal_arc_checks_bounding_box() -> None:
    """首个 G2 半径 10mm 合法；裸轴字继承 G2 后半径 20mm 凸到 Y=280，必须拒。"""
    text = _minimal_gcode("G0 X100 Y260\nG2 X120 Y260 I10 J0\nX160 Y260 I20 J0\n")
    with pytest.raises(GcodeError, match="圆弧 [XY] 包围盒"):
        validate_gcode(text)


def test_pen_lift_counts_glued_and_padded_forms() -> None:
    # 抬笔 = Z 回 0：紧凑写法、带空格和继承 G1 的裸 Z0 都要计数；
    # 落笔 Z5 与不含 Z 的画线行不算。生产 profile 不用 G0 Z0：G0 实际终点
    # 会继承未知 XY，无法排除固件回原点换纸触发。
    stats = validate_gcode(
        _minimal_gcode(
            "G1G90 Z0.0F10000\nG1 G90 Z0.000 F10000\nZ0\n"
            "G1G90 Z5.0F10000\nG1 X10.000Y10.000F8000\n"
        )
    )
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
