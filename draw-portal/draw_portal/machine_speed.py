"""兼容同步设备与后台调速事务，只在设备确认两轴成功后回报生效。"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from draw_portal.mcp_result import tool_result_failed, unwrap_tool_text


class MachineSpeedError(Exception):
    pass


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


def wait_for_speed(
    raw: str,
    rate: int,
    probe: Callable[[int], tuple[bool, str]],
) -> int:
    payload = _payload(raw)
    if payload.get("status") == "accepted":
        request_id = payload.get("request_id")
        if type(request_id) is not int or request_id <= 0 or payload.get("rate") != rate:
            raise MachineSpeedError("写字机未确认速度，请稍后重试")
        # 设备预算为新鲜 Idle 6s + 两次应答各 5s；25s 覆盖云端往返。
        deadline = time.monotonic() + 25.0
        for _ in range(50):
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
            if not isinstance(speed, dict) or speed.get("request_id") != request_id:
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
    confirmed = payload.get("rate")
    if type(confirmed) is not int or confirmed != rate:
        raise MachineSpeedError("写字机未确认所选速度，请稍后重试")
    return confirmed
