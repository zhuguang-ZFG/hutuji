"""SVG → G-code 转换（vpype + vpype-gcode）与 §5 合同校验。

vpype 管线：read → linemerge → linesort → linesimplify → gwrite --profile hutuji
（hutuji profile 见 hutuji_gwrite.toml，格式参考 vpype-gcode README）
转换产物必须再过 validate_gcode() 逐行断言 §5 禁令才允许下发。
"""
from __future__ import annotations

import logging
import math
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_PROFILE_PATH = Path(__file__).parent / "hutuji_gwrite.toml"

# 坐标限幅：A4 纸 210×297mm，X 短边 Y 长边，四周留 10mm 边距
MAX_X_MM = 190.0
MAX_Y_MM = 277.0
_EPS = 0.01

# 单文件大小上限（protocol §5，S3 侧 PSRAM 缓冲硬约束；恰好 512KiB 放行，再多一字节才拒）
MAX_BYTES = 512 * 1024

# 抬笔次数目前只做可观测告警；硬阈值须等机头实测后再锁定（README 组 1.3）。
PEN_LIFT_WARN_THRESHOLD = 50

# 线简化容差（mm）：直接决定 G1 行数，从而决定逐行流控下的出图流畅度。
# 机器笔触精度约 0.1mm，0.2mm 在笔宽量级内，肉眼看不出差别。
# 实测同一张图：无简化 9266 行 → 0.1mm 143 行 → 0.2mm 172 行 → 0.5mm 116 行。
# 逐行流控下每行一次 WiFi 往返（~25ms），F8000=133mm/s。
# 拐点在 0.1mm：运动时间(~4.8s)刚好盖过往返(~3.6s)，0.05mm 以下往返反超开始卡顿。
# 奎享靠窗口化流控保留 0.02mm；我们逐行流控下 0.1mm 是流畅极限的最低简化。
LINESIMPLIFY_TOLERANCE_MM = 0.1
# 碎笔画合并容差（mm）：对齐奎享 nearDst=0.3，减少抬落笔次数。
# 合并端点几乎相接的线；0.3mm 内并笔不改变构图（奎享现役值，实测安全）。
LINEMERGE_TOLERANCE_MM = 0.3

# G-code 字（字母 + 数值）。整行唯一的解析入口：G/M 字号与 X/Y 坐标都从这里取，
# 避免两套正则口径不一致（旧代码 `\b([XY])` 在 `G0X0Y0` 里因 X 紧跟数字而无词边界，
# 匹配不到坐标，导致 X0Y0 禁令被绕过）。
# 数值容忍 `01`、`1.0`、`.5`、`1e3`——`1e3` 必须认，否则会被截成 `1` 而漏放 1000mm。
_WORD_RE = re.compile(r"([A-Z])\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[Ee][+-]?\d+)?)")

# X/Y 后面**必须**紧跟一个合法有限数值。`_WORD_RE` 只挑合法数值，`XNAN`、`X `、
# `X1.5.5` 这类畸形写法会让坐标被静默跳过，越界值就此从限幅里消失（静默漏放比
# 误拒危险）。`_AXIS_CAND_RE` 抓住轴字及其后连续的「数值样」字符整体，再用
# `_FINITE_NUM_RE` 判其是否为合法有限数——`X1.5.5` 会被 `_WORD_RE` 截成 `1.5`
# 留下残片，这里能整体识别为非法。
_AXIS_CAND_RE = re.compile(r"([XY])\s*([0-9+\-.Ee]*)")
_FINITE_NUM_RE = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[Ee][+-]?\d+)?$")

logger = logging.getLogger(__name__)


class GcodeError(RuntimeError):
    """G-code 转换或合同校验失败。"""


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
    # 扫过的角度跨度，统一折算成 CCW 正向 [0, 2π)；起终点重合 = 整圆
    sweep = (a1 - a0) if not clockwise else (a0 - a1)
    sweep %= 2.0 * math.pi
    if sweep < 1e-9:
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
    """定位 vpype 命令：venv 内 console script → PATH → python -m vpype。"""
    # venv 场景：脚本与解释器同目录（Windows vpype.exe / POSIX vpype）
    for name in ("vpype.exe", "vpype"):
        candidate = Path(sys.executable).parent / name
        if candidate.is_file():
            return [str(candidate)]
    exe = shutil.which("vpype")
    if exe:
        return [exe]
    return [sys.executable, "-m", "vpype"]


