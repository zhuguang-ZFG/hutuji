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
from draw_portal.models import DeviceBinding
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
        return bool(owner and owner.user_id == binding.user_id and (
            name != "hutuji.ota_start" or (active and active.device_mac == binding.device_mac)
        ))

    if not target_current():
        return False, "设备绑定已变化，请刷新后重试"
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
            store.update_binding_token(binding.user_id, device_token_enc=enc, device_mac=binding.device_mac)
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

    plan = plan_check(board=board, current_version=current, catalog=catalog)
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
    entry = recommended_for_board(catalog, board)
    if entry is None:
        return {"status": "error", "detail": "board_mismatch"}

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

    url = build_bin_url(
        public_base,
        relpath,
        secret=signing_secret or None,
    )
    store.upsert_firmware_ota_session(
        user_id=binding.user_id,
        device_mac=binding.device_mac,
        state="upgrading",
        progress=0,
        reason=None,
        target_version=version,
    )

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
        uncertain = result == "HTTP 504" or str(result).startswith("网络 timeout")
        if uncertain:
            # 设备消息网关 504/读超时 ≠ 未送达（2026-09-11 实证：504 当轮设备
            # 已进入下载并自升成功）。会话保持 upgrading，由 ota_status 轮询
            # 与版本对账给出真实终态；前端收到 unknown 继续轮询不报错。
            return {"status": "unknown", "target_version": version, "detail": result}
        store.upsert_firmware_ota_session(
            user_id=binding.user_id,
            device_mac=binding.device_mac,
            state="failed",
            progress=0,
            reason=None,
            target_version=version,
        )
        return {"status": "error", "detail": result}

    # 设备若立即以 reason token 拒升（busy 等），会话回退
    text = unwrap_tool_text(result)
    try:
        payload = json.loads(text) if text else {}
    except json.JSONDecodeError:
        payload = {}
    if isinstance(payload, dict):
        device_reason = payload.get("reason") or payload.get("detail")
        if device_reason in {
            "busy",
            "board_mismatch",
            "check_failed",
            "download_failed",
            "verify_failed",
            "write_failed",
        }:
            store.upsert_firmware_ota_session(
                user_id=binding.user_id,
                device_mac=binding.device_mac,
                state="failed",
                progress=0,
                reason=str(device_reason),
                target_version=version,
            )
            return {"status": "error", "detail": str(device_reason)}

    return {"status": "started", "target_version": version, "board": board}


def get_ota_status(
    *,
    store: Store,
    binding: DeviceBinding,
    api_base: str,
    token_cipher_key: bytes,
) -> dict[str, Any]:
    store.expire_firmware_ota_session(binding.user_id, cutoff=time.time() - _OTA_STALE_SECONDS)
    session = store.get_firmware_ota_session(binding.user_id)
    if session and session.device_mac != binding.device_mac:
        session = None
    ok, status, _err = _fetch_device_status(
        api_base=api_base, token_cipher_key=token_cipher_key, binding=binding, store=store,
    )
    ota = status.get("ota") if ok else None
    if session and ok:
        state, progress, reason = session.state, session.progress, session.reason
        if isinstance(ota, dict):
            device_state = str(ota.get("state") or "").strip().lower()
            device_progress = ota.get("progress")
            if device_state in {"failed", "rolled_back"}:
                state = device_state
                reason = str(ota.get("reason") or device_state)
            elif (state == "upgrading" and device_state == "upgrading"
                  and isinstance(device_progress, (int, float))
                  and not isinstance(device_progress, bool) and progress < device_progress <= 100):
                progress = int(device_progress)
        # 设备重启会丢失 ota 临时状态；只有同目标版本才确认成功。
        if (session.target_version
                and str(status.get("firmware_version") or "").strip() == session.target_version
                and (session.state == "upgrading" or session.reason == "status_timeout")):
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
        "state": session.state if session else "idle",
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
    store.expire_firmware_ota_session(user_id, cutoff=time.time() - _OTA_STALE_SECONDS)
    session = store.get_firmware_ota_session(user_id)
    return bool(session and session.state == "upgrading"
                and (device_mac is None or session.device_mac == device_mac))
