from __future__ import annotations

import http.client
import importlib.util
import re
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest


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

    owner, claim = relay._claim_request("jd-123", now=100.0)
    assert owner
    relay._finish_request("jd-123", claim, True)
    owner, existing = relay._claim_request("jd-123", now=101.0)
    assert not owner and existing is claim and existing.delivered
    assert relay._claim_request("jd-123", now=401.0)[0]


def test_relay_failure_releases_request_id_for_retry() -> None:
    relay = _load_relay()

    owner, claim = relay._claim_request("jd-retry", now=100.0)
    assert owner
    relay._finish_request("jd-retry", claim, False)
    assert claim.done.is_set() and not claim.delivered
    assert relay._claim_request("jd-retry", now=101.0)[0]


class _RelayHarness:
    """真起 ThreadingHTTPServer；tg-notify.sh 子进程替换为可控桩，按调用顺序取结果。"""

    def __init__(self, relay, outcomes: list[tuple[threading.Event | None, int]]) -> None:
        self.relay = relay
        self.outcomes = outcomes
        self.calls = 0
        self.started = threading.Event()
        relay.subprocess = SimpleNamespace(run=self._run, DEVNULL=None)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), relay.Relay)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def _run(self, *_args, **_kwargs):
        gate, code = self.outcomes[self.calls]
        self.calls += 1
        self.started.set()
        if gate is not None:
            assert gate.wait(5)
        return SimpleNamespace(returncode=code)

    def post(self, request_id: str) -> int:
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        try:
            conn.request("POST", "/", body=b"hello", headers={"X-Hutuji-Notify-Id": request_id})
            return conn.getresponse().status
        finally:
            conn.close()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _post_async(harness: _RelayHarness, request_id: str) -> tuple[threading.Thread, list[int]]:
    status: list[int] = []
    worker = threading.Thread(target=lambda: status.append(harness.post(request_id)))
    worker.start()
    return worker, status


def test_relay_duplicate_waits_for_pending_delivery_before_202() -> None:
    relay = _load_relay()
    release = threading.Event()
    harness = _RelayHarness(relay, [(release, 0)])
    try:
        first, first_status = _post_async(harness, "jd-pending-ok")
        assert harness.started.wait(5)
        retry, retry_status = _post_async(harness, "jd-pending-ok")
        retry.join(0.3)
        assert retry.is_alive(), "处理中不得立刻按已送达回 202"
        release.set()
        first.join(5)
        retry.join(5)
        assert first_status == [200] and retry_status == [202]
        assert harness.calls == 1
    finally:
        harness.close()


def test_relay_duplicate_takes_over_when_pending_send_fails() -> None:
    relay = _load_relay()
    release = threading.Event()
    harness = _RelayHarness(relay, [(release, 1), (None, 0)])
    try:
        first, first_status = _post_async(harness, "jd-pending-fail")
        assert harness.started.wait(5)
        retry, retry_status = _post_async(harness, "jd-pending-fail")
        retry.join(0.3)
        release.set()
        first.join(5)
        retry.join(5)
        # 首个请求的连接可能早已断开；告警必须由重试接手送出，不能被 202 静默吞掉。
        assert first_status == [503] and retry_status == [200]
        assert harness.calls == 2
    finally:
        harness.close()


def test_relay_duplicate_returns_retryable_status_when_pending_outlives_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relay = _load_relay()
    monkeypatch.setattr(relay, "PENDING_WAIT_SECONDS", 0.2)
    release = threading.Event()
    harness = _RelayHarness(relay, [(release, 0)])
    try:
        first, _ = _post_async(harness, "jd-pending-slow")
        assert harness.started.wait(5)
        assert harness.post("jd-pending-slow") == 503
        release.set()
        first.join(5)
    finally:
        harness.close()


def test_relay_pending_wait_fits_inside_sender_attempt_timeout() -> None:
    relay = _load_relay()
    notify = (ROOT / "deploy" / "tg-notify.sh").read_text(encoding="utf-8")
    attempt = int(re.search(r"--max-time (\d+) -s -o /dev/null \\\n\s+-H \"X-Hutuji-Notify-Id", notify).group(1))
    assert 15 < relay.PENDING_WAIT_SECONDS < attempt


