from __future__ import annotations

import time
from pathlib import Path

import pytest

from draw_portal.crypto import decrypt_token, encrypt_token
from draw_portal.models import normalize_bind_code
from draw_portal.store import Store


def _key() -> bytes:
    from cryptography.fernet import Fernet

    return Fernet.generate_key()


def test_bind_session_consume(tmp_path: Path):
    s = Store(tmp_path / "t.db")
    key = _key()
    enc = encrypt_token(key, "device-jwt-secret")
    now = time.time()
    s.upsert_bind_session(
        bind_code="ABC123",
        device_mac="aa:bb:cc:dd:ee:ff",
        device_token_enc=enc,
        expires_at=now + 600,
    )
    got = s.consume_bind_session("ABC123", now=now + 1)
    assert got is not None
    assert decrypt_token(key, got.device_token_enc) == "device-jwt-secret"
    assert s.consume_bind_session("ABC123", now=now + 2) is None


def test_invalid_bind_code():
    with pytest.raises(ValueError):
        normalize_bind_code("abc")
