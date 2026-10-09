"""门户和云端推送共用的设备业务错误判据，逐层保留信封错误旗标。"""
from __future__ import annotations

import json
import unicodedata
from typing import Any

from .redact import _redact_secret_text


# 旧 S3 把拒绝作为成功字符串返回；精确匹配以保留「已经暂停」等幂等成功。
_LEGACY_REJECTIONS = frozenset({
    "写字机正忙，请稍候再试",
    "正在试笔，请稍候",
    "换纸中无法暂停，换纸完成后可再试",
})

# 只透出设备协议中的固定拒绝文案，未知网关/异常正文仍走调用方的通用提示。
# 新增设备文案时在此登记；不能靠「包含中文」放行任意内部错误或凭据。
_DEVICE_REJECTIONS = _LEGACY_REJECTIONS | frozenset({
    "纸张尺寸、留边或可绘制范围无效",
    "任务正在收尾，请稍候",
    "请先结束任务或取消预览，再修改纸张",
    "写字机未就绪，请连接后重试",
    "纸张设置已变化，请刷新后重试",
    "纸张记录损坏或机型已变化，请明确恢复默认设置",
    "请先核对并确认设备实际可绘制范围",
    "上次预览正在收尾，请稍候",
    "写字机或纸张配置未就绪，请连接设备后重试",
    "无法创建预览任务",
    "当前没有待确认的预览",
    "写字机未连接或未就绪",
    "写字机未授权，请联系卖家协助激活",
    "无法创建出图任务",
    "正在升级固件，请等待设备重启",
    "正在保存纸张设置，请稍候查询结果",
    "正在调整速度，请稍候查询结果",
    "当前没在出图",
    "正在保存设备设置，请稍候",
    "已回原点，正在完成收尾",
    "写字机 Telnet 已断开，无法暂停",
    "写字机 Telnet 已断开，无法继续",
    "写字机 Telnet 未连接",
    "写字机未就绪（未收到版本应答）",
    "作品与当前纸张不一致，请重新生成",
    "还没画过东西，没有可重画的内容",
    "写字机未连接、未就绪或未授权",
    "无法创建试笔任务",
    "未知的手动控制动作",
    "当前任务状态不允许手动控制",
    "未能取到可信坐标，已拒绝点动以保安全，请稍后再试",
    "无法创建手动控制任务",
    "轴或速度无效（XY 3000-16000，Z 100-3000 毫米/分钟）",
    "写字机未连接、未就绪或设置未通过检查",
    "当前机型不支持调整速度",
    "当前任务状态不允许调整速度",
    "无法创建速度调整任务",
    "预览已变化，请重新核对作品",
    "这台机器一次只能画一页哦",
})
_REJECTION_LABELS = {
    "grbl_settings_mismatch": "写字机参数校验未通过，请检查设备设置",
    "上一 reset owner 尚未收敛": "上次停止操作尚未完成，请稍候",
    "无法创建 abort 归位任务": "无法开始停止归位，请检查设备状态",
    "url/preview_url 必须是同站能力地址且扩展名正确": "作品链接无效，请重新生成",
    "pages 必须是 1-20 组同站能力地址（url=gcode + preview_url=png）": "作品分页数据无效，请重新生成",
    "pages 第 1 页必须与 url/preview_url 一致": "作品与第一页预览不一致，请重新生成",
}


def _known_rejection(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 320:
        return None
    message = "".join(c for c in value if unicodedata.category(c) not in {"Cc", "Cf"}).strip()
    if message in _DEVICE_REJECTIONS:
        return _redact_secret_text(message)
    return _REJECTION_LABELS.get(message)


def _tool_result(raw: str) -> tuple[bool, str | None]:
    """判错与安全文案共用逐层遍历；外层失败不能被内层成功覆盖。"""
    value: Any = raw
    failed = False
    detail = None
    for _ in range(8):
        if isinstance(value, str):
            if value in _LEGACY_REJECTIONS:
                return True, detail or value
            if failed:
                detail = detail or _known_rejection(value)
            try:
                value = json.loads(value)
            except (TypeError, json.JSONDecodeError):
                return failed, detail
            except (RecursionError, ValueError):
                return True, detail
            if isinstance(value, str):
                # JSON字符串仍可能装着下一层对象；不能提前把编码后的error当成功。
                continue
        if not isinstance(value, dict):
            return failed, detail
        if (value.get("isError") is True or value.get("success") is False
                or value.get("ok") is False or bool(value.get("error"))):
            failed = True
            error = value.get("error")
            if isinstance(error, dict):
                error = error.get("message")
            detail = (detail or _known_rejection(error)
                      or _known_rejection(value.get("detail"))
                      or _known_rejection(value.get("reason")))
        if "data" in value and (
            value.get("success") is True or isinstance(value["data"], (dict, str))
        ):
            value = value["data"]
            continue
        content = value.get("content")
        if isinstance(content, list) and content and isinstance(content[0], dict):
            value = content[0]
            continue
        if isinstance(content, str):
            value = content
            continue
        if isinstance(value.get("text"), str) and not any(
            key in value for key in ("connected", "ready", "state", "grbl_state")
        ):
            value = value["text"]
            continue
        return failed, detail
    return True, detail  # 正常信封远少于八层；超深不能记为受理成功。


def tool_result_failed(raw: str) -> bool:
    """只认明确业务失败；每轮最多拆一层，不能越过中间层的 isError。"""
    return _tool_result(raw)[0]


def tool_result_detail(raw: str, default: str = "设备未执行该操作，请检查设备状态") -> str:
    """仅返回已知设备拒绝原因；成功或未知正文返回固定兜底文案。"""
    failed, detail = _tool_result(raw)
    return detail if failed and detail else default
