"""云端生成按明确设备读取纸张；完成后按同一次绑定推送。"""
from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import re
from urllib.parse import urlsplit

import httpx

from .paper_config import PaperConfig, PaperConfigError


class DevicePaperError(ValueError):
    """目标或纸张不可确认，不能猜测另一台设备。"""


@dataclass(frozen=True)
class DrawTarget:
    device_id: str
    machine: str
    paper: PaperConfig
    owner_stamp: str

    def to_dict(self):
        return {"device_id": self.device_id, "machine": self.machine,
                "paper": self.paper.to_dict(), "owner_stamp": self.owner_stamp}

    @classmethod
    def from_dict(cls, value):
        try:
            if (not isinstance(value, dict) or set(value) != {"device_id", "machine", "paper", "owner_stamp"} or
                    not isinstance(value["device_id"], str) or
                    not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", value["device_id"]) or
                    value["machine"] not in ("paper", "nopaper") or
                    not isinstance(value["owner_stamp"], str) or
                    not re.fullmatch(r"[0-9a-f]{64}", value["owner_stamp"])):
                raise ValueError("invalid target")
            return cls(value["device_id"], value["machine"], PaperConfig.from_dict(value["paper"]), value["owner_stamp"])
        except (ValueError, TypeError):
            raise DevicePaperError("设备纸张记录无效，请重新连接") from None


def _portal_base(settings) -> str:
    # 复用已有同机配置和密钥，禁止把设备信息或共享密钥发到可配置的公网地址。
    try:
        parsed = urlsplit(getattr(settings, "usage_ingest_url", ""))
        parsed.port
    except ValueError:
        raise DevicePaperError("设备纸张服务未配置") from None
    try:
        local = ipaddress.ip_address(parsed.hostname or "").is_loopback
    except ValueError:
        local = parsed.hostname == "localhost"
    if (parsed.scheme not in ("http", "https") or not local or
            parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise DevicePaperError("设备纸张服务未配置")
    return f"{parsed.scheme}://{parsed.netloc}"


async def resolve_target(settings, device_id: str) -> DrawTarget | None:
    secret = getattr(settings, "usage_ingest_secret", "")
    if not secret:
        return None  # 独立生成服务仍可用固定默认档；不具备动态设备配置能力。
    if not isinstance(device_id, str):
        raise DevicePaperError("请传当前对话设备的 MAC 作为 device_id")
    device_id = device_id.strip().lower()
    if not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", device_id):
        raise DevicePaperError("请传当前对话设备的 MAC 作为 device_id")
    try:
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            response = await client.get(_portal_base(settings) + "/api/internal/paper",
                params={"device_id": device_id}, headers={"X-Hutuji-Usage-Secret": secret})
        if response.status_code != 200 or len(response.content) > 4096:
            raise DevicePaperError("无法读取当前设备纸张，请确认设备已绑定并重新连接")
        try:
            target = DrawTarget.from_dict(response.json())
        except ValueError:
            raise DevicePaperError("设备纸张回包无效，请重新连接") from None
        if target.device_id != device_id:
            raise DevicePaperError("设备纸张回包不匹配，请重新连接")
        return target
    except (httpx.HTTPError, PaperConfigError):
        raise DevicePaperError("设备纸张服务暂不可用，请稍后重试") from None


async def push_target(settings, target: DrawTarget, pages: list[dict]) -> bool:
    try:
        async with httpx.AsyncClient(timeout=45, trust_env=False) as client:
            response = await client.post(_portal_base(settings) + "/api/internal/paper/push",
                json={"target": target.to_dict(), "pages": pages},
                headers={"X-Hutuji-Usage-Secret": getattr(settings, "usage_ingest_secret", "")})
        payload = response.json() if response.status_code == 200 and len(response.content) <= 4096 else None
        return isinstance(payload, dict) and payload.get("status") == "sent"
    except (httpx.HTTPError, ValueError):
        return False
