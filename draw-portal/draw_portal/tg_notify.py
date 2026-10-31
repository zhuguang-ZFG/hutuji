"""Telegram 运维通知（portal 侧注册监控）：新用户首次绑定设备等注册类事件。

- 凭据走环境变量 TG_BOT_TOKEN / TG_CHAT_ID（生产 /etc/hutuji/tg.env，600，
  凭据禁入库）；两项任一缺省 = 通知面整链禁用（静默跳过，同 subscribe_notify
  口径——通知是配件，不是功能闸）。
- 发送 fire-and-forget（daemon 线程）：任何失败只落日志（只记类型名，
  不回显），绝不阻塞/抛回绑定主链。
- _post 统一走 /opt/hutuji/deploy/tg-notify.sh 子进程：出口策略（直连 /
  jd 经 lima HTTPS 中继）只在该脚本一处维护；token 不进 python 进程 env。
  2026-09-12 实测：urllib/httpx 都没有 happy-eyeballs，Telegram AAAA 在
  无 IPv6 路由节点不可达（URLError/ConnectError 101），curl 可达。
- dedupe_key 非空时进程内去重（注册类事件一个主键只推一次；重启进程最多
  重复一条，可接受——注册是低频事件，不值得引入持久化去重）。
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading

logger = logging.getLogger(__name__)

_NOTIFY_SH = "/opt/hutuji/deploy/tg-notify.sh"
# 须大于 tg-notify.sh 中继路径最坏 64s（3×20s + 2×2s）；daemon 线程里等，不阻塞主链。
_TIMEOUT_S = 75.0
# 去重集合上限：注册量到万级前不会被撞满；满后自然回落「可能重复推送」。
_DEDUPE_CAP = 10_000

_seen_lock = threading.Lock()
_seen: set[str] = set()


def configured() -> bool:
    return bool(os.environ.get("TG_BOT_TOKEN") and os.environ.get("TG_CHAT_ID"))


def _dedupe_consume(key: str) -> bool:
    """True = 首次出现应发送；False = 已推过。"""
    with _seen_lock:
        if key in _seen:
            return False
        if len(_seen) >= _DEDUPE_CAP:
            _seen.clear()
        _seen.add(key)
        return True


def _post(token: str, chat_id: str, text: str) -> None:
    """出口收敛到 tg-notify.sh；token/chat 参数保留签名但由 shell 脚本自取。"""
    try:
        subprocess.run(
            [_NOTIFY_SH, "portal", text],
            timeout=_TIMEOUT_S,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:  # noqa: BLE001 失败只记类型，不掀主链。
        logger.warning("tg-notify send failed type=%s", type(exc).__name__)


def notify_async(text: str, dedupe_key: str | None = None) -> None:
    """fire-and-forget 发送；未配置=静默不发送；dedupe_key 已见则跳过。"""
    token = os.environ.get("TG_BOT_TOKEN", "")
    chat_id = os.environ.get("TG_CHAT_ID", "")
    if not token or not chat_id:
        return
    if dedupe_key is not None and not _dedupe_consume(dedupe_key):
        return
    host = os.environ.get("HUTUJI_TG_HOST_LABEL", "hutuji")
    threading.Thread(
        target=_post, args=(token, chat_id, f"[{host}] {text}"), daemon=True
    ).start()
