"""生图 provider API key 加解密（独立 Fernet 密钥）。"""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


def encrypt_api_key(key: bytes, plaintext: str) -> str:
    return Fernet(key).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_api_key(key: bytes, ciphertext: str) -> str:
    try:
        return Fernet(key).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("api_key 解密失败") from exc
