"""DRAW-BIND 身份扩展 v2：与 ESP-IDF PSA 的 P-256 字节契约对齐。"""
from __future__ import annotations

import base64
import binascii
import hashlib
import re
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils


IDENTITY_VERSION = 2
_URLSAFE = re.compile(r"[A-Za-z0-9_-]+\Z")


class DeviceIdentityError(ValueError):
    """不回显公钥、签名、挑战或 token 的固定错误。"""


def decode_field(value: str | None, size: int) -> bytes:
    if not isinstance(value, str) or len(value) != (size * 8 + 5) // 6 or not _URLSAFE.fullmatch(value):
        raise DeviceIdentityError("设备身份字段无效，请重新扫描设备二维码")
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise DeviceIdentityError("设备身份字段无效，请重新扫描设备二维码") from exc
    if len(raw) != size or base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != value:
        raise DeviceIdentityError("设备身份编码无效，请重新扫描设备二维码")
    return raw


@dataclass(frozen=True)
class DeviceIdentity:
    device_id: str
    public_key: str
    nonce: str
    identity_version: int = IDENTITY_VERSION


def parse_identity(*, device_id: str | None, public_key: str | None,
                   nonce: str | None, identity_version: int | None) -> DeviceIdentity:
    if identity_version != IDENTITY_VERSION:
        raise DeviceIdentityError("设备身份版本不支持，请升级后重新扫码")
    raw = decode_field(public_key, 65)
    decode_field(nonce, 16)
    if raw[0] != 4 or device_id != hashlib.sha256(raw).hexdigest()[:32]:
        raise DeviceIdentityError("设备公钥与身份不匹配")
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
    except ValueError as exc:
        raise DeviceIdentityError("设备公钥无效") from exc
    return DeviceIdentity(device_id, public_key, nonce)


def signed_message(identity: DeviceIdentity, *, challenge: str, bind_code: str,
                   mac: str, plotter_sku: str, device_token: str) -> bytes:
    digest = hashlib.sha256(device_token.strip().encode("utf-8")).hexdigest()
    return (
        f"hutuji-bind-v2|{identity.device_id}|{identity.nonce}|{challenge}|"
        f"{bind_code}|{mac}|{plotter_sku}|{digest}"
    ).encode("utf-8")


def verify_announce(identity: DeviceIdentity, *, challenge: str | None, signature: str | None,
                    bind_code: str, mac: str, plotter_sku: str, device_token: str) -> None:
    decode_field(challenge, 32)
    raw = decode_field(signature, 64)
    key = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), decode_field(identity.public_key, 65)
    )
    # PSA 的签名是定长 r||s；cryptography 的 verify 接收 ASN.1 DER。
    der = utils.encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    try:
        key.verify(der, signed_message(
            identity, challenge=challenge, bind_code=bind_code, mac=mac,
            plotter_sku=plotter_sku, device_token=device_token,
        ), ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as exc:
        raise DeviceIdentityError("设备身份校验失败，请重新扫描设备二维码") from exc