def test_ops_check_is_role_aware_and_does_not_parse_systemd_bullets() -> None:
    script = (ROOT / "deploy" / "tg-ops-check.sh").read_text(encoding="utf-8")

    assert "list-units 'hutuji-*' --state=failed --plain" in script
    assert '"hutuji-cloud-bridge" || "$unit" == "hutuji-draw-admin"' in script
    assert "systemctl is-active --quiet nginx" in script
    assert "systemctl is-active --quiet sshd" in script
    assert "flock -n 9 || exit 0" in script


def test_ops_check_covers_enabled_non_hutuji_business_dependencies() -> None:
    script = (ROOT / "deploy" / "tg-ops-check.sh").read_text(encoding="utf-8")

    # 主节点 Redis（队列/breaker）、PG shadow、sing-box（TG 出网）均不带 hutuji- 前缀；
    # 收窄 failed 扫描后必须另行点名，且只在本机启用时要求 active（京东热备未安装）。
    assert "for dep in redis postgresql sing-box; do" in script
    assert 'systemctl is-enabled --quiet "$dep" 2>/dev/null || continue' in script
    assert 'systemctl is-active --quiet "$dep" 2>/dev/null || msg+="not active dependency: $dep"' in script


def test_ops_check_reports_running_units_stuck_in_restart_loop() -> None:
    script = (ROOT / "deploy" / "tg-ops-check.sh").read_text(encoding="utf-8")
    loop = script.split("looping=\"\"", 1)[1].split('msg+="restart loop:', 1)[0]

    # 10-31 curated-prep 在 Restart=on-failure 下重启 343 次，从未进 failed，巡检全程静默。
    assert "list-units 'hutuji-*' --all --type=service" in loop
    assert 'is_expected_inactive "$unit" && continue' in loop
    assert '[[ "$sub" == "auto-restart" ]]' in loop
    assert '"${restarts:-0}" -ge 3' in loop and "3600000000" in loop
    # 手动 stop 后 NRestarts 不清零；跳过已停服务，否则停机处置后会每小时误报。
    assert '[[ "$state" == "inactive" || "$state" == "failed" ]] && continue' in loop


def test_curated_prep_unit_gives_up_into_failed_instead_of_looping() -> None:
    unit = (ROOT / "deploy" / "hutuji-curated-prep.service").read_text(encoding="utf-8")
    interval = int(re.search(r"^StartLimitIntervalSec=(\d+)$", unit, re.M).group(1))
    burst = int(re.search(r"^StartLimitBurst=(\d+)$", unit, re.M).group(1))
    restart_sec = int(re.search(r"^RestartSec=(\d+)$", unit, re.M).group(1))

    # 间隔内能自动拉起的次数必须超过上限，否则永远进不了 failed（旧值 600s 内最多 7 次 < 20）。
    assert interval // restart_sec > burst
    assert burst <= 3


def test_python_callers_outlive_relay_retry_budget() -> None:
    notify = (ROOT / "deploy" / "tg-notify.sh").read_text(encoding="utf-8")
    relay_block = notify.split('if [[ -n "${TG_RELAY_URL:-}" ]]; then', 1)[1].split("\nfi\n", 1)[0]
    attempt = int(re.search(r"--max-time (\d+)", relay_block).group(1))
    tries = int(re.search(r"\[\[ \$tries -ge (\d+) \]\] && break", relay_block).group(1))
    pause = int(re.search(r"sleep (\d+)", relay_block).group(1))
    worst = tries * attempt + (tries - 1) * pause

    for rel in ("draw-portal/draw_portal/tg_notify.py", "mcp-server/hutuji_mcp/activation_relay.py"):
        source = (ROOT / rel).read_text(encoding="utf-8")
        timeout = float(re.search(r"^_(?:TG_)?TIMEOUT_S = ([\d.]+)$", source, re.M).group(1))
        assert timeout > worst, f"{rel} 子进程超时 {timeout}s 不覆盖中继最坏 {worst}s"


def test_relay_client_reuses_one_id_and_waits_past_receiver_timeout() -> None:
    notify = (ROOT / "deploy" / "tg-notify.sh").read_text(encoding="utf-8")
    relay = (ROOT / "deploy" / "tg-relay-server.py").read_text(encoding="utf-8")

    assert 'notify_id="$(hostname -s)-$$-$(date +%s%N)"' in notify
    assert '-H "X-Hutuji-Notify-Id: ${notify_id}"' in notify
    assert "--max-time 20" in notify
    assert 'self.headers.get("X-Hutuji-Notify-Id")' in relay
    assert "self.send_response(503)" in relay
