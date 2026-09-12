"""SVG → G-code 转换（vpype + vpype-gcode）与 §5 合同校验。

vpype 管线：read → layout（归一化） → linemerge → linesort → linesimplify → gwrite --profile hutuji
（hutuji profile 见 hutuji_gwrite.toml，格式参考 vpype-gcode README）
转换产物必须再过 validate_gcode() 逐行断言 §5 禁令才允许下发。
"""
from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
import logging
import math
import re
import shutil
import subprocess
import sys
import tempfile
import weakref
from dataclasses import dataclass
from pathlib import Path

_PROFILE_PATH = Path(__file__).parent / "hutuji_gwrite.toml"

# vpype 是外部进程；curated/QD 命中绕过付费 DrawLimiter 后仍必须有资源上界。
# 以事件循环为键懒建，兼容测试/嵌入调用轮换 asyncio loop；生产单 loop 只有一个闸。
# 上限读 HUTUJI_VPYPE_MAX_CONCURRENT（默认 6，与 config.settings 课堂级默认对齐）。
_vpype_semaphores: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, weakref.ReferenceType[asyncio.Semaphore]
] = weakref.WeakKeyDictionary()


def _vpype_max_concurrent() -> int:
    from .config import settings
    from .portal_settings import effective_vpype_max_concurrent

    return effective_vpype_max_concurrent(settings, _vpype_env_default())


def _vpype_env_default() -> int:
    import os

    raw = os.environ.get("HUTUJI_VPYPE_MAX_CONCURRENT", "6").strip() or "6"
    try:
        n = int(raw)
    except ValueError:
        n = 6
    return max(1, min(n, 16))


def _vpype_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    limit = _vpype_max_concurrent()
    semaphore_ref = _vpype_semaphores.get(loop)
    semaphore = semaphore_ref() if semaphore_ref is not None else None
    if semaphore is None or getattr(semaphore, "_hutuji_limit", None) != limit:
        semaphore = asyncio.Semaphore(limit)
        semaphore._hutuji_limit = limit  # type: ignore[attr-defined]
        _vpype_semaphores[loop] = weakref.ref(semaphore)
    return semaphore

# 坐标限幅：A4 纸 210×297mm，四周留 10mm 边距 ⇒ X≤277、Y≤190。
# 轴向真值（2026-09-04 用户实机确认）：X 为长边（297 方向）、Y 为宽边（210 方向）——
# 与用户 08-21 口径一致；`$$` 读到的 $130=210/$131=297 是厂商配置值，与物理轴角色
# 相反（无限位开关，配置值不进安全链；MCP 本限幅是唯一防线）。同日先按 `$$` 数值
# 把 277 放到 Y，用户实机判定后反转到 X——以用户物理观察为准。
# 改这里必须同步 xiaozhi `hutuji_recovery_core.h` 的 kJogEnvelopeMax*Mm（R17-XREPO-01 互钉）。
MAX_X_MM = 277.0
MAX_Y_MM = 190.0
# 撰写页（SVG 坐标系）= 竖版 190 宽 × 277 高（B 案，2026-09-06 晚用户拍板「不要横着写，
# 要竖着写」）：墙上竖装实机当日纸面双 bit 实测 X+ 朝房间下、Y+ 朝房间右、原点墙面左上；
# G-code 层转置（_transpose_gcode_xy：机器 X:=SVG y、Y:=SVG x）把竖版页映成墙面正读——
# 撰写 x→房间右（从左往右写）、y→房间下（行序自上而下）、页左上角起笔恰落原点角。
# 转置后机器 X=SVG y≤277、Y=SVG x≤190，§5 物理限幅（上面的 MAX_*）不变且自动满足。
PAGE_AUTHOR_WIDTH_MM = 190.0
PAGE_AUTHOR_HEIGHT_MM = 277.0
# 布局归一化边距：LLM 无视 prompt 的「居中留边」（2026-08-09 画质探针实测六组
# 题材 bbox 占画布 1.8%~85.5% 乱飞，star 缩到角落、house 顶到硬边零边距），
# 构图必须由确定性几何兜底：vpype layout 把内容等比缩放并居中到
# (MAX_X-2m)×(MAX_Y-2m) 区域。归一化排在 linemerge/linesimplify 之前，
# 两个容差才是最终毫米几何上的绝对值（排在其后会随放大倍数失真）。
# 10.0→5.0（2026-09-10 用户拍板「单张 A4 要充分利用」，量产不换纸机型单页场景）：
# 10mm 边距对画图类是纯浪费（16:9 照片面积利用率 71%→77%）；§5 硬边界 X≤277/Y≤190
# 本身已含 ~20mm 物理余量，纸面夹持区不在版式区。文字排版边距（8mm）不动。
LAYOUT_MARGIN_MM = 5.0
# 仅用于派生几何：圆弧三角函数浮点误差，以及 G0 近原点的保守换纸保护。
# 用户显式输入的 X/Y/Z 不得套此容差；§5 的 X≤277、Y≤190、Z∈{0,5} 是硬边界。
_GEOMETRY_EPS_MM = 0.01
# 正 R 圆弧的最小弦长：低于此值的弧起终点近乎重合，几何上是「近整圆」歧义
#（解出的圆心贴在弦中点旁，正 R 取 minor 一侧圆心会让包围盒退化成弦点而绕过
# 限幅）。vpype 生产管线实测 0 条真实圆弧，这种几何没有合法来源，fail-closed。
_ARC_MIN_CHORD_MM = 0.5
# 整圆判定阈值 = Grbl `Config.h` 的 ARC_ANGULAR_TRAVEL_EPSILON（R10-ARC-01）。
# `MotionControl.cpp` 对 |angular_travel| ≤ 5e-7 的 G2/G3 一律补 2π 当整圆画：
# 校验器若用更小的阈值（旧值 1e-9），(阈值, 5e-7) 区间的弧会被当「原地微动」
# 放行（包围盒退化成端点），固件却画出整圆——圆越大越致命，本机无限位开关，
# 这道校验是唯一防线。阈值必须与固件逐字一致，改动要与 Grbl 侧同步评审。
_ARC_FULL_CIRCLE_EPSILON_RAD = 5e-7

# 按机型的出图方向与安全限幅（2026-09-11 量产无换纸机联调引入）。transpose_xy=None
# 时回落 hutuji_gwrite.toml 的开关（现役 MK38 行为）；max_*_mm=None 时回落上方
# MAX_*_MM 常量——默认行为对全部既有调用方与测试保持不变。
@dataclass(frozen=True)
class MachineProfile:
    transpose_xy: bool | None = None
    max_x_mm: float | None = None
    max_y_mm: float | None = None


# 量产无换纸机（§10.4.15，物理 X≈210 / Y≈297，2026-09-11 用户实机确认）：墙面
# 竖装转置对它是「横竖颠倒 + 出界顶框」的双重错误（实机两案目击），故不转置；
# 机器坐标限幅按物理行程留 5mm 安全边（无 soft limit 可依赖，MCP 限幅是唯一防线，
# 与 MAX_*_MM 同纪律；点动包线同值由 xiaozhi recovery_core 分机型表镜像）。
PROFILE_NOPAPER = MachineProfile(transpose_xy=False, max_x_mm=205.0, max_y_mm=290.0)


def _resolve_limits(profile: MachineProfile | None) -> tuple[float, float]:
    if profile is None:
        return MAX_X_MM, MAX_Y_MM
    return (
        MAX_X_MM if profile.max_x_mm is None else profile.max_x_mm,
        MAX_Y_MM if profile.max_y_mm is None else profile.max_y_mm,
    )

