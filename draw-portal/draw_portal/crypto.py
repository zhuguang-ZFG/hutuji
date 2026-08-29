from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


def encrypt_token(key: bytes, plaintext: str) -> str:
    return Fernet(key).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_token(key: bytes, ciphertext: str) -> str:
    try:
        return Fernet(key).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("device_token 解密失败") from exc
