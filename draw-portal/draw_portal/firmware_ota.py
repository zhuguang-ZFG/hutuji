"""固件 OTA 业务：查版本 / 发起升级 / 读会话；busy 与错板闸。"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable

from draw_portal.crypto import decrypt_token, encrypt_token
from draw_portal.firmware_catalog import (
    build_bin_url,
    load_catalog,
    plan_check,
    recommended_for_board,
)
from draw_portal.mcp_result import tool_result_failed, unwrap_tool_text
from draw_portal.models import DeviceBinding, FirmwareOtaSession
from draw_portal.firmware_version import is_newer, parse_hutuji_version
from draw_portal.push import mint_messaging_token, push_tool_call
from draw_portal.store import Store

logger = logging.getLogger(__name__)

# 设备侧只受理不下载；短于画图推送即可
_OTA_START_TIMEOUT = 20
_STATUS_TIMEOUT = 15
# 连续十分钟没有进度即结束门户等待；设备升级/绘图忙闸仍由固件裁决。
_OTA_STALE_SECONDS = 600


# 与设备 JobIsIdleForOta / 手动控制 settled 对齐（出图中态才 busy）
_SETTLED_FOR_OTA = frozenset({"idle", "done", "error", "aborted"})


def assert_upgrade_allowed(
    *,
    job_state: str,
    board: str,
    manifest_board: str,
) -> str | None:
    state = (job_state or "").strip().lower()
    if state not in _SETTLED_FOR_OTA:
        return "busy"
    if board != manifest_board:
        return "board_mismatch"
    return None


def _parse_status_dict(raw: str) -> dict[str, Any]:
    text = unwrap_tool_text(raw)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _parse_ota_start_result(raw: str) -> dict[str, Any] | None:
    """受理必须来自设备正文；HTTP200或仅网关success不能证明设备已开始升级。"""
    for _ in range(8):
        try:
            unwrapped = unwrap_tool_text(raw)
            if unwrapped != raw:
                raw = unwrapped
                continue
            payload = json.loads(raw)
        except (TypeError, ValueError, RecursionError):
            return None
        if isinstance(payload, str):
            raw = payload
            continue
        return payload if isinstance(payload, dict) else None
    return None


def _push_healed(
    *,
    api_base: str,
    token_cipher_key: bytes,
    binding: DeviceBinding,
    store: Store,
    name: str,
    arguments: dict[str, str],
    timeout: int,
    on_token_minted: Callable[[str], None] | None = None,
) -> tuple[bool, str]:
    def target_current() -> bool:
        owner = store.get_binding_for_mac(binding.device_mac)
        active = store.get_binding_for_user(binding.user_id)
        return bool(owner and owner.user_id == binding.user_id and owner.bound_at == binding.bound_at and (
            name != "hutuji.ota_start" or (active and active.device_mac == binding.device_mac)
        ))

    if not target_current():
        return False, "设备绑定已变化，请刷新后重试"
    current = store.get_binding_for_mac(binding.device_mac)
    if current is None or current.user_id != binding.user_id or current.bound_at != binding.bound_at:
        return False, "设备绑定已变化，请刷新后重试"
    binding = current
    token = decrypt_token(token_cipher_key, binding.device_token_enc)
    ok, result = push_tool_call(
        api_base=api_base,
        device_token=token,
        name=name,
        arguments=arguments,
        timeout=timeout,
    )
    if not ok and result in ("HTTP 401", "HTTP 403", "HTTP 404"):
        fresh = mint_messaging_token(api_base=api_base, mac=binding.device_mac)
        if fresh:
            if not target_current():
                return False, "设备绑定已变化，请刷新后重试"
            enc = encrypt_token(token_cipher_key, fresh)
            if not store.update_binding_token_if_current(binding, enc, expected_token_enc=binding.device_token_enc):
                return False, "设备凭据已更新，请刷新后重试"
            if on_token_minted:
                on_token_minted(enc)
            ok, result = push_tool_call(
                api_base=api_base,
                device_token=fresh,
                name=name,
                arguments=arguments,
                timeout=timeout,
            )
    if ok and tool_result_failed(result):
        return False, "设备未接受操作，请刷新状态后重试"
    return ok, result


def _fetch_device_status(
    *,
    api_base: str,
    token_cipher_key: bytes,
    binding: DeviceBinding,
    store: Store,
) -> tuple[bool, dict[str, Any], str]:
    ok, raw = _push_healed(
        api_base=api_base,
        token_cipher_key=token_cipher_key,
        binding=binding,
        store=store,
        name="hutuji.status",
        arguments={},
        timeout=_STATUS_TIMEOUT,
    )
    if not ok:
        return False, {}, raw
    return True, _parse_status_dict(raw), raw


def check_firmware(
    *,
    store: Store,
    binding: DeviceBinding,
    catalog_path: str,
    api_base: str,
    token_cipher_key: bytes,
    board_fallback: str | None = None,
) -> dict[str, Any]:
    if not catalog_path:
        return {
            "update_available": False,
            "current": None,
            "latest": None,
            "notes": "",
            "reason": "check_failed",
            "detail": "固件目录未配置",
        }
    try:
        catalog = load_catalog(catalog_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("firmware catalog load failed: %s", type(exc).__name__)
        return {
            "update_available": False,
            "current": None,
            "latest": None,
            "notes": "",
            "reason": "check_failed",
            "detail": "固件目录不可用",
        }

    ok, status, err = _fetch_device_status(
        api_base=api_base,
        token_cipher_key=token_cipher_key,
        binding=binding,
        store=store,
    )
    if not ok:
        return {
            "update_available": False,
            "current": None,
            "latest": None,
            "notes": "",
            "reason": "check_failed",
            "detail": err,
        }

    board = str(status.get("board") or board_fallback or "").strip()
    current = str(status.get("firmware_version") or "").strip()
    if not board or not current:
        return {
            "update_available": False,
            "current": current or None,
            "latest": None,
            "notes": "",
            "reason": "check_failed",
            "detail": "设备未上报 board/firmware_version",
            "board": board or None,
            "device_state": status.get("state"),
        }

    try:
        plan = plan_check(board=board, current_version=current, catalog=catalog,
                          device_mac=binding.device_mac)
    except ValueError:
        return {"update_available": False, "reason": "check_failed", "detail": "固件灰度配置不可用"}
    plan["board"] = board
    plan["device_state"] = status.get("state")
    return plan


def start_upgrade(
    *,
    store: Store,
    binding: DeviceBinding,
    catalog_path: str,
    public_base: str,
    signing_secret: str,
    api_base: str,
    token_cipher_key: bytes,
    board_fallback: str | None = None,
) -> dict[str, Any]:
    if is_upgrading(store, binding.user_id):
        return {"status": "error", "detail": "busy"}
    session = store.reserve_firmware_ota_session(binding.user_id, binding.device_mac)
    if session is None:
        return {"status": "error", "detail": "busy"}
    outcome: dict[str, Any] = {"status": "error", "detail": "check_failed"}
    try:
        outcome = _start_upgrade_claimed(
            store=store, binding=binding, session=session, catalog_path=catalog_path,
            public_base=public_base, signing_secret=signing_secret, api_base=api_base,
            token_cipher_key=token_cipher_key, board_fallback=board_fallback,
        )
        return outcome
    finally:
        if outcome.get("status") in {"started", "unknown"}:
            store.accept_firmware_ota_start(session, uncertain=outcome["status"] == "unknown")
        else:
            detail = outcome.get("detail")
            reason = detail if detail in {"busy", "board_mismatch", "check_failed", "download_failed",
                                         "verify_failed", "write_failed", "up_to_date"} else None
            store.fail_firmware_ota_start(session, reason)


def _start_upgrade_claimed(
    *, store: Store, binding: DeviceBinding, session: FirmwareOtaSession,
    catalog_path: str, public_base: str, signing_secret: str, api_base: str,
    token_cipher_key: bytes, board_fallback: str | None,
) -> dict[str, Any]:

    plan = check_firmware(
        store=store,
        binding=binding,
        catalog_path=catalog_path,
        api_base=api_base,
        token_cipher_key=token_cipher_key,
        board_fallback=board_fallback,
    )
    if plan.get("reason") == "check_failed":
        return {"status": "error", "detail": plan.get("detail") or "check_failed"}
    if not plan.get("update_available"):
        return {
            "status": "error",
            "detail": plan.get("reason") or "up_to_date",
        }

    board = str(plan.get("board") or "")
    catalog = load_catalog(catalog_path)
    try:
        entry = recommended_for_board(catalog, board, binding.device_mac)
    except ValueError:
        return {"status": "error", "detail": "check_failed"}
    if entry is None:
        return {"status": "error", "detail": "board_mismatch"}
    if entry.get('version') != plan.get('latest'):
        return {"status": "error", "detail": "check_failed"}

    ok, status, err = _fetch_device_status(
        api_base=api_base,
        token_cipher_key=token_cipher_key,
        binding=binding,
        store=store,
    )
    if not ok:
        return {"status": "error", "detail": err}

    job_state = str(status.get("state") or "")
    device_board = str(status.get("board") or board_fallback or "").strip()
    reject = assert_upgrade_allowed(
        job_state=job_state,
        board=device_board,
        manifest_board=board,
    )
    if reject:
        return {"status": "error", "detail": reject}

    version = str(entry.get("version") or plan.get("latest") or "")
    sha256 = str(entry.get("sha256") or "")
    relpath = str(entry.get("relpath") or "")
    if not version or not relpath:
        return {"status": "error", "detail": "check_failed"}

    # 两次查询间设备可能已从其它入口升级；发送前必须以最新版本裁决。
    current = status.get("firmware_version")
    if (not isinstance(current, str) or parse_hutuji_version(current) is None
            or parse_hutuji_version(version) is None):
        return {"status": "error", "detail": "check_failed"}
    if not is_newer(version, current):
        return {"status": "error", "detail": "up_to_date"}

    url = build_bin_url(
        public_base,
        relpath,
        secret=signing_secret or None,
    )
    if not store.set_firmware_ota_target(session, version):
        return {"status": "error", "detail": "busy"}

    ok, result = _push_healed(
        api_base=api_base,
        token_cipher_key=token_cipher_key,
        binding=binding,
        store=store,
        name="hutuji.ota_start",
        arguments={
            "url": url,
            "version": version,
            "board": board,
            "sha256": sha256,
        },
        timeout=_OTA_START_TIMEOUT,
    )
    if not ok:
        # urllib 的实际异常类名有大写；超时、响应截断或断连均不能证明命令未送达。
        uncertain = result == "HTTP 504" or str(result).casefold() in {
            "网络 timeout", "网络 timeouterror", "网络 remotedisconnected",
            "网络 connectionreseterror", "网络 incompleteread",
        }
        if uncertain:
            # 设备消息网关 504/读超时 ≠ 未送达（2026-09-11 实证：504 当轮设备
            # 已进入下载并自升成功）。会话保持 upgrading，由 ota_status 轮询
            # 与版本对账给出真实终态；前端收到 unknown 继续轮询不报错。
            return {"status": "unknown", "target_version": version, "detail": result}
        return {"status": "error", "detail": result}

    # 设备若立即以 reason token 拒升（busy 等），会话回退
    payload = _parse_ota_start_result(result)
    if isinstance(payload, dict):
        device_reason = payload.get("reason") or payload.get("detail")
        if isinstance(device_reason, str) and device_reason in {
            "busy",
            "board_mismatch",
            "check_failed",
            "download_failed",
            "verify_failed",
            "write_failed",
        }:
            return {"status": "error", "detail": str(device_reason)}

    if not isinstance(payload, dict) or payload.get("ok") is not True:
        # 保持uncertain，旧failed不能解除本轮占位；后续由设备进度或版本对账确认。
        return {"status": "unknown", "target_version": version,
                "detail": "升级受理结果尚未确认，请等待设备状态更新"}

    return {"status": "started", "target_version": version, "board": board}


def get_ota_status(
    *,
    store: Store,
    binding: DeviceBinding,
    api_base: str,
    token_cipher_key: bytes,
) -> dict[str, Any]:
    session = store.get_firmware_ota_session(binding.user_id)
    if session and session.device_mac != binding.device_mac:
        session = None
    ok, status, _err = _fetch_device_status(
        api_base=api_base, token_cipher_key=token_cipher_key, binding=binding, store=store,
    )
    ota_now = status.get("ota") if ok else None
    if session and session.state in {"starting", "uncertain", "upgrading"}:
        stale = session.updated_at <= time.time() - _OTA_STALE_SECONDS
        device_upgrading = (
            isinstance(ota_now, dict)
            and str(ota_now.get("state") or "").strip().lower() == "upgrading"
        )
        version_match = bool(
            session.target_version
            and str(status.get("firmware_version") or "").strip() == session.target_version
        )
        # 设备离线刷写或仍在 upgrading 时不按墙钟放锁；可达且已离开升级、版本未对上才过期。
        if stale and ok and not device_upgrading and not version_match:
            store.expire_firmware_ota_session(
                binding.user_id, cutoff=time.time() - _OTA_STALE_SECONDS
            )
            session = store.get_firmware_ota_session(binding.user_id)
            if session and session.device_mac != binding.device_mac:
                session = None
    ota = ota_now if ok else None
    # starting 尚未收到命令应答，设备仍可能携带上一轮的失败/进度。
    # 外部继续显示 upgrading 并拦出图，内部阶段也使迟到查询的 CAS 失效。
    if session and session.state != "starting" and ok:
        state, progress, reason = session.state, session.progress, session.reason
        if isinstance(ota, dict):
            device_state = str(ota.get("state") or "").strip().lower()
            device_progress = ota.get("progress")
            if state == "uncertain" and device_state == "upgrading":
                # 504 无送达保证；先看到设备开始，才允许消费随后报告的失败。
                state = "upgrading"
            if state == "upgrading" and device_state in {"failed", "rolled_back"}:
                state = device_state
                reason = str(ota.get("reason") or device_state)
            elif (state == "upgrading" and device_state == "upgrading"
                  and isinstance(device_progress, (int, float))
                  and not isinstance(device_progress, bool) and progress < device_progress <= 100):
                progress = int(device_progress)
        # 设备重启会丢失 ota 临时状态；只有同目标版本才确认成功。
        if (session.target_version
                and str(status.get("firmware_version") or "").strip() == session.target_version
                and (session.state in {"upgrading", "uncertain"} or session.reason == "status_timeout")):
            state, progress, reason = "ok", 100, None
        if (state, progress, reason) != (session.state, session.progress, session.reason):
            session = store.update_firmware_ota_session(
                session, state=state, progress=progress, reason=reason, updated_at=time.time(),
            )
    # 迟到回包被条件更新拒绝后，以当前会话为准；另一台设备的会话不返回。
    session = store.get_firmware_ota_session(binding.user_id)
    if session and session.device_mac != binding.device_mac:
        session = None
    out: dict[str, Any] = {
        "state": ("upgrading" if session.state in {"starting", "uncertain"} else session.state) if session else "idle",
        "progress": session.progress if session else 0,
        "reason": session.reason if session else None,
        "target_version": session.target_version if session else None,
        "updated_at": session.updated_at if session else None,
    }
    if ok:
        if isinstance(ota, dict):
            out["ota"] = ota
        out["firmware_version"] = status.get("firmware_version")
        out["device_state"] = status.get("state")
    return out


def is_upgrading(store: Store, user_id: str, *, device_mac: str | None = None) -> bool:
    session = store.get_firmware_ota_session(user_id)
    in_flight = bool(
        session and session.state in {"starting", "uncertain", "upgrading"}
        and (device_mac is None or session.device_mac == device_mac)
    )
    # 已登记目标则查询失败也不放锁，由 get_ota_status 在设备可达且离开升级后过期。
    if in_flight and session is not None and session.target_version:
        return True
    store.expire_firmware_ota_session(user_id, cutoff=time.time() - _OTA_STALE_SECONDS)
    session = store.get_firmware_ota_session(user_id)
    return bool(session and session.state in {"starting", "uncertain", "upgrading"}
                and (device_mac is None or session.device_mac == device_mac))
