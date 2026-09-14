"""纸张、留边与已核对的机器范围；共享给生成、门户和产物复核。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import math
from typing import Any

PAPER_SIZES = {"A4": (210.0, 297.0), "A3": (297.0, 420.0), "A2": (420.0, 594.0)}
# 覆盖 A2 和常用大行程绘图机；上界是产品输入预算，不代表任何机器实测范围。
MAX_PAPER_MM = 2000.0
MAX_TRAVEL_MM = 2000.0


class PaperConfigError(ValueError):
    """纸张输入或整页适配无效。"""


def _tenths(name: str, value: Any, low: float, high: float) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PaperConfigError(f"{name} 必须是毫米数值")
    if not low <= value <= high or not math.isfinite(value):
        raise PaperConfigError(f"{name} 须在 {low:g}–{high:g}mm，最多一位小数")
    scaled = round(value * 10)
    if not low <= value <= high or abs(value * 10 - scaled) > 1e-7:
        raise PaperConfigError(f"{name} 须在 {low:g}–{high:g}mm，最多一位小数")
    return scaled


@dataclass(frozen=True)
class PaperConfig:
    paper: str = "A4"
    width_mm: float = 210.0
    height_mm: float = 297.0
    landscape: bool = False
    margin_mm: float = 10.0
    swap_xy: bool = True
    max_x_mm: float = 277.0
    max_y_mm: float = 190.0

    def __post_init__(self) -> None:
        if self.paper not in (*PAPER_SIZES, "custom"):
            raise PaperConfigError("纸张须为 A4、A3、A2 或 custom")
        if type(self.landscape) is not bool or type(self.swap_xy) is not bool:
            raise PaperConfigError("横向和 XY 交换须为布尔值")
        w = _tenths("纸宽", self.width_mm, 50.0, MAX_PAPER_MM)
        h = _tenths("纸高", self.height_mm, 50.0, MAX_PAPER_MM)
        m = _tenths("留边", self.margin_mm, 0.0, 50.0)
        x = _tenths("X 可绘制范围", self.max_x_mm, 10.0, MAX_TRAVEL_MM)
        y = _tenths("Y 可绘制范围", self.max_y_mm, 10.0, MAX_TRAVEL_MM)
        if self.paper in PAPER_SIZES and (self.width_mm, self.height_mm) != PAPER_SIZES[self.paper]:
            raise PaperConfigError("纸张名称与尺寸不一致")
        if self.landscape:
            w, h = h, w
        w, h = w - m * 2, h - m * 2
        if min(w, h) < 200:
            raise PaperConfigError("留边后每边至少需要 20mm")
        if self.swap_xy:
            w, h = h, w
        if w > x or h > y:
            raise PaperConfigError(f"当前纸张需要 X{w / 10:g} × Y{h / 10:g}mm，超出已设置的可绘制范围")

    @property
    def page_width_mm(self) -> float:
        width = self.height_mm if self.landscape else self.width_mm
        return (round(width * 10) - 2 * round(self.margin_mm * 10)) / 10

    @property
    def page_height_mm(self) -> float:
        height = self.width_mm if self.landscape else self.height_mm
        return (round(height * 10) - 2 * round(self.margin_mm * 10)) / 10

    @property
    def marker(self) -> str:
        # S3 纯核心逐字段生成同一标记，避免两种浮点 JSON 表示产生伪不一致。
        return "p1:{}:{}:{}:{}:{}:{}:{}:{}".format(
            self.paper, round(self.width_mm * 10), round(self.height_mm * 10),
            int(self.landscape), round(self.margin_mm * 10), int(self.swap_xy),
            round(self.max_x_mm * 10), round(self.max_y_mm * 10),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_profile(self):
        from .gcode_gen import MachineProfile

        return MachineProfile(
            transpose_xy=self.swap_xy, max_x_mm=self.max_x_mm, max_y_mm=self.max_y_mm,
            page_width_mm=self.page_width_mm, page_height_mm=self.page_height_mm, paper=self,
        )

    @classmethod
    def from_dict(cls, value: Any) -> PaperConfig:
        if not isinstance(value, dict) or set(value) != {item.name for item in fields(cls)}:
            raise PaperConfigError("纸张配置字段不完整或不支持")
        return cls(**value)

    @classmethod
    def default_for(cls, machine: str) -> PaperConfig:
        if machine in ("", "paper"):
            return cls()
        if machine == "nopaper":
            return cls(swap_xy=False, max_x_mm=205.0, max_y_mm=290.0)
        raise PaperConfigError("未知机型，不能猜测可绘制范围")

    def requires_range_confirmation(self, previous: PaperConfig) -> bool:
        return self.max_x_mm > previous.max_x_mm or self.max_y_mm > previous.max_y_mm
