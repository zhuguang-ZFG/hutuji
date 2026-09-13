"""三轴读回与后台调速事务：受理不等于生效，单轴请求不降级成两轴写入。"""
from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from typing import Any

from draw_portal.mcp_result import tool_result_failed, unwrap_tool_text


class MachineSpeedError(Exception):
    pass


SPEED_LIMITS = {"x": (3000, 16000), "y": (3000, 16000), "z": (100, 3000)}


def valid_speed_rate(axis: str, rate: int) -> bool:
    low, high = SPEED_LIMITS["x" if axis == "xy" else axis]
    return type(rate) is int and low <= rate <= high


def _payload(raw: str) -> dict[str, Any]:
    if tool_result_failed(raw):
        raise MachineSpeedError("写字机未完成速度调整，请检查设备状态")
    for _ in range(4):
        unwrapped = unwrap_tool_text(raw)
        if unwrapped == raw:
            break
        raw = unwrapped
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        value = None
    if not isinstance(value, dict):
        raise MachineSpeedError("写字机未确认速度，请稍后重试")
    return value


def supports_axis_speed(raw: str) -> bool:
    speed = _payload(raw).get("speed")
    return isinstance(speed, dict) and speed.get("axis_control") is True


def _matches_axis(payload: dict[str, Any], axis: str, required: bool) -> bool:
    return payload.get("axis", None if required else "xy") == axis


def _rates(payload: dict[str, Any]) -> dict[str, int | float] | None:
    rates = payload.get("rates")
    if not isinstance(rates, dict):
        return None
    if any(type(rates.get(axis)) not in (int, float) or
           not math.isfinite(rates[axis]) or rates[axis] <= 0 for axis in SPEED_LIMITS):
        return None
    return {axis: rates[axis] for axis in SPEED_LIMITS}


def wait_for_speed(
    raw: str,
    rate: int,
    probe: Callable[[int], tuple[bool, str]],
    *,
    axis: str = "xy",
    require_axes: bool = False,
) -> dict[str, Any]:
    payload = _payload(raw)
    if not _matches_axis(payload, axis, require_axes):
        raise MachineSpeedError("写字机未确认所选轴，请检查设备版本")
    if payload.get("status") == "accepted":
        request_id = payload.get("request_id")
        if (type(request_id) is not int or request_id <= 0 or
                type(payload.get("rate")) is not int or payload.get("rate") != rate):
            raise MachineSpeedError("写字机未确认速度，请稍后重试")
        # 最长为 Idle 6s + XY 两次写入 + 三轴读回各 5s；40s 留出云端往返。
        deadline = time.monotonic() + 40.0
        for _ in range(80):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.5, remaining))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            ok, status_raw = probe(max(1, min(5, int(remaining))))
            if not ok:
                raise MachineSpeedError("速度结果查询失败，请检查设备状态")
            speed = _payload(status_raw).get("speed")
            if (not isinstance(speed, dict) or type(speed.get("request_id")) is not int or
                    speed.get("request_id") != request_id or
                    not _matches_axis(speed, axis, require_axes)):
                raise MachineSpeedError("速度任务已变化，请重新查询设备状态")
            if require_axes and speed.get("requested_rate") != rate:
                raise MachineSpeedError("速度任务已变化，请重新查询设备状态")
            if speed.get("state") == "done":
                payload = speed
                break
            if speed.get("state") != "pending" or speed.get("requested_rate") != rate:
                raise MachineSpeedError("写字机未完成速度调整，请检查设备状态")
        else:
            raise MachineSpeedError("速度结果查询超时，请检查设备状态")
        if payload.get("status") == "accepted":
            raise MachineSpeedError("速度结果查询超时，请检查设备状态")
    rates = _rates(payload)
    if require_axes and rates is None:
        raise MachineSpeedError("写字机未返回三轴实际速度，请重新读取")
    result: dict[str, Any] = {"axis": axis}
    if rates is not None:
        result["rates"] = rates
    if rate == 0:
        if rates is None:
            raise MachineSpeedError("写字机未返回三轴实际速度，请重新读取")
        return result
    if type(payload.get("rate")) is not int or payload.get("rate") != rate:
        raise MachineSpeedError("写字机未确认所选速度，请稍后重试")
    if rates is not None and any(rates[key] != rate for key in axis):
        raise MachineSpeedError("写字机读回速度与设置不符，请重新读取")
    result["rate"] = rate
    return result
