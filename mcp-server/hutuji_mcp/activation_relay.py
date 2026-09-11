"""激活码中转：新设备把小智 OTA 下发的 6 位激活码 POST 给本端点，由服务器
持控制台 token 代为绑定，用户无需开网页手输（2026-08-20「联网扫码后无感
写入机器码」需求；直连 serial-only 预注册已实测不存在：控制台绑定端点强制
verificationCode，见 docs 记录）。

安全口径：
- 激活码即秘密本身（单次、短时效），攻击者拿到别人有效码才能代绑——
  转发就是控制台公开 API 的语义，不额外放大风险；公网限速由 nginx 兜底。
- 日志脱敏：永不记激活码；只记 MAC 与结果标签。
- 上行失败/超时统一塌成 502，不回显小智云内部报文；验证码被拒单独
  400（设备端无论如何都会继续走「屏显激活码」人工兜底，不依赖本端结果）。
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time

import httpx

logger = logging.getLogger(__name__)

_TG_TIMEOUT_S = 15.0
# 出口收敛到 tg-notify.sh：直连/jd 经 lima SSH 中继只在该脚本一处维护；
# 2026-09-12 实测 urllib/httpx 无 happy-eyeballs，Telegram AAAA 在无 IPv6
# 路由节点不可达（Errno 101），curl 可达。
_TG_NOTIFY_SH = "/opt/hutuji/deploy/tg-notify.sh"


def _tg_post(token: str, chat_id: str, text: str) -> None:
    """发一条 Telegram 运维通知；失败只落日志（不回显细节）。"""
    try:
        subprocess.run(
            [_TG_NOTIFY_SH, "activate-relay", text],
            timeout=_TG_TIMEOUT_S,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:  # noqa: BLE001 通知面绝不掀主链
        logger.warning("activate-relay: tg-notify send failed type=%s", type(exc).__name__)


def notify_bound_async(mac: str) -> None:
    """新设备代绑成功 → Telegram 即时通知（fire-and-forget，daemon 线程）。

    凭据走 TG_BOT_TOKEN/TG_CHAT_ID 环境变量（/etc/hutuji/tg.env，仅生产）；
    未配置=通知面禁用，静默返回，不影响绑定主链。
    """
    token = os.environ.get("TG_BOT_TOKEN", "")
    chat_id = os.environ.get("TG_CHAT_ID", "")
    if not token or not chat_id:
        return
    hostname = os.environ.get("HUTUJI_TG_HOST_LABEL", "hutuji")
    threading.Thread(
        target=_tg_post,
        args=(token, chat_id, f"[{hostname}] 新设备激活绑定 mac={mac}"),
        daemon=True,
    ).start()

_MAC_RE = re.compile(r"^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$")
# 小智 OTA 激活码实测为 6 位数字；放宽到 4~8 位以免服务端改长度时误拒。
_CODE_RE = re.compile(r"^\d{4,8}$")

# 绑定上行超时：控制台在境内，8s 覆盖抖动又不过长占用 uvicorn worker。
_UPSTREAM_TIMEOUT_S = 8.0
# 设备列表缓存：绑定前的幂等检查（同一台机器开机/重连会重复 POST），
# 60s 内不重复拉全量列表。
_DEVICE_LIST_TTL_S = 60.0

_device_list_cache: tuple[float, set[str]] | None = None


class RelayDisabledError(Exception):
    """未配置控制台 token / agent id：中转不可用，设备走人工输码兜底。"""


class InvalidCodeError(Exception):
    """小智云拒绝该验证码（过期/已用/不匹配）。"""


class UpstreamError(Exception):
    """小智云网络/5xx/非预期应答；对外统一 502，细节只进日志（脱敏后）。"""


def validate_mac(mac: str) -> str:
    if not isinstance(mac, str) or not _MAC_RE.fullmatch(mac):
        raise ValueError("mac 必须是 aa:bb:cc:dd:ee:ff 形式")
    return mac.lower()


def validate_code(code: str) -> str:
    if not isinstance(code, str) or not _CODE_RE.fullmatch(code):
        raise ValueError("code 必须是 4~8 位数字")
    return code


def relay_configured(settings) -> bool:
    return bool(settings.xiaozhi_console_token and settings.xiaozhi_agent_id)


async def _list_bound_macs(settings, client: httpx.AsyncClient) -> set[str]:
    """拉 agent 已绑设备 MAC 集合（带 60s 缓存）。失败按 UpstreamError。"""
    global _device_list_cache
    now = time.monotonic()
    if _device_list_cache is not None and now - _device_list_cache[0] < _DEVICE_LIST_TTL_S:
        return _device_list_cache[1]
    resp = await client.get(
        f"{settings.xiaozhi_api_base}/agents/{settings.xiaozhi_agent_id}/devices",
        headers={"Authorization": f"Bearer {settings.xiaozhi_console_token}"},
        timeout=_UPSTREAM_TIMEOUT_S,
    )
    if resp.status_code != 200:
        raise UpstreamError(f"devices list status={resp.status_code}")
    try:
        data = resp.json().get("data", [])
        macs = {
            str(d.get("mac_address", "")).lower()
            for d in data
            if isinstance(d, dict) and d.get("mac_address")
        }
    except (ValueError, AttributeError) as exc:
        raise UpstreamError("devices list shape") from exc
    _device_list_cache = (now, macs)
    return macs


def invalidate_device_list_cache() -> None:
    """绑定成功后作废缓存；测试也用它复位全局态。"""
    global _device_list_cache
    _device_list_cache = None


async def bind_device(settings, mac: str, code: str) -> str:
    """代绑一台设备；返回 "bound" / "already_bound"。

    窄异常：RelayDisabledError → 503；InvalidCodeError → 400；
    UpstreamError → 502。由路由层映射，不回显内部细节。
    """
    if not relay_configured(settings):
        raise RelayDisabledError()
    mac = validate_mac(mac)
    code = validate_code(code)
    try:
        async with httpx.AsyncClient() as client:
            if mac in await _list_bound_macs(settings, client):
                logger.info("activate-relay: mac=%s already bound", mac)
                return "already_bound"
            resp = await client.post(
                f"{settings.xiaozhi_api_base}/agents/{settings.xiaozhi_agent_id}/devices",
                headers={"Authorization": f"Bearer {settings.xiaozhi_console_token}"},
                json={"mac_address": mac, "verificationCode": code},
                timeout=_UPSTREAM_TIMEOUT_S,
            )
    except httpx.HTTPError as exc:
        # 网络异常只记类型（脱敏硬约定：异常对象可能带 URL/内部报文）。
        raise UpstreamError(type(exc).__name__) from exc
    if resp.status_code == 200:
        invalidate_device_list_cache()
        notify_bound_async(mac)
        logger.info("activate-relay: mac=%s bound", mac)
        return "bound"
    if resp.status_code == 400:
        # 控制台对无效码的应答码是 INVALID_VERIFICATION_CODE（2026-08-20 实测）；
        # 其余 400（形状不符等）按上游异常处理。只读 code 字段，不回显 message。
        try:
            err_code = resp.json().get("code", "")
        except (ValueError, AttributeError):
            # 合法 JSON 但非 dict（list/str/null）时 .get 抛 AttributeError——
            # 与 _list_bound_macs 同口径按形状不符走 502，不让设备收 500。
            err_code = ""
        if err_code == "INVALID_VERIFICATION_CODE":
            logger.info("activate-relay: mac=%s code rejected", mac)
            raise InvalidCodeError()
        raise UpstreamError(f"bind 400 code={err_code!r}")
    # token 过期（401/403）是运维事故：日志点名原因但不回显给设备。
    if resp.status_code in (401, 403):
        logger.error("activate-relay: console token rejected status=%d", resp.status_code)
        raise UpstreamError("console token rejected")
    raise UpstreamError(f"bind status={resp.status_code}")