# 单文件大小上限（protocol §5，S3 侧 PSRAM 缓冲硬约束；恰好 512KiB 放行，再多一字节才拒）
MAX_BYTES = 512 * 1024

# 单行 payload 上限（不含行尾换行符）。**254 不是 255**：Grbl_Esp32
# `Protocol.h:34` 定义 `LINE_BUFFER_SIZE 256`，而 `Protocol.cpp:68` 的溢出判定排在
# 换行判定**之前**——
#     if (cl->len == (LINE_BUFFER_SIZE - 1)) return Error::Overflow;   // 先判溢出
#     if (c == '\r' || c == '\n') { ... return Error::Eol; }            // 后判 EOL
# 于是第 255 个 payload 字节能存进缓冲（len 变 255），但紧随的换行符进来时先撞上
# `len == 255` → 回 `error:11`，且 `Protocol.cpp:218` 的 `empty_line()` 把整行丢掉，
# **该行从未进入 `execute_line()`**。即 255 字节的行不是「勉强执行」而是「整行被吃」。
# 逐字转写固件 add_char_to_line + protocol_poll_client 复现（payload → 设备应答/真正执行）：
#   253B → ok / 253B        254B → ok / 254B
#   255B → error:11 / 无     256B → error:11 + ok / 空行
# 上游 grblHAL 把 `LINE_BUFFER_SIZE` 定为 257（`protocol.h:36`，注释 "256 characters
# plus terminator"）正是为了让标称字符数真的可用；Grbl_Esp32 用 256 未加 1，故本机
# 可用 payload 是 254。校验器必须按设备真正能执行的字节数判，宁可从严。
MAX_LINE_PAYLOAD_BYTES = 254

# 抬笔次数目前只做可观测告警；硬阈值须等机头实测后再锁定（README 组 1.3）。
PEN_LIFT_WARN_THRESHOLD = 50

# 线简化容差（mm）：直接决定 G1 行数，从而决定逐行流控下的出图流畅度。
# 机器笔触精度约 0.1mm，0.2mm 在笔宽量级内，肉眼看不出差别。
# 实测同一张图：无简化 9266 行 → 0.1mm 143 行 → 0.2mm 172 行 → 0.5mm 116 行。
# 逐行流控下每行一次 WiFi 往返（~25ms），F24000=400mm/s（2026-09-12 提速批）。
# 拐点在 0.1mm：运动时间(~4.8s)刚好盖过往返(~3.6s)，0.05mm 以下往返反超开始卡顿。
# 奎享靠窗口化流控保留 0.02mm；S3 已落地窗口=512（`15f73c9`），
# 故 S4 把容差从 0.1mm 回降到 0.05mm，靠窗口化吸收往返，向奎享画质靠拢。
LINESIMPLIFY_TOLERANCE_MM = 0.05
# 碎笔画合并容差（mm）：对齐奎享 nearDst=0.3，减少抬落笔次数。
# 合并端点几乎相接的线；0.3mm 内并笔不改变构图（奎享现役值，实测安全）。
LINEMERGE_TOLERANCE_MM = 0.3
# 落笔态最短 G1（mm）：压住本机 `$1=25` + 弹簧笔架空窗。F24000≈400mm/s 时
# 1.0mm≈2.5ms；连续 21 段≈53ms 仍盖过单次 25ms 往返空档。AI 描摹 worst21 曾虚笔，
# curated≈575ms 实笔——同 Z5 字面量，差在段能否撑住 planner（2026-08-28）。
# 不改 $1/Z5 契约；只在 gwrite 之后塌缩落笔短段。笔画末点始终保留。
MIN_PEN_DOWN_G1_MM = 1.0

# G-code 字（字母 + 十进制数值）。整行唯一的解析入口：G/M 字号与 X/Y 坐标都从这里取，
# 避免两套正则口径不一致（旧代码 `\b([XY])` 在 `G0X0Y0` 里因 X 紧跟数字而无词边界，
# 匹配不到坐标，导致 X0Y0 禁令被绕过）。Grbl 解析器不支持科学计数法，故不能接受
# `1e3` 后把与写字机不同的数值下发；剩余内容检查会从严拒绝这类写法。
_WORD_RE = re.compile(r"([A-Z])\s*([+-]?(?:\d+\.?\d*|\.\d+))")

# X/Y 后面必须紧跟一个 Grbl 可解析的有限十进制数。`_WORD_RE` 只挑合法数值，
# `XNAN`、`X `、`X1.5.5` 这类畸形写法会让坐标被静默跳过，越界值就此从限幅里消失
# （静默漏放比误拒危险）。实际拦截分工（2026-08-16 实测，既有测试只验结果不验机制）：
# - `X1.5.5`/`XNAN`/`X `：`_WORD_RE` 只吞得下合法数（`1.5`/空），残段（`.5`/`NAN`/`X`）
#   在**更早的 remainder 检查**就被拒，下面的轴字检查根本走不到；
# - `X1e3`/`X.5e3`/`X1e-1`：`_AXIS_CAND_RE` 的值字符类不含 `e`，停在 `1`/`.5` 判为
#   有限放行——真正拦截它们的是字循环里 `E` 不在 `_ALLOWED_WORD_LETTERS`；
# - 本轴字检查是冗余兜底：任何它想拒的轴值残段都已先被 remainder 吃掉，保留只为
#   纵深防御，不许把它当主防线改。
_AXIS_CAND_RE = re.compile(r"([XY])\s*([0-9+\-.]*)")
_FINITE_NUM_RE = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)$")

# 生产文件允许的 G-code 指令。任务级 M30 由 S3 在 fresh Idle 后单独发送，
# 文件内不允许任何 M 码；G2/G3 仅为外部 G-code / 圆弧限幅兼容保留。
_ALLOWED_G_CODES = frozenset({"G0", "G1", "G2", "G3", "G21", "G90"})
_ALLOWED_WORD_LETTERS = frozenset({"G", "M", "F", "X", "Y", "Z", "I", "J", "R"})

logger = logging.getLogger(__name__)


class GcodeError(RuntimeError):
    """G-code 转换或合同校验失败。"""


