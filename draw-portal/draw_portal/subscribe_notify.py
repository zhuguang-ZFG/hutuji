"""一次性订阅消息通知（生成完成/失败时，把离开小程序的用户拉回来）。

- 模板 ID 走 DRAW_PORTAL_WX_SUBSCRIBE_TEMPLATE_ID，空 = 整链禁用（静默跳过，
  不影响画图/写的主链——通知是配件，不是功能闸）。
- 字段键位走 DRAW_PORTAL_WX_SUBSCRIBE_FIELDS（JSON）。默认 thing1/phrase2/time3：
  MP 后台建模板时按「描述(thing)/状态(phrase)/时间(time)」顺序取号即可对上；
  键位对不上微信会回 47003，只落 warning 不断链。
- access_token 复用 chat.get_wx_access_token（同一张票，缓存 7200s-300s）。
- 发送 fire-and-forget（daemon 线程）：任何失败只落日志，绝不阻塞/抛回主链。
- thing ≤20 字、phrase ≤5 字、time ≤32 字（微信侧硬约束，超长截断）。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

_DEFAULT_FIELDS = {"thing": "thing1", "phrase": "phrase2", "time": "time3"}
_NOTIFY_PAGE = "pages/index/index"

_jobs_lock = threading.Lock()
# 一个用户短时间只发一条成功通知：多页文档/连发任务合并为最近一次
_recent: dict[str, float] = {}
_RECENT_WINDOW_SECONDS = 5.0


def parse_fields(raw: str | None) -> dict[str, str]:
    """解析字段键位 JSON；空/坏值一律回落默认键位。"""
    if not raw or not raw.strip():
        return dict(_DEFAULT_FIELDS)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("subscribe fields JSON invalid, using defaults")
        return dict(_DEFAULT_FIELDS)
    if not isinstance(data, dict):
        logger.warning("subscribe fields JSON not an object, using defaults")
        return dict(_DEFAULT_FIELDS)
    fields = dict(_DEFAULT_FIELDS)
    for key in ("thing", "phrase", "time"):
        value = str(data.get(key) or "").strip()
        if value:
            fields[key] = value
    return fields


def enabled(config: Any) -> bool:
    return bool(getattr(config, "subscribe_template_id", "") and getattr(config, "wx_appid", ""))


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text[:limit] if text else "-"


def _post_subscribe_send(
    *, appid: str, secret: str, openid: str, template_id: str, data: dict[str, dict[str, str]]
) -> int:
    from draw_portal.chat import get_wx_access_token, invalidate_wx_access_token

    errcode = 0
    for attempt in range(2):
        token = get_wx_access_token(appid, secret)
        payload = json.dumps(
            {
                "touser": openid,
                "template_id": template_id,
                "page": _NOTIFY_PAGE,
                "data": data,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        try:
            req = Request(
                f"https://api.weixin.qq.com/cgi-bin/message/subscribe/send?access_token={token}",
                data=payload,
                method="POST",
            )
            req.add_header("Content-Type", "application/json")
            with urlopen(req, timeout=8) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except (HTTPError, URLError, OSError, json.JSONDecodeError) as exc:
            logger.warning("subscribe send network error: %s", type(exc).__name__)
            return -1
        errcode = int(body.get("errcode") or 0)
        if errcode in (40001, 42001) and attempt == 0:
            invalidate_wx_access_token()
            continue
        break
    return errcode


def notify_job_result(
    config: Any,
    *,
    openid: str,
    status: str,
    title: str,
    fields: dict[str, str] | None = None,
) -> None:
    """同步发送一次订阅消息；未启用/openid 缺失直接跳过，失败只落日志。"""
    if not enabled(config):
        return
    if not openid or status not in ("success", "failed"):
        return
    appid = str(config.wx_appid)
    secret = str(config.wx_secret)
    template_id = str(config.subscribe_template_id)
    keys = fields or dict(_DEFAULT_FIELDS)
    data = {
        keys["thing"]: {"value": _clip(title, 20)},
        keys["phrase"]: {"value": "生成成功" if status == "success" else "生成失败"},
        keys["time"]: {"value": time.strftime("%Y-%m-%d %H:%M")},
    }
    errcode = _post_subscribe_send(
        appid=appid,
        secret=secret,
        openid=openid,
        template_id=template_id,
        data=data,
    )
    if errcode == 0:
        return
    if errcode == 43101:
        # 用户拒绝/未订阅该模板：一次性授权未拿到，属正常路径
        logger.info("subscribe send skipped (43101 not subscribed)")
        return
    logger.warning("subscribe send failed errcode=%s", errcode)


def notify_job_result_async(config: Any, *, user_id: str, openid: str, status: str, title: str) -> None:
    """fire-and-forget 入口；同用户 5s 窗口内的重复通知只保留最近一条。"""
    if not enabled(config) or not openid:
        return
    now = time.monotonic()
    with _jobs_lock:
        last = _recent.get(f"{user_id}:{status}", 0.0)
        if now - last < _RECENT_WINDOW_SECONDS:
            return
        _recent[f"{user_id}:{status}"] = now
        # 惰性清理：留最近 64 条即可
        if len(_recent) > 64:
            keep = sorted(_recent.items(), key=lambda kv: kv[1])[-64:]
            _recent.clear()
            _recent.update(dict(keep))

    def _run() -> None:
        try:
            notify_job_result(config, openid=openid, status=status, title=title)
        except Exception as exc:  # 通知绝不冒泡进主链
            logger.warning("subscribe notify error: %s", type(exc).__name__)

    threading.Thread(target=_run, daemon=True, name="hutuji-subscribe-notify").start()