def svg_to_gcode(svg_path: Path, gcode_path: Path) -> GcodeStats:
    """跑 vpype 管线生成 G-code，随后做 §5 合同校验。"""
    cmd = _vpype_cmd() + [
        "--config", str(_PROFILE_PATH),
        "read", str(svg_path),
        # linemerge：把首尾相接的碎笔画并成一条，减少抬落笔次数
        "linemerge", "--tolerance", f"{LINEMERGE_TOLERANCE_MM}mm",
        "linesort",
        # linesimplify：SVG 曲线默认离散得极密（实测一张图 9266 行 G1，步长
        # 0.12mm）。逐行流控下每行都要一次 Telnet 往返（~20ms），而 0.12mm @
        # F1500 只需 4.9ms 执行——电机走完就干等，表现为「笔走停停」。
        # 简化到笔宽量级内可把行数降一个数量级，肉眼无差别。
        "linesimplify", "--tolerance", f"{LINESIMPLIFY_TOLERANCE_MM}mm",
        "gwrite", "--profile", "hutuji", str(gcode_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300)
    if proc.returncode != 0:
        logger.error("vpype 转换失败（exit %d）：%s", proc.returncode, proc.stderr[-500:])
        raise GcodeError(f"vpype 转换失败（exit {proc.returncode}）")
    text = gcode_path.read_text(encoding="utf-8")
    return validate_gcode(text)


def validate_gcode(text: str) -> GcodeStats:
    """逐行断言 docs/protocol.md §5 契约，违规抛 GcodeError（带行号）。"""
    lines = text.splitlines()
    if not lines or not any(l.strip() for l in lines):
        raise GcodeError("G-code 为空")

    # 单文件 ≤ 512KB（protocol §5，S3 侧 PSRAM 缓冲硬约束；与 VPS 内存无关）
    total_bytes = len(text.encode("utf-8"))
    if total_bytes > MAX_BYTES:
        raise GcodeError(
            f"G-code 总大小 {total_bytes} 字节超上限 {MAX_BYTES}（512KB，protocol §5）"
        )

    head = "\n".join(lines[:10])
    if "G21" not in head or "G90" not in head:
        raise GcodeError("文件头缺少 G21/G90（§5 自包含要求）")

    pen_lifts = 0
    # 圆弧包围盒需要起点，故逐行跟踪当前 XY。None = 尚无已知位置（首个定位前）。
    cur_x: float | None = None
    cur_y: float | None = None

    for idx, raw in enumerate(lines, start=1):
        line = raw.strip()
        # 剥掉 G-code 行注释（`;` 及其后内容），避免注释中的 X/Y 字母触发轴字检查
        code_part = line.split(";", 1)[0].strip()
        upper = code_part.upper()

        # 单行 ≤255 字符（Grbl LINE_BUFFER_SIZE 256）
        if len(raw) > 255:
            raise GcodeError(f"第 {idx} 行超过 255 字符")

        # 首行禁 G0 X0Y0Z0（软复位后首页触发点）
        if idx == 1 and re.fullmatch(r"G0?0\s*X0\s*Y0\s*Z0", upper.replace(" ", "")):
            raise GcodeError("首行是 G0 X0 Y0 Z0（会触发自动换纸，§5 禁止）")

        # 指令识别不能依赖空格切分：`G0X0Y0`（无空格）整行只有一个 token，
        # `G90 G0 X0 Y0`（模态前缀）首个 token 是 G90，两者都能绕过下面的禁令。
        # 改为把整行拆成 G-code 字，与写法和位置无关；`G01`/`G1` 归一为 G1。
        # 指令与坐标共用这一份结果，避免两套正则口径不一致（早先 motion_re 的
        # `\b` 在 `G0X0Y0` 里不成立，导致坐标一个都没匹配到）。
        words = _WORD_RE.findall(upper)

        # 轴字后连续的「数值样」字符整体必须是合法有限数。`XNAN`/`X `（缺值）、
        # `X1.5.5`（残片）都会让 _WORD_RE 静默丢掉该轴坐标——等于把未知坐标当
        # 「本行没有该轴」放行，限幅形同虚设。故独立扫轴字，非法即拒。
        for m in _AXIS_CAND_RE.finditer(upper):
            if not _FINITE_NUM_RE.match(m.group(2)):
                raise GcodeError(
                    f"第 {idx} 行 {m.group(1)} 轴数值无法解析（禁 NAN/INF/残缺写法）：{line}"
                )

        codes = set()
        for letter, number in words:
            if letter not in ("G", "M"):
                continue
            value = float(number)
            # `G1E400` 之类会 float() 成 inf，int(inf) 抛 OverflowError——那是
            # 非 GcodeError 异常，会穿透闸门变成 500 而不是「拒绝并说明原因」。
            if not math.isfinite(value):
                raise GcodeError(f"第 {idx} 行 {letter} 字号数值非有限：{line}")
            codes.add(f"{letter}{int(value)}")

        coords = [(a, float(v)) for a, v in words if a in ("X", "Y")]

        # 抬笔 = Z 回 0 的运动（本 profile 不产 M3/M5，笔控纯 Z 运动）。
        # 统计用于发现过度碎线，不在 v0 硬拒。
        if codes & {"G0", "G1"}:
            _z = next((float(v) for a, v in words if a == "Z"), None)
            if _z is not None and abs(_z) < _EPS:
                pen_lifts += 1

        # 禁 G28 / G30（回原点触发自动换纸）
        if "G28" in codes or "G30" in codes:
            raise GcodeError(f"第 {idx} 行含 G28/G30（§5 禁止）：{line}")

        # 禁 M30/M2（程序结束触发自动换纸）及换纸系统指令
        _forbidden_m = codes & {"M2", "M30", "M701", "M704", "M711", "M712", "M713", "M721"}
        if _forbidden_m:
            raise GcodeError(
                f"第 {idx} 行含 {'/'.join(sorted(_forbidden_m))}（换纸/结束指令，§5 禁止）：{line}"
            )

        # 禁 G91 相对坐标。§5 要求文件头 G90（上面已强制），但中途切到 G91 会让
        # 下面的位置跟踪全部失效——之后每个圆弧起点都是错的，包围盒随之失真而
        # 静默漏放越界。本 profile 只产 G90，故直接拒绝而非去实现相对模式。
        if "G91" in codes:
            raise GcodeError(f"第 {idx} 行含 G91 相对坐标（§5 要求全程 G90）：{line}")

        # G0 落点 XY≈0 = 回原点触发（含 G0 X0 Y0 收尾），一律禁止
        if "G0" in codes:
            xy = dict(coords)
            if abs(xy.get("X", 1.0)) < _EPS and abs(xy.get("Y", 1.0)) < _EPS:
                raise GcodeError(f"第 {idx} 行 G0 落点 X0 Y0（触发自动换纸，§5 禁止）：{line}")

        # 坐标限幅 A4（X≤190mm Y≤277mm），越界即拒。
        # G2/G3 圆弧同样产生实际位移，必须一起限幅。
        if codes & {"G0", "G1", "G2", "G3"}:
            for axis, val in coords:
                limit = MAX_X_MM if axis == "X" else MAX_Y_MM
                if val < -_EPS or val > limit + _EPS:
                    raise GcodeError(f"第 {idx} 行 {axis}{val} 越界（限幅 0~{limit}mm）：{line}")

        # G2/G3 端点合规仍可能中途凸出画幅（§5「含圆弧包围盒，不只是端点」）。
        # 需要起点，故逐行跟踪当前位置；G90 绝对坐标下省略的轴沿用上一个值。
        xy = dict(coords)
        if codes & {"G2", "G3"}:
            if cur_x is None or cur_y is None:
                raise GcodeError(f"第 {idx} 行 G2/G3 缺少已知起点（前面没有定位指令）：{line}")
            others = {a: float(v) for a, v in words if a in ("I", "J", "R")}
            x1 = xy.get("X", cur_x)
            y1 = xy.get("Y", cur_y)
            min_x, min_y, max_x, max_y = _arc_bbox(
                cur_x, cur_y, x1, y1,
                others.get("I"), others.get("J"), others.get("R"),
                clockwise="G2" in codes,
            )
            for label, lo, hi in (("X", min_x, max_x), ("Y", min_y, max_y)):
                limit = MAX_X_MM if label == "X" else MAX_Y_MM
                if lo < -_EPS or hi > limit + _EPS:
                    raise GcodeError(
                        f"第 {idx} 行圆弧 {label} 包围盒 [{lo:.3f}, {hi:.3f}] "
                        f"越界（限幅 0~{limit}mm）：{line}"
                    )

        # 更新当前位置（G90 绝对；本 profile 不产 G91，故不处理相对模式）
        if codes & {"G0", "G1", "G2", "G3"}:
            cur_x = xy.get("X", cur_x)
            cur_y = xy.get("Y", cur_y)

    if pen_lifts > PEN_LIFT_WARN_THRESHOLD:
        logger.warning(
            "G-code 抬笔 %d 次，超过软阈值 %d；本次仍放行",
            pen_lifts,
            PEN_LIFT_WARN_THRESHOLD,
        )

    return GcodeStats(lines=len(lines), bytes=total_bytes, pen_lifts=pen_lifts)