_SELF_TEST_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="190mm" height="190mm" viewBox="0 0 190 190">
<path d="M20 20 L30 30 L40 20" fill="none" stroke="black" stroke-width="0.5"/>
</svg>"""


def _run_self_test() -> None:
    """在 systemd ExecStartPre 沙箱内跑一次真实 vpype + profile + §5 校验。"""
    with tempfile.TemporaryDirectory(prefix="hutuji-gcode-selftest-") as tmp:
        svg_path = Path(tmp) / "probe.svg"
        gcode_path = Path(tmp) / "probe.gcode"
        svg_path.write_text(_SELF_TEST_SVG, encoding="ascii")
        stats = svg_to_gcode(svg_path, gcode_path)
        if stats.lines <= 0 or stats.bytes <= 0:
            raise GcodeError("生产生成链自检得到空产物")


def _arc_center(
    x0: float, y0: float, x1: float, y1: float,
    i: float | None, j: float | None, r: float | None,
    clockwise: bool,
) -> tuple[float, float]:
    """求 G2/G3 圆心。复刻 Grbl `GCode.cpp` 的两种模式，保持行为一致。

    - IJ 偏移模式：圆心 = 起点 + (I, J)（`GCode.cpp:1266-1270` 以此反推 target_r）
    - R 半径模式：解出经过起终点、半径为 |R| 的圆心（`GCode.cpp:1216-1251`）；
      **负 R 表示「走超过 180° 的那条弧」**，靠翻转 h 的符号把圆心放到行程线另一侧。
    """
    if r is not None:
        dx, dy = x1 - x0, y1 - y0
        if dx == 0.0 and dy == 0.0:
            # Grbl: R 模式下起终点相同即 GcodeInvalidTarget
            raise GcodeError("G2/G3 半径模式起终点重合（Grbl 会报 invalid target）")
        chord = math.hypot(dx, dy)
        if chord < _ARC_MIN_CHORD_MM:
            # 起终点近乎重合的正 R 弧是「近整圆」歧义：解出的圆心在弦中点旁，正 R
            # 取 minor 一侧圆心，包围盒退化成弦点，会把「绕远路的大弧」误判成
            # 「原地不动」而绕过限幅。vpype 生产管线实测 0 条真实圆弧（arc 校验纯
            # 防御纵深），这种几何歧义没有合法来源，按 fail-closed 拒收。
            raise GcodeError(
                f"G2/G3 半径模式起终点近乎重合（弦长 {chord:.4f}mm < "
                f"{_ARC_MIN_CHORD_MM}mm），近整圆几何歧义一律拒绝"
            )
        h_x2_div_d = 4.0 * r * r - dx * dx - dy * dy
        if h_x2_div_d < 0:
            raise GcodeError(f"G2/G3 半径 {r} 不足以连接起终点（Grbl 会报 arc radius error）")
        h_x2_div_d = -math.sqrt(h_x2_div_d) / math.hypot(dx, dy)
        if not clockwise:
            h_x2_div_d = -h_x2_div_d
        if r < 0:
            h_x2_div_d = -h_x2_div_d
        return x0 + 0.5 * (dx - dy * h_x2_div_d), y0 + 0.5 * (dy + dx * h_x2_div_d)

    # IJ 偏移模式：圆心 = 起点 + (I, J)
    cx, cy = x0 + (i or 0.0), y0 + (j or 0.0)
    # Grbl 会校验「起点到圆心」与「终点到圆心」的半径是否自洽，不自洽即
    # GcodeInvalidTarget（`GCode.cpp:1272-1279`：>0.5mm，或 >0.005mm 且 >0.1% 半径）。
    # 不照做的话，畸形圆弧会在这里算出一个失真的包围盒——可能过大而误拒，也可能
    # 过小而漏放。阈值与固件保持一致，避免云端放行了机器却报 error 的割裂。
    r_start = math.hypot(x0 - cx, y0 - cy)
    r_end = math.hypot(x1 - cx, y1 - cy)
    delta_r = abs(r_end - r_start)
    if delta_r > 0.005 and (delta_r > 0.5 or delta_r > 0.001 * r_start):
        raise GcodeError(
            f"G2/G3 圆弧半径不自洽（起点半径 {r_start:.4f} / 终点半径 {r_end:.4f}，"
            f"差 {delta_r:.4f}mm；Grbl 会报 invalid target）"
        )
    return cx, cy


def _arc_bbox(
    x0: float, y0: float, x1: float, y1: float,
    i: float | None, j: float | None, r: float | None,
    clockwise: bool,
) -> tuple[float, float, float, float]:
    """圆弧包围盒 (min_x, min_y, max_x, max_y)。

    端点不足以定界：圆弧中途会凸出到象限点（0°/90°/180°/270°）。只有**落在实际
    扫过角度区间内**的象限点才算，否则会把整个外接正方形当包围盒而误拒合法弧。
    """
    cx, cy = _arc_center(x0, y0, x1, y1, i, j, r, clockwise)
    radius = math.hypot(x0 - cx, y0 - cy)

    a0 = math.atan2(y0 - cy, x0 - cx)
    a1 = math.atan2(y1 - cy, x1 - cx)
    # 扫过的角度跨度，统一折算成 CCW 正向 [0, 2π)；扫角 ≤ 固件 epsilon = 整圆
    #（阈值语义见 _ARC_FULL_CIRCLE_EPSILON_RAD 的注释，必须与 Grbl 逐字一致——
    # 含比较符：MotionControl.cpp 是 `<= eps`（CCW）/ `>= -eps`（CW），R13-GC-02
    # 前此处是严格 `<`，|sweep| 恰等于 5e-7 时固件画整圆而校验器按近零弧放行）。
    sweep = (a1 - a0) if not clockwise else (a0 - a1)
    sweep %= 2.0 * math.pi
    if sweep <= _ARC_FULL_CIRCLE_EPSILON_RAD:
        sweep = 2.0 * math.pi

    xs, ys = [x0, x1], [y0, y1]
    for k in range(4):
        ang = k * math.pi / 2.0
        # 该象限点相对起点、沿运动方向走过的角度是否落在 sweep 内
        delta = (ang - a0) if not clockwise else (a0 - ang)
        delta %= 2.0 * math.pi
        if delta <= sweep:
            xs.append(cx + radius * math.cos(ang))
            ys.append(cy + radius * math.sin(ang))
    return min(xs), min(ys), max(xs), max(ys)


@dataclass(frozen=True)
class GcodeStats:
    lines: int
    bytes: int
    pen_lifts: int


def _vpype_cmd() -> list[str]:
    """定位 vpype 命令：venv 内 console script → PATH → python -m vpype_cli。"""
    # venv 场景：脚本与解释器同目录（Windows vpype.exe / POSIX vpype）
    for name in ("vpype.exe", "vpype"):
        candidate = Path(sys.executable).parent / name
        if candidate.is_file():
            return [str(candidate)]
    exe = shutil.which("vpype")
    if exe:
        return [exe]
    # 兜底必须是 vpype_cli 不是 vpype（GC-P3a）：vpype 包没有 __main__.py，
    # `-m vpype` 是死路径，真走到时报 "No module named vpype.__main__"，把
    # 「console script 丢失」误诊成「vpype 没装」。console script 的入口
    # `vpype_cli:cli` 所在的 vpype_cli 包才有 __main__.py（1.15 实测）。
    return [sys.executable, "-m", "vpype_cli"]


def _vpype_command(svg_path: Path, gcode_path: Path, normalize: bool = True) -> list[str]:
    """构造固定 vpype 管线，供同步和可取消异步入口共用。

    normalize=False（写字排版链专用）跳过 layout：文字页版式由 write_text 按
    竖版 190x277mm 自排（B 案 2026-09-06，撰写页常量 PAGE_AUTHOR_*），layout 的
    等比缩放会让「字号」随每页内容量漂移（多页字大小不一）。
    """
    return _vpype_cmd() + [
        "--config", str(_PROFILE_PATH),
        "read", str(svg_path),
        # layout：等比缩放 + 居中到边距内，兜住 LLM 构图散布（见 LAYOUT_MARGIN_MM）。
        # GC-02：零跨度几何（纯竖直/水平线）生产链**可达**——vpype read 只丢单点
        # 段，两点竖/横线存活进 layout，零跨度缩放比按 numpy 语义 min(inf, 有限)
        # =有限正常归一化（R13-GC-01 前此处误记「生产链不可达」；回归钉：
        # test_svg_to_gcode_layout_handles_degenerate_line 横线 + 竖线两例）。
        # 真正危险的只剩两点重合（宽=高=0 → scale=inf → 坐标 inf，由
        # validate_gcode「无法解析」闸 fail-closed）。改 layout 调用方式或升级
        # numpy/vpype 前必须先证明零跨度几何仍被正确处理。
        # 撰写页 = 竖版 190x277（B 案 2026-09-06，常量注释见 PAGE_AUTHOR_*）：
        # vpype layout 默认强制竖版——现在竖版正是所需，页宽 < 页高时不加
        # --landscape（加了会强制换回横版 277x190，转置后 Y=SVG x 越出 190 被
        # validate_gcode 整单拒）。内容归一化到 (190-2m)x(277-2m) 居中。
        *((
            "layout", "--fit-to-margins", f"{LAYOUT_MARGIN_MM:.0f}mm",
            *(("--landscape",) if PAGE_AUTHOR_WIDTH_MM > PAGE_AUTHOR_HEIGHT_MM else ()),
            f"{PAGE_AUTHOR_WIDTH_MM:.0f}x{PAGE_AUTHOR_HEIGHT_MM:.0f}mm",
        ) if normalize else ()),
        # linemerge：把首尾相接的碎笔画并成一条，减少抬落笔次数
        "linemerge", "--tolerance", f"{LINEMERGE_TOLERANCE_MM}mm",
        "linesort",
        # linesimplify：SVG 曲线默认离散得极密（实测一张图 9266 行 G1，步长
        # 0.12mm）。逐行流控下每行都要一次 Telnet 往返（~25ms），而 0.12mm @
        # F24000（400mm/s，2026-09-12 提速批）只需 0.3ms 执行——电机走完就干等，
        # 表现为「笔走停停」，提速后该矛盾更突出。简化到笔宽量级内可把行数降
        # 一个数量级，肉眼无差别。
        # 进给以生产 profile 为准（hutuji_gwrite.toml:58-61 全是 F24000）；早先
        # 这里写的 F1500/~20ms 是 profile 定稿前的旧口径，与上方 25ms 自相矛盾。
        "linesimplify", "--tolerance", f"{LINESIMPLIFY_TOLERANCE_MM}mm",
        "gwrite", "--profile", "hutuji", str(gcode_path),
    ]


def _xy_from_words(upper: str) -> tuple[float | None, float | None]:
    x = y = None
    for letter, number in _WORD_RE.findall(upper):
        if letter == "X":
            x = float(number)
        elif letter == "Y":
            y = float(number)
    return x, y


def _z_from_words(upper: str) -> float | None:
    for letter, number in _WORD_RE.findall(upper):
        if letter == "Z":
            return float(number)
    return None


def _f_from_words(words: list) -> float | None:
    for letter, number in words:
        if letter == "F":
            return float(number)
    return None


def _collapse_short_pen_down_segments(
    text: str, min_mm: float | None = None
) -> str:
    """把落笔态连续短 G1 塌到 ≥ min_mm，末点保留；不跨越抬笔/G0。

    动机：亚毫米 G1 @ F24000 使 planner 空档触发 `$1` 失能与弹簧抬笔，逻辑 Z
    仍可为 5，纸面呈「虚实交替」。缓存长笔画撑得住，故同 profile 表现不同。

    换行风格原样保留（CRLF 不得塌成 LF），否则会改磁盘字节数，绕过 §5
    512KiB 按磁盘字节计的上限，并让回给 S3 的 `bytes` 与 Content-Length 脱钩。
    """
    floor = MIN_PEN_DOWN_G1_MM if min_mm is None else min_mm
    nl = "\r\n" if "\r\n" in text else "\n"
    ends_with_nl = text.endswith("\n")
    raw_lines = text.splitlines()
    out: list[str] = []
    cur_x: float | None = None
    cur_y: float | None = None
    pen_down = False
    cur_feed = 24000.0
    last_emit_x: float | None = None
    last_emit_y: float | None = None
    pending: tuple[float, float, float] | None = None  # (x, y, 设 pending 那行的 F)

    def _flush_pending() -> None:
        nonlocal pending, last_emit_x, last_emit_y, cur_x, cur_y
        if pending is None:
            return
        px, py, pf = pending
        if last_emit_x is None or last_emit_y is None or (
            abs(px - last_emit_x) > 1e-9 or abs(py - last_emit_y) > 1e-9
        ):
            out.append(f"G1 X{px:.3f}Y{py:.3f}F{pf:g}")
            last_emit_x, last_emit_y = px, py
            cur_x, cur_y = px, py
        pending = None

    for raw in raw_lines:
        line = raw.strip()
        if not line:
            out.append(raw)
            continue
        code_part = line.split(";", 1)[0].strip()
        upper = code_part.upper()
        words = _WORD_RE.findall(upper)
        letters = {letter for letter, _ in words}
        z_val = _z_from_words(upper)
        x_val, y_val = _xy_from_words(upper)
        is_g1 = any(letter == "G" and abs(float(number) - 1.0) < 1e-9 for letter, number in words)
        is_g0 = any(letter == "G" and abs(float(number) - 0.0) < 1e-9 for letter, number in words)

        if is_g1 and z_val is not None and abs(z_val - 5.0) < 1e-6:
            _flush_pending()
            pen_down = True
            last_emit_x, last_emit_y = cur_x, cur_y
            out.append(line)
            continue

        if is_g1 and z_val is not None and abs(z_val - 0.0) < 1e-6:
            _flush_pending()
            pen_down = False
            out.append(line)
            continue

        if pen_down and is_g1 and "Z" not in letters and (x_val is not None or y_val is not None):
            nx = cur_x if x_val is None else x_val
            ny = cur_y if y_val is None else y_val
            if nx is None or ny is None:
                out.append(line)
                continue
            # 塌缩写回的 F 继承本行（缺 F 回落模态值，再兜底 24000）——硬编码
            # F24000 会让非 F24000 profile/夹具的进给实验失真（2026-08-28 review）。
            line_feed = _f_from_words(words)
            if line_feed is not None:
                cur_feed = line_feed
            emit_feed = line_feed if line_feed is not None else cur_feed
            pending = (nx, ny, emit_feed)
            if last_emit_x is None or last_emit_y is None:
                out.append(f"G1 X{nx:.3f}Y{ny:.3f}F{emit_feed:g}")
                last_emit_x, last_emit_y = nx, ny
                pending = None
            else:
                dist = math.hypot(nx - last_emit_x, ny - last_emit_y)
                if dist + 1e-9 >= floor:
                    out.append(f"G1 X{nx:.3f}Y{ny:.3f}F{emit_feed:g}")
                    last_emit_x, last_emit_y = nx, ny
                    pending = None
            cur_x, cur_y = nx, ny
            continue

        if is_g0 or (is_g1 and not pen_down):
            _flush_pending()
            if x_val is not None:
                cur_x = x_val
            if y_val is not None:
                cur_y = y_val
            out.append(line)
            continue

        out.append(line)

    _flush_pending()
    body = nl.join(out)
    if ends_with_nl:
        if nl == "\r\n":
            if not body.endswith("\r\n"):
                body += "\r\n"
        elif not body.endswith("\n"):
            body += "\n"
    return body


# 转置匹配行：本管线的运动行恒为完整 XY 对（gwrite 模板 + 塌缩写回都成双）。
_TRANSPOSE_PAIR_RE = re.compile(r"X(-?\d+(?:\.\d+)?)(\s*)Y(-?\d+(?:\.\d+)?)")
_transpose_xy_cache: bool | None = None


def _transpose_xy_enabled() -> bool:
    """读生产 profile 的 `transpose_xy`（B 案竖版转置开关，模块级缓存）。

    gwrite 不认识该键（`config.get` 取用，未知键静默忽略），纯供本模块自读——
    方向配置全部留在 hutuji_gwrite.toml 一个文件里。进程内缓存一次：.py/toml
    变更的生产部署本来就伴随服务重启（deploy 规程）。
    """
    global _transpose_xy_cache
    if _transpose_xy_cache is None:
        import tomllib

        cfg = tomllib.loads(_PROFILE_PATH.read_text(encoding="utf-8"))
        _transpose_xy_cache = bool(cfg.get("gwrite", {}).get("hutuji", {}).get("transpose_xy", False))
    return _transpose_xy_cache


def _transpose_gcode_xy(text: str) -> str:
    """逐行 X↔Y 词值互换（B 案竖版转置：机器 X:=撰写 y、Y:=撰写 x）。

    插入点在塌缩之后、弧拟合之前——流中只有 G0/G1（模板恒带完整 XY 对），
    弧由弧拟合在转置后几何上生成，方向天然正确，无需处理 G2/G3 的 I/J 与
    旋向互换。fail-closed：任何只带单轴词（模态 X/Y 行）的行会让静默互换
    变成几何损坏，整单拒绝。换行风格原样保留（同塌缩的字节口径纪律）。
    """
    out: list[str] = []
    for raw in text.splitlines(keepends=True):
        stripped = raw.strip()
        if not stripped:
            out.append(raw)
            continue
        words = _WORD_RE.findall(stripped.upper())
        n_x = sum(1 for letter, _ in words if letter == "X")
        n_y = sum(1 for letter, _ in words if letter == "Y")
        if n_x == 0 and n_y == 0:
            out.append(raw)
            continue
        if n_x != 1 or n_y != 1:
            raise GcodeError(f"竖版转置拒绝模态/多值 XY 行：{stripped[:60]}")
        swapped, n_sub = _TRANSPOSE_PAIR_RE.subn(r"X\g<3>\g<2>Y\g<1>", stripped, count=1)
        if n_sub != 1:
            raise GcodeError(f"竖版转置无法互换 XY 行：{stripped[:60]}")
        out.append(
            swapped + ("\r\n" if raw.endswith("\r\n") else "\n" if raw.endswith("\n") else "")
        )
    return "".join(out)


def _read_generated_gcode(gcode_path: Path, on_raw_text=None, *, profile: "MachineProfile | None" = None) -> GcodeStats:
    """读取 vpype 成功退出后的产物，并统一成合同错误。

    **必须按字节读**，不能用 `read_text()`：后者走 universal newlines，把 CRLF 塌成
    单个 LF，于是 §5 的 512KiB 上限与回给 S3 的 `bytes` 都会按塌陷后的字符数计算，
    每行少算 1 字节。而 S3 经 HTTP 下载到的是磁盘原始字节，`Content-Length` 与
    CRC32 也都按原始字节算——两边量的不是同一个东西。实测：CRLF 产物磁盘 524369
    字节（超上限 81 字节）被 `validate_gcode` 判为未超并放行，`bytes` 少报 20846。
    512KiB 是 S3 侧 PSRAM 缓冲的硬约束，少算即等于把越限产物放给设备。
    """
    try:
        raw = gcode_path.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.error("vpype 返回 0 但未产出可读 G-code 文件：%s", exc, exc_info=True)
        raise GcodeError("vpype 转换未产出 G-code 文件") from None
    # 塌缩前原文回调：paper_risk 闸的标定面是 vpype 原文（08-26/27 样本），
    # 1.0mm 地板会让塌缩后文本的短段指标结构性惰性；pen_lifts 对塌缩/转置/
    # 弧拟合不变，闸可用终稿 stats.pen_lifts 评原文。成功解码即发一次。
    if on_raw_text is not None:
        on_raw_text(text)
    # 落笔短段地板 + 弧拟合：写回磁盘，保证 /files 下载字节与校验口径一致。
    # 顺序：先塌缩（短段地板 1.0mm，保证拟合输入的最小段长），再拟合（弧/直线
    # 覆盖连续折线）。弧拟合只砍行数不改几何（终点逐点取原顶点，容差 0.02mm
    # 叠加在 linesimplify 0.05 之后），2026-09-05 提速批：113ms/行传输瓶颈下
    # 行数 ≈ 时间，字库曲线 24 步细分压成 1~3 条 G2/G3。
    # 惰性 import：arc_fit 反向复用本模块的 _WORD_RE 等解析器，顶层互引成环。
    from hutuji_mcp.arc_fit import fit_gcode_arcs

    collapsed = _collapse_short_pen_down_segments(text)
    # B 案竖版转置（toml `transpose_xy`）：塌缩后、弧拟合前——流中只有 G0/G1
    # 完整 XY 对，弧由拟合在转置后几何上生成（旋向天然正确）。转置在撰写系
    # 内部是反射（det −1），恰与墙上竖装的奇象限相消，纸面站直横读。
    transpose = _transpose_xy_enabled() if profile is None or profile.transpose_xy is None else profile.transpose_xy
    transposed = _transpose_gcode_xy(collapsed) if transpose else collapsed
    fitted = fit_gcode_arcs(transposed)
    if fitted != text:
        try:
            gcode_path.write_bytes(fitted.encode("utf-8"))
        except OSError as exc:
            logger.error("落笔短段塌缩/弧拟合后写回失败：%s", exc, exc_info=True)
            raise GcodeError("G-code 后处理写回失败") from None
        text = fitted
        raw = fitted.encode("utf-8")
    return validate_gcode(text, total_bytes=len(raw), profile=profile)


def svg_to_gcode(svg_path: Path, gcode_path: Path, normalize: bool = True, on_raw_text=None, *, profile: "MachineProfile | None" = None) -> GcodeStats:
    """跑 vpype 管线生成 G-code，随后做 §5 合同校验。

    normalize=False 仅写字/表格排版链使用（跳过 layout 保字号，与
    svg_to_gcode_async 同约定）；其余调用方不得传。portal 文档导入是同步
    调用方，表格/文章页版式由 table_layout/write_text 自排。
    """
    cmd = _vpype_command(svg_path, gcode_path, normalize)
    proc = None
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=300)
    except subprocess.TimeoutExpired:
        # 超时是进程级异常（非 GcodeError），会穿透 svg_to_gcode 变成 500；
        # §5「越界即整单拒绝 + 明确告知」要求统一塌成窄类型。exc_info 保留
        # 现场（命令/超时值），便于运维定位；对外消息仍不含 stderr。
        logger.error("vpype 转换超时（300s 上限）", exc_info=True)
        raise GcodeError("vpype 转换超时（300s 上限）") from None
    except OSError as exc:
        # vpype 二进制缺失/权限拒绝等启动失败同样不是 GcodeError，一并塌窄。
        logger.error("vpype 子进程启动失败：%s", exc, exc_info=True)
        raise GcodeError("vpype 子进程启动失败") from None
    if proc.returncode != 0:
        logger.error("vpype 转换失败（exit %d）：%s", proc.returncode, proc.stderr[-500:])
        raise GcodeError(f"vpype 转换失败（exit {proc.returncode}）")
    return _read_generated_gcode(gcode_path, on_raw_text=on_raw_text, profile=profile)


async def _svg_to_gcode_async_unbounded(svg_path: Path, gcode_path: Path, normalize: bool = True, on_raw_text=None, *, profile: "MachineProfile | None" = None) -> GcodeStats:
    """可取消地运行 vpype；请求取消或超时都必须回收子进程。"""
    try:
        process = await asyncio.create_subprocess_exec(
            *_vpype_command(svg_path, gcode_path, normalize),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        logger.error("vpype 子进程启动失败：%s", exc, exc_info=True)
        raise GcodeError("vpype 子进程启动失败") from None

    async def _stop_process() -> None:
        if process.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
            await process.wait()

    async def _stop_process_before_propagating_cancellation() -> None:
        cleanup_task = asyncio.create_task(_stop_process())
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            # 重复取消也不能把子进程留在后台；回收完成后再继续传播取消。
            await cleanup_task
            raise

    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=300)
    except asyncio.TimeoutError:
        await _stop_process_before_propagating_cancellation()
        logger.error("vpype 转换超时（300s 上限）")
        raise GcodeError("vpype 转换超时（300s 上限）") from None
    except asyncio.CancelledError:
        # 取消 asyncio 等待不会自动停止 subprocess；这里拥有子进程句柄，必须显式回收。
        await _stop_process_before_propagating_cancellation()
        raise

    if process.returncode != 0:
        tail = stderr.decode("utf-8", errors="replace")[-500:]
        logger.error("vpype 转换失败（exit %d）：%s", process.returncode, tail)
        raise GcodeError(f"vpype 转换失败（exit {process.returncode}）")
    return _read_generated_gcode(gcode_path, on_raw_text=on_raw_text, profile=profile)


async def svg_to_gcode_async(svg_path: Path, gcode_path: Path, normalize: bool = True, on_raw_text=None, *, profile: "MachineProfile | None" = None) -> GcodeStats:
    """可取消地运行 vpype；最多两个子进程并发，取消时仍回收已启动进程。

    normalize=False 仅写字排版链使用（跳过 layout 保字号），其余调用方不得传。
    """
    async with _vpype_semaphore():
        return await _svg_to_gcode_async_unbounded(svg_path, gcode_path, normalize, on_raw_text=on_raw_text, profile=profile)


def validate_gcode(text: str, total_bytes: int | None = None, *, profile: "MachineProfile | None" = None) -> GcodeStats:
    """逐行断言 docs/protocol.md §5 契约，违规抛 GcodeError（带行号）。

    `total_bytes` 是**落盘后的真实字节数**，由调用方按 `len(read_bytes())` 传入。
    不传则退回 `len(text.encode())`，只在「文本即全部真相」的场景（测试直接构造
    字符串）成立；凡是从文件来的产物都必须显式传，否则 CRLF 会被少算 1 字节/行，
    详见 `_read_generated_gcode` 的说明。
    """
    # 行拆分必须与设备口径一致（GC-P3c）：设备只认 \n / \r 做 EOL，而 Python
    # splitlines() 还会按 \x0b/\x0c/\x1c-\x1e/\x85/U+2028/U+2029 切行——一条含
    # \x0c 的 270 字节设备行会被拆成两条「合法」短行、分隔符本身消失，设备却
    # 按原样收到超长行被 error:11 静默吃掉。故只按 \n 切；行尾单个 \r 剥掉
    #（CRLF 产物真实存在，§3 已约定 S3 重切行），其余控制字符留在行内，交由
    # 下面循环里的控制字符/ASCII 闸显式拒绝。
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # 文件末尾换行符产生的空尾段不是一行
    max_x_mm, max_y_mm = _resolve_limits(profile)

    lines = [l[:-1] if l.endswith("\r") else l for l in lines]
    if not lines or not any(l.strip() for l in lines):
        raise GcodeError("G-code 为空")

    # 单文件 ≤ 512KB（protocol §5，S3 侧 PSRAM 缓冲硬约束；与 VPS 内存无关）
    if total_bytes is None:
        total_bytes = len(text.encode("utf-8"))
    if total_bytes > MAX_BYTES:
        raise GcodeError(
            f"G-code 总大小 {total_bytes} 字节超上限 {MAX_BYTES}（512KB，protocol §5）"
        )

    # §5 精确文件头/尾是产物形状契约，不是语法合法性：剥注释/空行后的前 5 个
    # 代码块必须逐字对齐 hutuji_gwrite.toml 的 document_start（G21/G90/F24000 +
    # 两次 G1G90 Z0.0F10000 幂等抬笔）。G210/G900 这类子串、F 值不为 24000、
    # 抬笔数值不是 0.0/10000，都会在这里被拒，而不是等宽松的允许列表兜底。
    # 抬笔行的「等价写法」只放宽空白（G1 G90 Z0.0 F10000），数值字面量不放松。
    code_lines: list[tuple[int, str]] = []
    for lineno, raw in enumerate(lines, start=1):
        code = raw.split(";", 1)[0].strip()
        if code:
            code_lines.append((lineno, code))

    expected_head = (
        "G21",
        "G90",
        "F24000",
        "G1G90 Z0.0F10000",  # 第 1 次幂等抬笔（不建立 Z 基准，G92 由 S3 旁路）
        "G1G90 Z0.0F10000",  # 第 2 次幂等抬笔
    )
    for i, expected in enumerate(expected_head, start=1):
        if i > len(code_lines):
            raise GcodeError(
                f"第 {i} 行缺少 §5 文件头 {expected}（文件头不足 5 个代码块）"
            )
        lineno, code = code_lines[i - 1]
        if code.replace(" ", "") != expected.replace(" ", ""):
            raise GcodeError(
                f"第 {lineno} 行不是精确的 §5 文件头 {expected}（实际：{code}）"
            )

    # §5 收尾：最后一个有效代码块（剥注释/空行后）必须精确对齐生产
    # document_end。只放宽空格，不能把任意含 Z0 的画线或错误进给当成收尾。
    last_lineno, last_code = code_lines[-1]
    expected_end = "G1G90 Z0.0F10000"
    if last_code.replace(" ", "") != expected_end.replace(" ", ""):
        raise GcodeError(
            f"第 {last_lineno} 行（文件末尾）必须是 {expected_end}（§5 契约，实际：{last_code}）"
        )

    seen_g21 = False
    seen_g90 = False
    pen_lifts = 0
    # 零运动闸（GC-P3b）：上游描摹丢线/linesimplify 滤光几何时，vpype 仍会产出
    # 「合法头尾但一条 XY 运动都没有」的空产物——放行即浪费一张纸和一轮换纸
    # 周期，用户拿到「成功」却是白纸。至少一条带 XY 坐标的运动行才算有内容。
    seen_xy_motion = False
    # Grbl 复位/上电后的默认运动模式是 **G0**：`Grbl_Esp32/src/GCode.h:78`
    # `Seek = 0, // G0 (Default: Must be zero)`，modal 结构体归零即 Seek；
    # 只有 M2/M30 之后才切到 G1（`GCode.cpp:1736`）。只有本行显式给 G0~G3 才切换。
    # 裸轴字行（如 `X10 Y20`）会继承该模式，校验器必须与固件保持一致，否则
    # 可绕过坐标限幅、回原点禁令和圆弧位置跟踪——尤其裸 `X0 Y0` 会被固件按 G0
    # 执行并触发自动换纸 + 失能电机（`GCode.cpp:1773-1786`，该路径只查 XY、
    # 无一次性门）。初值取 G0 是 fail-safe 方向：宁可对生产头之前的裸轴字从严误拒。
    active_motion = "G0"
    # 圆弧包围盒需要起点，故逐行跟踪当前 XY。None = 尚无已知位置（首个定位前）。
    cur_x: float | None = None
    cur_y: float | None = None

    for idx, raw in enumerate(lines, start=1):
        line = raw.strip()
        # 剥掉 G-code 行注释（`;` 及其后内容），避免注释中的 X/Y 字母触发轴字检查
        code_part = line.split(";", 1)[0].strip()
        upper = code_part.upper()

        # 行内控制字符显式拒绝（GC-P3c 的另一半）：\x0c/\x0b/\t/行中 \r 等在
        # 设备侧要么是 EOL（\r，产生行错位）、要么进 Grbl 解析器被拒——旧
        # splitlines() 把其中一部分当分隔符吞掉，校验器与设备看到的行就不同了。
        if any(ch < " " or ch == "\x7f" for ch in raw):
            raise GcodeError(f"第 {idx} 行含控制字符（生产 G-code 行内禁止控制字符）")

        # Grbl LINE_BUFFER_SIZE 是字节数；S3 会原样转发 UTF-8，故不能只量 Python
        # 字符数。生产 G-code 只接受 ASCII，避免多字节注释撑爆设备行缓冲。
        # `raw` 已剥去行尾换行符（含 CRLF 的 \r），故其长度就是 payload 字节数——
        # 与 MAX_LINE_PAYLOAD_BYTES 的口径（不含换行）一致。
        try:
            line_bytes = raw.encode("ascii")
        except UnicodeEncodeError:
            raise GcodeError(f"第 {idx} 行含非 ASCII 字符（生产 G-code 只允许 ASCII）") from None
        if len(line_bytes) > MAX_LINE_PAYLOAD_BYTES:
            raise GcodeError(
                f"第 {idx} 行 {len(line_bytes)} 字节超上限 {MAX_LINE_PAYLOAD_BYTES}"
                f"（Grbl LINE_BUFFER_SIZE 256 的可用 payload，第 255 字节起整行被 error:11 吃掉）"
            )

        # 旧「首行禁 G0 X0Y0Z0」检查已删（GC-P3d 死代码）：文件头闸在本循环之前
        # 运行且要求首个代码块必须是精确 G21，任何首行 G0 都到不了这里；
        # 非首行的 G0 落点 X0Y0 由下方通用禁令覆盖。

        # 指令识别不能依赖空格切分：`G0X0Y0`（无空格）整行只有一个 token，
        # `G90 G0 X0 Y0`（模态前缀）首个 token 是 G90，两者都能绕过下面的禁令。
        # 改为把整行拆成 G-code 字，与写法和位置无关；`G01`/`G1` 归一为 G1。
        # 指令与坐标共用这一份结果，避免两套正则口径不一致（早先 motion_re 的
        # `\b` 在 `G0X0Y0` 里不成立，导致坐标一个都没匹配到）。
        words = _WORD_RE.findall(upper)
        # 允许列表必须覆盖整条有效代码；任何未被「字母+数值」解析器消费的内容
        # （如 !/~/?/%、ZNAN、孤立字母、括号注释）都从严拒绝，不能静默忽略。
        remainder = _WORD_RE.sub("", upper)
        if remainder.strip():
            raise GcodeError(f"第 {idx} 行含无法解析或不允许的内容：{line}")

        # 轴字后连续的「数值样」字符整体必须是合法有限数。`XNAN`/`X `（缺值）、
        # `X1.5.5`（残片）都会让 _WORD_RE 静默丢掉该轴坐标——等于把未知坐标当
        # 「本行没有该轴」放行，限幅形同虚设。故独立扫轴字，非法即拒。
        for m in _AXIS_CAND_RE.finditer(upper):
            if not _FINITE_NUM_RE.match(m.group(2)):
                raise GcodeError(
                    f"第 {idx} 行 {m.group(1)} 轴数值无法解析（禁 NAN/INF/残缺写法）：{line}"
                )

        codes = set()
        value_letters: set[str] = set()
        for letter, number in words:
            if letter not in _ALLOWED_WORD_LETTERS:
                raise GcodeError(f"第 {idx} 行含无法解析或不允许的 {letter} 字：{line}")
            value = float(number)
            if not math.isfinite(value):
                raise GcodeError(f"第 {idx} 行 {letter} 数值非有限：{line}")
            if letter not in ("G", "M"):
                if letter in value_letters:
                    raise GcodeError(f"第 {idx} 行重复 {letter} 字（Grbl 不接受同块重复字）：{line}")
                value_letters.add(letter)
            if letter == "Z" and value not in (0.0, 5.0):
                raise GcodeError(f"第 {idx} 行 Z{value} 非生产笔位（只允许 Z0/Z5）：{line}")
            if letter == "F":
                # 2026-09-12 提速批：XY 生产进给 F24000（400mm/s，3× 旧 F8000），
                # Z 笔控行仍恒 F10000；总范围闸按文件最大允许值 24000 收口。
                if value <= 0 or value > 24000:
                    raise GcodeError(f"第 {idx} 行 F{value} 超出生产进给范围 0<F≤24000：{line}")
            if letter not in ("G", "M"):
                continue
            if not value.is_integer():
                raise GcodeError(f"第 {idx} 行 {letter} 字号必须是整数：{line}")
            code = f"{letter}{int(value)}"
            if code in codes:
                raise GcodeError(f"第 {idx} 行重复 {code} 指令（Grbl modal group 冲突）：{line}")
            codes.add(code)

        coords = [(a, float(v)) for a, v in words if a in ("X", "Y")]
        xy = dict(coords)
        z_values = [float(v) for a, v in words if a == "Z"]
        feed_values = [float(v) for a, v in words if a == "F"]
        feed = feed_values[0] if feed_values else None

        # Grbl 运动模式是模态的：本行显式 G0~G3 时切换；只有轴字时沿用上次模式。
        # 同一 modal group 出现多个运动码时 Grbl 会报错，云端也直接拒，不能从 set
        # 中任取一个导致校验结果不确定。
        motion_codes = codes & {"G0", "G1", "G2", "G3"}
        if len(motion_codes) > 1:
            raise GcodeError(f"第 {idx} 行含多个运动模式 {sorted(motion_codes)}：{line}")
        if motion_codes:
            active_motion = next(iter(motion_codes))
        has_axis_words = any(a in ("X", "Y", "Z") for a, _ in words)
        has_motion = bool(motion_codes or has_axis_words)

        arc_words = {a for a, _ in words if a in ("I", "J", "R")}
        if arc_words and active_motion not in {"G2", "G3"}:
            raise GcodeError(f"第 {idx} 行圆弧参数 I/J/R 只能用于 G2/G3：{line}")
        if active_motion in {"G2", "G3"} and (has_motion or arc_words):
            if not coords:
                raise GcodeError(f"第 {idx} 行 G2/G3 缺少 X/Y 轴目标：{line}")
            if not arc_words:
                raise GcodeError(f"第 {idx} 行 G2/G3 缺少圆弧参数 I/J 或 R：{line}")
            if "R" in arc_words and arc_words & {"I", "J"}:
                raise GcodeError(f"第 {idx} 行 G2/G3 不得同时使用 R 与 I/J：{line}")

        # 保留最危险禁令的专用提示；其余超出生产允许列表的 G/M 指令统一拒绝。
        if codes & {"G28", "G30"}:
            raise GcodeError(f"第 {idx} 行含 G28/G30（§5 禁止）：{line}")
        forbidden_m = {code for code in codes if code.startswith("M")}
        if forbidden_m:
            if forbidden_m & {"M2", "M30", "M701", "M704", "M711", "M712", "M713", "M721"}:
                reason = "换纸/结束指令"
            else:
                reason = "生产文件不允许 M 码"
            raise GcodeError(f"第 {idx} 行含 {'/'.join(sorted(forbidden_m))}（{reason}，§5 禁止）：{line}")
        forbidden_g = {code for code in codes if code.startswith("G") and code not in _ALLOWED_G_CODES}
        if forbidden_g:
            raise GcodeError(
                f"第 {idx} 行含 {'/'.join(sorted(forbidden_g))}（生产文件允许 G0/G1/G2/G3/G21/G90）：{line}"
            )

        # 下载文件不是泛用 G-code：笔控和 XY 运动必须逐行对齐 production profile。
        # F 是 Grbl modal 字，若允许继承，任何中间行都可悄悄沿用落笔 F10000 或
        # 外部注入的低速值；XY 与 Z 同块则会在移动途中落/抬笔。两者均违背 §5。
        # 危险度排序：Z 混入 XY 运动（行程中落/抬笔）> G0 落点 (0,0)（触发本机
        # 自动换纸，机械动作）> 进给失配。提速批后旧 F8000 注入若先命中「XY 运动
        # 必须 F24000」，最高危的换纸触发会被低危消息盖住，故原点闸在 G0/G1
        # 进给检查内先判；终点缺轴取 cur，任一未知则不在此判定，由下方 G0 完整
        # 终点闸 fail closed。
        target_x = xy.get("X", cur_x)
        target_y = xy.get("Y", cur_y)
        if has_axis_words:
            if z_values:
                if coords or active_motion != "G1" or "G90" not in codes or feed != 10000:
                    raise GcodeError(
                        f"第 {idx} 行笔控必须是纯 G1G90 Z0/Z5 F10000：{line}"
                    )
            elif active_motion in {"G0", "G1"}:
                if (
                    active_motion == "G0"
                    and coords
                    and target_x is not None
                    and target_y is not None
                    and abs(target_x) < _GEOMETRY_EPS_MM
                    and abs(target_y) < _GEOMETRY_EPS_MM
                ):
                    raise GcodeError(f"第 {idx} 行 G0 落点 X0 Y0（触发自动换纸，§5 禁止）：{line}")
                if not coords or feed != 24000:
                    raise GcodeError(
                        f"第 {idx} 行 XY 运动必须本行带 F24000，且不得混入 Z：{line}"
                    )
            elif active_motion in {"G2", "G3"} and feed is None:
                # G2/G3 仅保留给既有外部 G-code 限幅回归；即使不由 profile 产出，
                # 也不能让它继承未知进给。
                raise GcodeError(f"第 {idx} 行圆弧运动必须本行带 F 进给：{line}")

        # 抬笔 = 纯 G1G90 Z0 F10000（本 profile 不产 M3/M5，笔控纯 Z 运动）。
        if has_motion and active_motion in {"G0", "G1"}:
            _z = z_values[0] if z_values else None
            if _z == 0.0:
                pen_lifts += 1

        seen_g21 = seen_g21 or "G21" in codes
        seen_g90 = seen_g90 or "G90" in codes
        if has_motion and not (seen_g21 and seen_g90):
            raise GcodeError(f"第 {idx} 行运动前缺少精确 G21/G90（§5 自包含要求）：{line}")

        # 坐标限幅 A4（X≤190mm Y≤190mm），越界即拒。裸轴字同样是模态运动，
        # 不能因本行没有显式 G 字而跳过。
        # 零运动闸要求实际位移（GC-01）：目标点 != 当前坐标才算有内容（含 G2/G3
        # 终点）；零行程（终点 == 起点，如定位同点后的 G2 X100 Y100 I0 J0 /
        # G1 X100 Y100）不再计入——否则「定位 + 零行程」文件仍能骗过闸，放行
        # 即画白纸。当前坐标未知（首个定位前）时带坐标即视为位移：从未知位置
        # 到目标必然是移动。G0 是抬笔态空走（hutuji_gwrite.toml「G0 X..Y.. ←
        # 抬笔态空走到起点」，空走不落墨），不计入运动闸——空走 + 零行程同样
        # 画不出任何东西；生产产物的落墨行全是 G1。
        if has_motion and coords and active_motion != "G0":
            displaced = False
            for axis, target in coords:
                cur = cur_x if axis == "X" else cur_y
                if cur is None or target != cur:
                    displaced = True
                    break
            if displaced:
                seen_xy_motion = True
        if has_motion:
            for axis, val in coords:
                limit = max_x_mm if axis == "X" else max_y_mm
                if val < 0.0 or val > limit:
                    raise GcodeError(f"第 {idx} 行 {axis}{val} 越界（限幅 0~{limit}mm）：{line}")


        # G0 必须能解析出完整 XY 实际终点；任一轴未知时无法证明不会落到 (0,0)，
        # 而本机该落点会触发自动换纸，故 fail closed。原点已在进给检查内先判，
        # 此处补 G0 无轴字（裸 G0 继承 cur）的落点。
        if has_motion and active_motion == "G0":
            if target_x is None or target_y is None:
                raise GcodeError(f"第 {idx} 行 G0 缺少已知完整 XY 起点/目标（无法排除自动换纸）：{line}")
            if abs(target_x) < _GEOMETRY_EPS_MM and abs(target_y) < _GEOMETRY_EPS_MM:
                raise GcodeError(f"第 {idx} 行 G0 落点 X0 Y0（触发自动换纸，§5 禁止）：{line}")

        # G2/G3 端点合规仍可能中途凸出画幅（§5「含圆弧包围盒，不只是端点」）。
        # 需要起点，故逐行跟踪当前位置；G90 绝对坐标下省略的轴沿用上一个值。
        if has_motion and active_motion in {"G2", "G3"}:
            if cur_x is None or cur_y is None:
                raise GcodeError(f"第 {idx} 行 G2/G3 缺少已知起点（前面没有定位指令）：{line}")
            others = {a: float(v) for a, v in words if a in ("I", "J", "R")}
            x1 = target_x if target_x is not None else cur_x
            y1 = target_y if target_y is not None else cur_y
            min_x, min_y, max_x, max_y = _arc_bbox(
                cur_x, cur_y, x1, y1,
                others.get("I"), others.get("J"), others.get("R"),
                clockwise=active_motion == "G2",
            )
            for label, lo, hi in (("X", min_x, max_x), ("Y", min_y, max_y)):
                limit = max_x_mm if label == "X" else max_y_mm
                if lo < -_GEOMETRY_EPS_MM or hi > limit + _GEOMETRY_EPS_MM:
                    raise GcodeError(
                        f"第 {idx} 行圆弧 {label} 包围盒 [{lo:.3f}, {hi:.3f}] "
                        f"越界（限幅 0~{limit}mm）：{line}"
                    )

        # 更新当前位置（G90 绝对；本 profile 不产 G91，故不处理相对模式）。
        if has_motion:
            cur_x = target_x
            cur_y = target_y

    if not (seen_g21 and seen_g90):
        raise GcodeError("文件头缺少精确 G21/G90（§5 自包含要求）")

    if not seen_xy_motion:
        raise GcodeError(
            "G-code 无任何 XY 运动（空产物：上游几何被全部滤掉，下发只会浪费纸张）"
        )

    if pen_lifts > PEN_LIFT_WARN_THRESHOLD:
        logger.warning(
            "G-code 抬笔 %d 次，超过软阈值 %d；本次仍放行",
            pen_lifts,
            PEN_LIFT_WARN_THRESHOLD,
        )

    return GcodeStats(lines=len(lines), bytes=total_bytes, pen_lifts=pen_lifts)


def _main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args != ["--self-test"]:
        print("usage: python -m hutuji_mcp.gcode_gen --self-test", file=sys.stderr)
        return 2
    _run_self_test()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
