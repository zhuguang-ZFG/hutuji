from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _load_relay():
    path = ROOT / "deploy" / "tg-relay-server.py"
    spec = importlib.util.spec_from_file_location("hutuji_tg_relay", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_relay_request_id_is_idempotent_and_expires() -> None:
    relay = _load_relay()
    relay._request_expiry.clear()

    assert relay._claim_request("jd-123", now=100.0)
    assert not relay._claim_request("jd-123", now=101.0)
    assert relay._claim_request("jd-123", now=401.0)


def test_relay_failure_releases_request_id_for_retry() -> None:
    relay = _load_relay()
    relay._request_expiry.clear()

    assert relay._claim_request("jd-retry", now=100.0)
    relay._release_request("jd-retry")
    assert relay._claim_request("jd-retry", now=101.0)


def test_ops_check_is_role_aware_and_does_not_parse_systemd_bullets() -> None:
    script = (ROOT / "deploy" / "tg-ops-check.sh").read_text(encoding="utf-8")

    assert "list-units 'hutuji-*' --state=failed --plain" in script
    assert '"hutuji-cloud-bridge" || "$unit" == "hutuji-draw-admin"' in script
    assert "systemctl is-active --quiet nginx" in script
    assert "systemctl is-active --quiet sshd" in script
    assert "flock -n 9 || exit 0" in script


def test_relay_client_reuses_one_id_and_waits_past_receiver_timeout() -> None:
    notify = (ROOT / "deploy" / "tg-notify.sh").read_text(encoding="utf-8")
    relay = (ROOT / "deploy" / "tg-relay-server.py").read_text(encoding="utf-8")

    assert 'notify_id="$(hostname -s)-$$-$(date +%s%N)"' in notify
    assert '-H "X-Hutuji-Notify-Id: ${notify_id}"' in notify
    assert "--max-time 20" in notify
    assert 'self.headers.get("X-Hutuji-Notify-Id")' in relay
    assert "self.send_response(503)" in relay
