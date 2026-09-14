"""设备纸张读写、生成快照和发送前核对；机器范围不从 Grbl 厂商设置推导。"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from hutuji_mcp.paper_config import PaperConfig, PaperConfigError
from hutuji_mcp.paper_artifacts import PaperArtifactError, read_paper_metadata

from .crypto import decrypt_token, encrypt_token
from .mcp_result import tool_result_failed, unwrap_tool_text


class PaperSettingsError(ValueError):
    def __init__(self, message: str, *, status: int = 409):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class PaperSnapshot:
    paper: PaperConfig
    device_mac: str = ""
    machine: str = "paper"
    capable: bool = False
    online: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"config": self.paper.to_dict(), "marker": self.paper.marker,
                "device_mac": self.device_mac, "machine": self.machine,
                "capable": self.capable, "online": self.online,
                "page_width_mm": self.paper.page_width_mm, "page_height_mm": self.paper.page_height_mm}


def check_owner(store, binding, *, require_active: bool = False):
    current = store.get_binding_for_mac(binding.device_mac)
    if (current is None or current.user_id != binding.user_id or current.bound_at != binding.bound_at):
        raise PaperSettingsError("设备绑定已变化，请刷新后重试")
    if require_active:
        active = store.get_binding_for_user(binding.user_id)
        if active is None or active.device_mac != binding.device_mac:
            raise PaperSettingsError("控制设备已切换，请刷新后重试")
    return current


def update_token(store, binding, encrypted: str) -> None:
    if not store.update_binding_token_if_current(binding, encrypted):
        raise PaperSettingsError("设备绑定已变化，请刷新后重试")


def _invoke(store, config, binding, name, arguments, *, require_active=False, call_tool=None):
    from .push import mint_messaging_token, push_tool_call

    transport = call_tool or push_tool_call
    current = check_owner(store, binding, require_active=require_active)
    try:
        token = decrypt_token(config.token_cipher_key, current.device_token_enc)
    except ValueError:
        # 旧密文损坏也可按精确 MAC 重建连接，不把解密细节或 500 留给用户。
        ok, raw = False, "HTTP 401"
    else:
        ok, raw = transport(api_base=config.xiaozhi_api_base, device_token=token,
                            name=name, arguments=arguments, timeout=10)
    if not ok and raw in ("HTTP 401", "HTTP 403", "HTTP 404"):
        check_owner(store, binding, require_active=require_active)
        fresh = mint_messaging_token(api_base=config.xiaozhi_api_base, mac=binding.device_mac)
        if fresh:
            check_owner(store, binding, require_active=require_active)
            if not store.update_binding_token_if_current(binding, encrypt_token(config.token_cipher_key, fresh)):
                raise PaperSettingsError("设备绑定已变化，请刷新后重试")
            ok, raw = transport(api_base=config.xiaozhi_api_base, device_token=fresh,
                                name=name, arguments=arguments, timeout=10)
    check_owner(store, binding, require_active=require_active)
    return ok and not tool_result_failed(raw), raw


def parse_status(raw: str, machine: str) -> PaperSnapshot:
    try:
        if not isinstance(raw, str) or len(raw) > 65536:
            raise ValueError("invalid status size")
        for _ in range(8):
            unwrapped = unwrap_tool_text(raw)
            if unwrapped == raw:
                break
            raw = unwrapped
        payload = json.loads(raw)
    except (ValueError, TypeError, RecursionError):
        raise PaperSettingsError("设备纸张状态不可读", status=502) from None
    if not isinstance(payload, dict):
        raise PaperSettingsError("设备纸张状态不可读", status=502)
    machine = payload.get("plotter_sku") or machine
    if machine not in ("paper", "nopaper"):
        raise PaperSettingsError("无法确认设备机型，请重新连接")
    online = payload.get("connected") is True and payload.get("ready") is True
    # v0.5 的 paper 已是有纸/无纸遥测字符串；配置使用独立字段，保留旧回包形状。
    section = payload.get("paper_config")
    if "paper_config" not in payload:
        return PaperSnapshot(PaperConfig.default_for(machine), machine=machine, online=online)
    try:
        if not isinstance(section, dict) or type(section.get("schema")) is not int or section["schema"] != 1:
            raise PaperConfigError("unsupported schema")
        if type(section.get("updating", False)) is not bool:
            raise PaperConfigError("invalid update state")
        paper = PaperConfig.from_dict(section.get("config"))
        if section.get("marker") != paper.marker:
            raise PaperConfigError("marker mismatch")
    except PaperConfigError:
        raise PaperSettingsError("设备纸张配置无效，请在设备上检查设置", status=502) from None
    if section.get("updating") is True:
        # 持久化事务期间可能读到旧配置；不能据此生成、发送或再发一次修改。
        raise PaperSettingsError("设备正在保存纸张设置，请稍候刷新", status=409)
    return PaperSnapshot(paper, machine=machine, capable=True, online=online)


def read_settings(store, config, binding, *, require_live=False, require_active=False, call_tool=None) -> PaperSnapshot:
    if binding is None:
        if require_live:
            raise PaperSettingsError("请先绑定设备", status=400)
        return PaperSnapshot(PaperConfig())
    ok, raw = _invoke(store, config, binding, "hutuji.status", {},
                      require_active=require_active, call_tool=call_tool)
    if ok:
        parsed = parse_status(raw, binding.machine_type or "paper")
        snapshot = PaperSnapshot(parsed.paper, binding.device_mac, parsed.machine, parsed.capable, parsed.online)
        if require_live and not snapshot.online:
            raise PaperSettingsError("写字机未就绪，请重新连接后重试", status=503)
        if snapshot.online:
            if not store.update_device_paper(binding, json.dumps(snapshot.paper.to_dict()),
                                             capable=snapshot.capable, machine=snapshot.machine):
                raise PaperSettingsError("设备绑定已变化，请刷新后重试")
            return snapshot
        # S3 刚启动时尚未读到机型，会暂报 paper；只有就绪读回可覆盖确认过的缓存。
    if require_live:
        raise PaperSettingsError("设备离线或未响应，请重新连接后重试", status=503)
    current = check_owner(store, binding, require_active=require_active)
    cached = store.get_device_paper(binding)
    if cached:
        try:
            paper = PaperConfig.from_dict(json.loads(cached["config_json"]))
        except (ValueError, TypeError):
            raise PaperSettingsError("缓存的纸张配置无效，请连接设备刷新") from None
        return PaperSnapshot(paper, binding.device_mac, current.machine_type or "paper", bool(cached["capable"]), False)
    return PaperSnapshot(PaperConfig.default_for(current.machine_type or "paper"), binding.device_mac,
                         current.machine_type or "paper")


def update_settings(store, config, binding, desired: PaperConfig, *, expected_marker: str,
                    range_confirmed: bool, call_tool=None) -> PaperSnapshot:
    before = read_settings(store, config, binding, require_live=True, require_active=True, call_tool=call_tool)
    if not before.capable:
        raise PaperSettingsError("设备固件尚不支持纸张设置，请先升级")
    if before.paper.marker != expected_marker:
        raise PaperSettingsError("纸张设置已变化，请刷新后重试")
    if desired.requires_range_confirmation(before.paper) and not range_confirmed:
        raise PaperSettingsError("增大可绘制范围前，请确认已按设备实际行程核对", status=400)
    ok, raw = _invoke(store, config, binding, "hutuji.paper", {
        "config": json.dumps(desired.to_dict(), separators=(",", ":")),
        "expected": expected_marker, "range_confirmed": range_confirmed,
    }, require_active=True, call_tool=call_tool)
    if not ok:
        raise PaperSettingsError("设备未接受纸张设置，请确认任务已结束", status=502)
    reply = parse_status(raw, before.machine)
    if not reply.capable or reply.paper != desired:
        raise PaperSettingsError("设备未确认本次纸张设置，请刷新后重试", status=502)
    after = read_settings(store, config, binding, require_live=True, require_active=True, call_tool=call_tool)
    if after.paper != desired:
        raise PaperSettingsError("纸张读回与本次设置不一致，请刷新后重试", status=502)
    return after


def write_job_context(job_dir: Path, snapshot: PaperSnapshot) -> None:
    write_context(job_dir / "job-context.json", snapshot)


def write_context(path: Path, snapshot: PaperSnapshot) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "device_mac": snapshot.device_mac,
        "machine": snapshot.machine, "paper": snapshot.paper.to_dict()}, ensure_ascii=True), encoding="utf-8")


def read_job_context(job_dir: Path) -> PaperSnapshot | None:
    return read_context(job_dir / "job-context.json")


def read_context(path: Path) -> PaperSnapshot | None:
    from hutuji_mcp.output_store import _read_regular_file

    try:
        raw, _ = _read_regular_file(path, max_bytes=2048)
    except FileNotFoundError:
        return None
    except OSError:
        raise PaperSettingsError("作品设置不可读，请重新生成") from None
    try:
        value = json.loads(raw)
        if (not isinstance(value, dict) or set(value) != {"version", "device_mac", "machine", "paper"} or
                type(value.get("version")) is not int or value["version"] != 1 or value.get("machine") not in ("paper", "nopaper") or
                not isinstance(value.get("device_mac"), str)):
            raise ValueError("invalid context")
        return PaperSnapshot(PaperConfig.from_dict(value["paper"]), value["device_mac"], value["machine"])
    except (ValueError, TypeError, KeyError, AttributeError):
        raise PaperSettingsError("作品设置不可读，请重新生成") from None


def verify_for_device(gcode_path: Path, snapshot: PaperSnapshot, *, context: PaperSnapshot | None = None) -> None:
    from hutuji_mcp.gcode_gen import GcodeError, validate_gcode
    from hutuji_mcp.output_store import _read_regular_file

    if context and context.device_mac and context.device_mac != snapshot.device_mac:
        raise PaperSettingsError("作品按另一台设备生成，请切回原设备或重新生成")
    if context and context.paper != snapshot.paper:
        raise PaperSettingsError("设备纸张设置已变化，请重新生成作品")
    try:
        data, _ = _read_regular_file(gcode_path)
        paper = read_paper_metadata(gcode_path, data=data)
        if paper is None:
            if snapshot.paper != PaperConfig.default_for(snapshot.machine):
                raise PaperSettingsError("旧作品没有纸张记录，请按当前设置重新生成")
        elif paper != snapshot.paper:
            raise PaperSettingsError("作品纸张与设备设置不一致，请重新生成")
        validate_gcode(data.decode("utf-8"), total_bytes=len(data), profile=snapshot.paper.to_profile())
    except (OSError, UnicodeError, PaperArtifactError, GcodeError):
        raise PaperSettingsError("作品内容或纸张校验失败，请重新生成") from None


def verify_job(job_dir: Path, snapshot: PaperSnapshot) -> None:
    from hutuji_mcp.output_store import _read_regular_file

    context = read_job_context(job_dir)
    verify_for_device(job_dir / "draw.gcode", snapshot, context=context)
    try:
        raw, _ = _read_regular_file(job_dir / "pages.json", max_bytes=8192)
    except FileNotFoundError:
        return
    except OSError:
        raise PaperSettingsError("作品分页记录不可读，请重新生成") from None
    try:
        meta = json.loads(raw)
        lines = meta["page_lines"]
        if (not isinstance(lines, list) or not 1 <= len(lines) <= 20 or
                any(type(count) is not int or count <= 0 for count in lines) or
                meta.get("page_count") != len(lines)):
            raise ValueError("invalid pages")
    except (ValueError, KeyError, TypeError):
        raise PaperSettingsError("作品分页记录不可读，请重新生成") from None
    for i in range(1, len(lines) + 1):
        verify_for_device(job_dir / "pages" / f"page_{i:03d}.gcode", snapshot, context=context)


def copy_saved_context(job_dir: Path, gcode_dest: Path) -> None:
    from hutuji_mcp.paper_artifacts import read_paper_metadata, write_paper_metadata

    raw = gcode_dest.read_bytes()
    paper = read_paper_metadata(job_dir / "draw.gcode", data=raw)
    write_paper_metadata(gcode_dest, paper, data=raw)
    context = read_job_context(job_dir)
    if context is not None:
        write_context(gcode_dest.with_suffix(".context.json"), context)


def remove_saved_file(path: Path) -> None:
    from hutuji_mcp.paper_artifacts import paper_path

    paths = [path]
    if path.suffix == ".gcode":
        paths.extend((paper_path(path), path.with_suffix(".context.json")))
    for item in paths:
        try:
            item.unlink(missing_ok=True)
        except OSError:
            pass


def binding_stamp(config, binding) -> str:
    import hashlib
    import hmac

    raw = json.dumps([binding.user_id, binding.device_mac, binding.bound_at], separators=(",", ":")).encode()
    return hmac.new(config.usage_ingest_secret.encode(), raw, hashlib.sha256).hexdigest()


def verify_capabilities(config, snapshot: PaperSnapshot, pages: list[dict]) -> None:
    import re
    import tempfile

    from hutuji_mcp.gcode_gen import GcodeError, validate_gcode
    from .generate import GenerateError, _download_limited

    if not isinstance(pages, list) or not 1 <= len(pages) <= 20:
        raise PaperSettingsError("作品分页记录不可读")
    prefix = config.public_base_url.rstrip("/") + "/files/"
    try:
        config.data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="paper-check-", dir=config.data_dir) as temp:
            for page in pages:
                if not isinstance(page, dict):
                    raise PaperSettingsError("作品地址不合法")
                url, preview = page.get("url"), page.get("preview_url")
                if (not isinstance(url, str) or not url.startswith(prefix) or
                        not re.fullmatch(r"draw_\d{8}_\d{6}_[A-Za-z0-9_-]{22}\.gcode", url[len(prefix):]) or
                        preview != url[:-6] + ".png"):
                    raise PaperSettingsError("作品地址不合法")
                dest = Path(temp) / "draw.gcode"
                marker = _download_limited(config.mcp_internal_base_url + "/files/" + url[len(prefix):], dest,
                    max_bytes=512 * 1024, timeout_seconds=15)
                if marker != snapshot.paper.marker:
                    raise PaperSettingsError("作品纸张与设备设置不一致，请重新生成")
                raw = dest.read_bytes()
                validate_gcode(raw.decode("utf-8"), total_bytes=len(raw), profile=snapshot.paper.to_profile())
    except (OSError, UnicodeError, GcodeError, GenerateError):
        raise PaperSettingsError("作品校验失败或已过期，请重新生成") from None
