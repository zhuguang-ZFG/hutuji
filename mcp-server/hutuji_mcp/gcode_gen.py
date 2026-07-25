"""SVG → G-code 转换（vpype + vpype-gcode）与 §5 合同校验。

vpype 管线：read → linesort → gwrite --profile hutuji
（hutuji profile 见 hutuji_gwrite.toml，格式参考 vpype-gcode README）
转换产物必须再过 validate_gcode() 逐行断言 §5 禁令才允许下发。
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_PROFILE_PATH = Path(__file__).parent / "hutuji_gwrite.toml"

# 坐标限幅：机器 200×200 软限位关，生成端限 190×190 留边距（容忍微小浮点越界）
MAX_MM = 190.0
_EPS = 0.01

# 单文件大小上限（protocol §5，S3 侧 PSRAM 缓冲硬约束；上限含，即 > 该值才拒）
MAX_BYTES = 512 * 1024

# 抬笔次数目前只做可观测告警；硬阈值须等机头实测后再锁定（README 组 1.3）。
PEN_LIFT_WARN_THRESHOLD = 50

logger = logging.getLogger(__name__)


class GcodeError(RuntimeError):
    """G-code 转换或合同校验失败。"""


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
        "linesort",
        "gwrite", "--profile", "hutuji", str(gcode_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300)
    if proc.returncode != 0:
        raise GcodeError(f"vpype 转换失败（exit {proc.returncode}）：{proc.stderr[-500:]}")
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

    motion_re = re.compile(r"\b([XY])(-?\d+(?:\.\d+)?)")
    pen_lifts = 0

    for idx, raw in enumerate(lines, start=1):
        line = raw.strip()
        upper = line.upper()

        # 单行 ≤255 字符（Grbl LINE_BUFFER_SIZE 256）
        if len(raw) > 255:
            raise GcodeError(f"第 {idx} 行超过 255 字符")

        # 首行禁 G0 X0Y0Z0（软复位后首页触发点）
        if idx == 1 and re.fullmatch(r"G0?0\s*X0\s*Y0\s*Z0", upper.replace(" ", "")):
            raise GcodeError("首行是 G0 X0 Y0 Z0（会触发自动换纸，§5 禁止）")

        tokens = upper.split()
        # M5 是本机 profile 的抬笔命令；统计用于发现过度碎线，不在 v0 硬拒。
        if "M5" in tokens or "M05" in tokens:
            pen_lifts += 1
        # 禁 G28 / G30（回原点触发自动换纸）
        if "G28" in tokens or "G30" in tokens:
            raise GcodeError(f"第 {idx} 行含 G28/G30（§5 禁止）：{line}")

        # G0 落点 XY≈0 = 回原点触发（含 G0 X0 Y0 收尾），一律禁止
        if tokens and tokens[0] in ("G0", "G00"):
            coords = {a: float(v) for a, v in motion_re.findall(upper)}
            if abs(coords.get("X", 1.0)) < _EPS and abs(coords.get("Y", 1.0)) < _EPS:
                raise GcodeError(f"第 {idx} 行 G0 落点 X0 Y0（触发自动换纸，§5 禁止）：{line}")

        # 坐标限幅 190×190mm（软限位关闭，越界撞机不报错）
        if tokens and tokens[0] in ("G0", "G00", "G1", "G01"):
            for axis, val_s in motion_re.findall(upper):
                val = float(val_s)
                if val < -_EPS or val > MAX_MM + 1.0:
                    raise GcodeError(f"第 {idx} 行 {axis}{val} 越界（限幅 0~{MAX_MM}mm）：{line}")

    if pen_lifts > PEN_LIFT_WARN_THRESHOLD:
        logger.warning(
            "G-code 抬笔 %d 次，超过软阈值 %d；本次仍放行",
            pen_lifts,
            PEN_LIFT_WARN_THRESHOLD,
        )

    return GcodeStats(lines=len(lines), bytes=total_bytes, pen_lifts=pen_lifts)
