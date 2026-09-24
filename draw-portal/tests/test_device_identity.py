"""身份扩展的真实密码学与 HTTP 事务回归；不调用真实小智服务。"""
from __future__ import annotations

import base64
import hashlib
import time
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

from draw_portal.device_identity import (
    DeviceIdentityError, decode_field, parse_identity, signed_message, verify_announce,
)
from draw_portal.store import BindStateError, Store
from test_app import _client, _register_and_login


MAC = "aa:bb:cc:dd:ee:01"
CODE = "ABC123"
TOKEN = "identity-test-token"


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _identity(number=1, nonce=bytes(range(16))):
    # 固定测试私钥便于和固件 host 的公开 P-256 点交叉核对，绝不用于设备。
    key = ec.derive_private_key(number, ec.SECP256R1())
    public = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    identity = parse_identity(
        device_id=hashlib.sha256(public).hexdigest()[:32],
        public_key=_b64(public), nonce=_b64(nonce), identity_version=2,
    )
    return key, identity


def _announce_payload(key, identity, challenge, *, mac=MAC, code=CODE,
                      token=TOKEN, sku="nopaper"):
    # 从规格独立拼签名文本，避免用被测 signed_message 自签自验掩盖字段遗漏。
    message = "|".join((
        "hutuji-bind-v2", identity.device_id, identity.nonce, challenge, code.upper(),
        mac.lower(), sku, hashlib.sha256(token.strip().encode("utf-8")).hexdigest(),
    )).encode("utf-8")
    signature = key.sign(message, ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(signature)
    return dict(asdict(identity), mac=mac, bind_code=code, device_token=token,
                plotter_sku=sku, challenge=challenge,
                signature=_b64(r.to_bytes(32, "big") + s.to_bytes(32, "big")))


@pytest.fixture
def portal(monkeypatch, tmp_path):
    monkeypatch.setenv("DRAW_PORTAL_DEVICE_IDENTITY_REQUIRED", "0")
    monkeypatch.setenv("TG_BOT_TOKEN", "")
    monkeypatch.setenv("TG_CHAT_ID", "")
    monkeypatch.setattr("draw_portal.app.probe_device_token", lambda **kw: (True, "test"))
    monkeypatch.setattr("draw_portal.push.mint_messaging_token", lambda **kw: "")
    client = _client(monkeypatch, tmp_path)
    _register_and_login(client)
    return SimpleNamespace(client=client, store=Store(tmp_path / "t.db"))


def _challenge(client, identity, *, mac=MAC, code=CODE):
    response = client.post("/api/device/bind/challenge", json=dict(
        asdict(identity), mac=mac, bind_code=code,
    ))
    assert response.status_code == 200, response.text
    return response.json()


def _claim(client, identity, *, mac=MAC, force=False):
    return client.post("/api/user/bind/claim", json=dict(
        asdict(identity), mac=mac, force=force,
    ))


def _start(client, key, identity, *, code=CODE, token=TOKEN):
    challenge = _challenge(client, identity, code=code)
    payload = _announce_payload(key, identity, challenge["challenge"], code=code, token=token)
    response = client.post("/api/device/bind/announce", json=payload)
    assert response.status_code == 200, response.text
    return payload, response


def _bind(portal, key, identity, *, code=CODE):
    claim = _claim(portal.client, identity)
    assert claim.status_code == 200, claim.text
    payload, response = _start(portal.client, key, identity, code=code)
    assert response.json()["bound"] is True
    return payload


def _second_user(client):
    other = type(client)(client.app)
    _register_and_login(other, "other@qq.com")
    return other


def test_raw_p256_signature_matches_firmware_message():
    key, identity = _identity()
    challenge = _b64(bytes(range(32)))
    payload = _announce_payload(key, identity, challenge, token=" 中文 token ")
    fields = dict(challenge=challenge, bind_code=CODE, mac=MAC,
                  plotter_sku="nopaper", device_token=" 中文 token ")
    verify_announce(identity, signature=payload["signature"], **fields)
    expected = (
        f"hutuji-bind-v2|{identity.device_id}|{identity.nonce}|{challenge}|{CODE}|"
        f"{MAC}|nopaper|{hashlib.sha256('中文 token'.encode()).hexdigest()}"
    )
    assert signed_message(identity, **fields) == expected.encode()
    assert len(decode_field(payload["signature"], 64)) == 64


@pytest.mark.parametrize("field,value", [
    ("device_token", "changed"), ("plotter_sku", "paper"),
    ("mac", "aa:bb:cc:dd:ee:02"), ("bind_code", "DEF456"),
    ("challenge", _b64(b"x" * 32)),
])
def test_signature_covers_every_authorized_field(field, value):
    key, identity = _identity()
    payload = _announce_payload(key, identity, _b64(bytes(32)))
    fields = {name: payload[name] for name in
              ("challenge", "signature", "bind_code", "mac", "plotter_sku", "device_token")}
    fields[field] = value
    with pytest.raises(DeviceIdentityError, match="校验失败"):
        verify_announce(identity, **fields)


@pytest.mark.parametrize("field,value", [
    ("identity_version", None), ("identity_version", 1), ("identity_version", 3),
    ("device_id", "0" * 32), ("nonce", "A" * 21 + "B"),
    ("nonce", "A" * 22 + "=="), ("public_key", "A" * 87),
])
def test_identity_rejects_version_encoding_and_key_mismatch(field, value):
    _, identity = _identity()
    fields = dict(asdict(identity), **{field: value})
    with pytest.raises(DeviceIdentityError):
        parse_identity(**fields)


def test_identity_rejects_non_curve_point_with_matching_digest():
    raw = b"\x04" + bytes(64)
    with pytest.raises(DeviceIdentityError, match="公钥无效"):
        parse_identity(device_id=hashlib.sha256(raw).hexdigest()[:32],
                       public_key=_b64(raw), nonce=_b64(bytes(16)), identity_version=2)


@pytest.mark.parametrize("claim_first", [True, False])
def test_signed_binding_in_either_arrival_order(portal, claim_first):
    key, identity = _identity()
    if claim_first:
        assert _claim(portal.client, identity).json()["status"] == "claimed"
    payload, announced = _start(portal.client, key, identity)
    if not claim_first:
        assert announced.json()["bound"] is False
        assert portal.store.get_device_identity(MAC) is None
        assert _claim(portal.client, identity).json()["status"] == "bound"
    else:
        assert announced.json()["bound"] is True
    binding = portal.store.get_binding_for_mac(MAC)
    assert binding.machine_type == "nopaper"
    assert binding.token_probe_ok is True
    assert portal.store.get_device_identity(MAC)["device_id"] == identity.device_id
    status = portal.client.get("/api/user/bind/claim/status",
                               params=dict(asdict(identity), mac=MAC))
    assert status.json()["status"] == "bound"
    assert TOKEN not in status.text
    assert "device_token_enc" not in status.text
    assert portal.client.post("/api/device/bind/announce", json=payload).json()["bound"] is True


def test_challenge_retry_is_fixed_and_cannot_reassign_mac(portal):
    _, identity = _identity()
    first = _challenge(portal.client, identity)
    assert _challenge(portal.client, identity) == first
    assert portal.store.get_device_identity(MAC) is None
    response = portal.client.post("/api/device/bind/challenge", json=dict(
        asdict(identity), mac="aa:bb:cc:dd:ee:02", bind_code=CODE,
    ))
    assert response.status_code == 409
    assert portal.store.get_identity_challenge(identity)["device_mac"] == MAC


@pytest.mark.parametrize("tamper", ["token", "signature", "challenge", "sku", "key"])
def test_bad_announce_does_not_pin_or_consume(portal, tamper):
    key, identity = _identity()
    challenge = _challenge(portal.client, identity)["challenge"]
    payload = _announce_payload(key, identity, challenge)
    if tamper == "token":
        payload["device_token"] = "replacement-token"
    elif tamper == "signature":
        payload["signature"] = _b64(bytes(64))
    elif tamper == "challenge":
        payload = _announce_payload(key, identity, _b64(b"x" * 32))
    elif tamper == "sku":
        payload["plotter_sku"] = "paper"
    else:
        payload["public_key"] = _identity(2)[1].public_key
    response = portal.client.post("/api/device/bind/announce", json=payload)
    assert response.status_code == 401
    assert portal.store.get_bind_session(CODE) is None
    assert portal.store.get_device_identity(MAC) is None
    assert portal.store.get_identity_challenge(identity)["consumed_at"] is None
    assert payload["device_token"] not in response.text


def test_qr_nonce_mismatch_cannot_auto_complete_or_fake_success(portal):
    key, identity = _identity()
    wrong = replace(identity, nonce=_b64(b"w" * 16))
    assert _claim(portal.client, wrong).status_code == 200
    _, announced = _start(portal.client, key, identity)
    assert announced.json()["bound"] is False
    assert portal.store.get_binding_for_mac(MAC) is None
    status = portal.client.get("/api/user/bind/claim/status",
                               params=dict(asdict(identity), mac=MAC))
    assert status.json()["status"] == "expired"
    complete = portal.client.post("/api/user/bind/complete", json=dict(
        asdict(wrong), bind_code=CODE,
    ))
    assert complete.status_code == 409
    assert portal.store.get_bind_session(CODE).consumed_at is None


def test_expired_challenge_never_gets_new_window(portal):
    key, identity = _identity()
    challenge = _challenge(portal.client, identity)
    with portal.store._connect() as conn:
        conn.execute("UPDATE bind_identity_challenges SET expires_at=?", (time.time() - 1,))
    again = portal.client.post("/api/device/bind/challenge", json=dict(
        asdict(identity), mac=MAC, bind_code=CODE,
    ))
    assert again.status_code == 410
    payload = _announce_payload(key, identity, challenge["challenge"])
    assert portal.client.post("/api/device/bind/announce", json=payload).status_code == 410
    assert _claim(portal.client, identity).status_code == 410
    assert portal.store.get_binding_for_mac(MAC) is None


def test_consumed_proof_after_unbind_cannot_recreate_binding(portal):
    key, identity = _identity()
    payload = _bind(portal, key, identity)
    assert _claim(portal.client, identity).json()["status"] == "already_bound"
    assert portal.client.post("/api/user/bind/unbind", json={"device_mac": MAC}).status_code == 200
    assert portal.client.post("/api/device/bind/announce", json=payload).status_code == 410
    assert _claim(portal.client, identity).status_code == 410
    assert portal.client.post("/api/user/bind/complete",
                              json={"bind_code": CODE, "force": True}).status_code == 410
    assert portal.store.get_binding_for_mac(MAC) is None
    assert portal.store.get_device_identity(MAC) is not None


def test_known_device_cannot_downgrade_in_compatibility_mode(portal):
    key, identity = _identity()
    _bind(portal, key, identity)
    unsigned = portal.client.post("/api/device/bind/announce", json={
        "mac": MAC, "bind_code": "DEF456", "device_token": TOKEN,
    })
    assert unsigned.status_code == 401
    assert portal.client.post("/api/user/bind/claim", json={"mac": MAC}).status_code == 409
    assert portal.store.get_bind_session("DEF456") is None


def test_transfer_requires_fresh_proof_and_explicit_force(portal):
    key, identity = _identity()
    old_payload = _bind(portal, key, identity)
    old_owner = portal.store.get_binding_for_mac(MAC).user_id
    other = _second_user(portal.client)
    assert _claim(other, identity, force=True).status_code == 410
    fresh = replace(identity, nonce=_b64(b"n" * 16))
    payload, _ = _start(portal.client, key, fresh, code="DEF456")
    denied = _claim(other, fresh)
    assert denied.status_code == 409
    assert denied.json()["detail"]["code"] == "binding_conflict"
    assert portal.store.get_binding_for_mac(MAC).user_id == old_owner
    assert _claim(other, fresh, force=True).json()["status"] == "bound"
    assert portal.store.get_binding_for_mac(MAC).user_id != old_owner
    assert portal.client.post("/api/device/bind/announce", json=old_payload).status_code == 410
    assert other.post("/api/device/bind/announce", json=payload).json()["bound"] is True
    assert portal.client.post("/api/user/bind/unbind", json={"device_mac": MAC}).status_code == 404


def test_reset_key_requires_original_owner_and_retires_old_key(portal, monkeypatch):
    key, identity = _identity()
    _bind(portal, key, identity)
    owner = portal.store.get_binding_for_mac(MAC).user_id
    other = _second_user(portal.client)
    replacement_key, replacement = _identity(2)
    _start(portal.client, replacement_key, replacement, code="DEF456")
    denied = _claim(other, replacement, force=True)
    assert denied.status_code == 409
    assert denied.json()["detail"]["code"] == "identity_mismatch"
    assert portal.store.get_device_identity(MAC)["device_id"] == identity.device_id
    assert _claim(portal.client, replacement).json()["status"] == "bound"
    assert portal.store.get_binding_for_mac(MAC).user_id == owner
    assert portal.store.get_device_identity(MAC)["device_id"] == replacement.device_id
    retired = replace(identity, nonce=_b64(b"r" * 16))
    _start(portal.client, key, retired, code="GHI789")
    minted = []
    monkeypatch.setattr("draw_portal.app.probe_device_token", lambda **kw: (False, "bad"))
    monkeypatch.setattr("draw_portal.push.mint_messaging_token",
                        lambda **kw: minted.append(kw) or "new-token")
    denied = _claim(portal.client, retired)
    assert denied.status_code == 409
    assert minted == []
    assert portal.store.get_device_identity(MAC)["device_id"] == replacement.device_id


def test_empty_device_token_is_minted_server_side(portal, monkeypatch):
    key, identity = _identity()
    calls = []
    monkeypatch.setattr("draw_portal.push.mint_messaging_token",
                        lambda **kw: calls.append(kw["mac"]) or "minted-test-token")
    assert _claim(portal.client, identity).status_code == 200
    _, announced = _start(portal.client, key, identity, token="")
    assert announced.json()["bound"] is True
    assert calls == [MAC]
    assert portal.store.get_binding_for_mac(MAC).token_probe_ok is True


def test_token_mint_failure_keeps_proof_retryable(portal, monkeypatch):
    key, identity = _identity()
    challenge = _challenge(portal.client, identity)
    assert _claim(portal.client, identity).status_code == 200
    payload = _announce_payload(key, identity, challenge["challenge"], token="")
    pending = portal.client.post("/api/device/bind/announce", json=payload)
    assert pending.status_code == 503
    assert pending.json()["detail"]["code"] == "binding_pending"
    assert portal.store.get_bind_session(CODE).consumed_at is None
    assert portal.store.get_identity_challenge(identity)["consumed_at"] is None
    assert portal.store.get_binding_for_mac(MAC) is None
    monkeypatch.setattr("draw_portal.push.mint_messaging_token",
                        lambda **kw: "minted-on-retry")
    assert portal.client.post("/api/device/bind/announce", json=payload).json()["bound"] is True


def test_minted_token_must_be_callable_before_consuming_proof(portal, monkeypatch):
    key, identity = _identity()
    challenge = _challenge(portal.client, identity)
    assert _claim(portal.client, identity).status_code == 200
    monkeypatch.setattr("draw_portal.push.mint_messaging_token", lambda **kw: "not-online-yet")
    monkeypatch.setattr("draw_portal.app.probe_device_token", lambda **kw: (False, "offline"))
    payload = _announce_payload(key, identity, challenge["challenge"], token="")
    pending = portal.client.post("/api/device/bind/announce", json=payload)
    assert pending.status_code == 503
    assert portal.store.get_identity_challenge(identity)["consumed_at"] is None
    assert portal.store.get_binding_for_mac(MAC) is None
    monkeypatch.setattr("draw_portal.app.probe_device_token", lambda **kw: (True, "ready"))
    assert portal.client.post("/api/device/bind/announce", json=payload).json()["bound"] is True


def test_rebind_rechecks_old_token_instead_of_trusting_cached_probe_flag(portal, monkeypatch):
    key, identity = _identity()
    _bind(portal, key, identity)
    previous = portal.store.get_binding_for_mac(MAC)
    _, fresh_identity = _identity(nonce=b"c" * 16)
    challenge = _challenge(portal.client, fresh_identity, code="DEF456")
    assert _claim(portal.client, fresh_identity).status_code == 200
    monkeypatch.setattr("draw_portal.app.probe_device_token", lambda **kw: (False, "expired"))
    payload = _announce_payload(key, fresh_identity, challenge["challenge"], code="DEF456", token="")
    pending = portal.client.post("/api/device/bind/announce", json=payload)
    assert pending.status_code == 503
    assert portal.store.get_binding_for_mac(MAC) == previous
    assert portal.store.get_identity_challenge(fresh_identity)["consumed_at"] is None
    probes = []
    monkeypatch.setattr("draw_portal.app.probe_device_token",
                        lambda **kw: (probes.append(kw["device_token"]) is None, "ready"))
    assert portal.client.post("/api/device/bind/announce", json=payload).json()["bound"] is True
    assert probes == [TOKEN]
    assert portal.store.get_binding_for_mac(MAC).device_token_enc == previous.device_token_enc


def test_bad_old_ciphertext_can_recover_with_new_signed_session(portal, monkeypatch):
    key, identity = _identity()
    _bind(portal, key, identity)
    owner = portal.store.get_binding_for_mac(MAC).user_id
    portal.store.update_binding_token(owner, device_mac=MAC, device_token_enc="broken-ciphertext")
    _, fresh_identity = _identity(nonce=b"b" * 16)
    assert _claim(portal.client, fresh_identity).status_code == 200
    monkeypatch.setattr("draw_portal.push.mint_messaging_token", lambda **kw: "recovered-token")
    _, response = _start(portal.client, key, fresh_identity, code="DEF456", token="")
    assert response.json()["bound"] is True
    binding = portal.store.get_binding_for_mac(MAC)
    assert binding.device_token_enc != "broken-ciphertext"
    assert binding.user_id == owner


def test_challenge_rate_limit_counts_new_sessions_only(tmp_path):
    store = Store(tmp_path / "identity.db")
    _, identity = _identity()
    first = store.create_identity_challenge(identity, mac=MAC, bind_code=CODE,
                                            request_ip="test-client", now=1000, ttl=600)
    for index in range(1, 20):
        item = replace(identity, nonce=_b64(index.to_bytes(16, "big")))
        store.create_identity_challenge(item, mac=MAC, bind_code=CODE,
                                        request_ip="test-client", now=1000, ttl=600)
    assert store.create_identity_challenge(identity, mac=MAC, bind_code=CODE,
                                           request_ip="test-client", now=1001, ttl=600) == first
    with pytest.raises(BindStateError) as error:
        store.create_identity_challenge(replace(identity, nonce=_b64(b"x" * 16)), mac=MAC,
                                        bind_code=CODE, request_ip="other", now=1001, ttl=600)
    assert error.value.status == 429


def test_claim_retry_keeps_original_expiration(tmp_path):
    store = Store(tmp_path / "identity.db")
    _, identity = _identity()
    kwargs = dict(asdict(identity), device_mac=MAC, user_id="test-user")
    first = store.upsert_bind_claim(**kwargs, force=False, created_at=1000, expires_at=1100)
    retried = store.upsert_bind_claim(**kwargs, force=True, created_at=1050, expires_at=1650)
    assert retried.expires_at == first.expires_at
    assert retried.created_at == first.created_at
    assert retried.force is True
    assert store.get_bind_claim(MAC, now=1100) is None


@pytest.mark.parametrize("missing", ["signature", "challenge", "nonce", "identity_version"])
def test_partial_signed_announce_never_falls_back_to_legacy(portal, missing):
    key, identity = _identity()
    challenge = _challenge(portal.client, identity)
    payload = _announce_payload(key, identity, challenge["challenge"])
    del payload[missing]
    response = portal.client.post("/api/device/bind/announce", json=payload)
    assert response.status_code == 401
    assert portal.store.get_bind_session(CODE) is None
    assert portal.store.get_device_identity(MAC) is None
    assert TOKEN not in response.text


def test_strict_mode_blocks_legacy_and_accepts_signed_binding(monkeypatch, tmp_path):
    monkeypatch.setenv("DRAW_PORTAL_DEVICE_IDENTITY_REQUIRED", "1")
    monkeypatch.setattr("draw_portal.app.probe_device_token", lambda **kw: (True, "test"))
    client = _client(monkeypatch, tmp_path)
    _register_and_login(client)
    store = Store(tmp_path / "t.db")
    legacy = client.post("/api/device/bind/announce", json={
        "mac": MAC, "bind_code": CODE, "device_token": TOKEN,
    })
    assert legacy.status_code == 401
    assert client.post("/api/user/bind/claim", json={"mac": MAC}).status_code == 409
    store.upsert_bind_session(bind_code="DEF456", device_mac=MAC,
                              device_token_enc="unused", expires_at=time.time() + 600)
    assert client.post("/api/user/bind/complete", json={"bind_code": "DEF456"}).status_code == 409
    key, identity = _identity()
    _bind(SimpleNamespace(client=client, store=store), key, identity)
    assert store.get_device_identity(MAC)["device_id"] == identity.device_id


@pytest.mark.parametrize('bad_body', [
    {'success': False, 'message': 'offline'},
    {'success': True, 'data': {'device_id': 'aa:bb:cc:dd:ee:02',
                             'state': 'idle', 'connected': True, 'ready': True}},
])
def test_actual_probe_failure_preserves_signed_proof_until_correct_device_answers(portal, monkeypatch, bad_body):
    from draw_portal import probe
    from test_probe import response
    monkeypatch.setattr('draw_portal.app.probe_device_token', probe.probe_device_token)
    response(monkeypatch, bad_body)
    key, identity = _identity()
    payload, announced = _start(portal.client, key, identity)
    assert not announced.json()['bound']
    pending = _claim(portal.client, identity)
    assert pending.status_code == 503
    assert pending.json()['detail']['code'] == 'binding_pending'
    assert portal.store.get_bind_session(CODE).consumed_at is None
    assert portal.store.get_binding_for_mac(MAC) is None
    response(monkeypatch, {'success': True, 'data': {
        'device_id': MAC, 'state': 'idle', 'connected': False, 'ready': False}})
    completed = _claim(portal.client, identity)
    assert completed.status_code == 200 and completed.json()['status'] == 'bound'
    assert portal.store.get_bind_session(CODE).consumed_at is not None
