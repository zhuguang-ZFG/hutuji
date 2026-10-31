#!/usr/bin/env python3
"""Telegram 中继接收端（只跑阿里云主节点）：jd 节点直连 api.telegram.org 被
DNS 污染+SNI 封锁、经 lima 的 SSH 中继被 jd HIDS 疑似节流反复挂起
（2026-09-12 实测：3 连成功后连续 3+1 次 KEX 挂死），而 jd→lima :443 TLS
0.13s 即达，故收口为 lima nginx HTTPS 反代：jd 的 tg-notify.sh 把正文 POST
到保密路径（nginx 只放行 jd 出口 IP），本进程在本机直连 TG 发出
（tg.env 配 TG_PROXY=http://127.0.0.1:7890 走 sing-box 出网）。

只 bind 127.0.0.1（TG_RELAY_BIND）：公网/内网直达 8092 均不可见，
唯一门是 nginx 保密路径；正文 ≤4KB，POST body 原样喂 tg-notify.sh --stdin。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NOTIFY_SH = "/opt/hutuji/deploy/tg-notify.sh"
MAX_BODY = 4096
REQUEST_TTL_SECONDS = 300.0
# 重复请求等首个请求出结果的上限：须小于发送端 curl --max-time 20，
# 且覆盖本端 tg-notify.sh --stdin 的 15s 子进程超时。
PENDING_WAIT_SECONDS = 17.0
_request_lock = threading.Lock()
_requests: dict[str, "_Claim"] = {}


class _Claim:
    """一个 request id 的认领：pending → 送达（保留到 TTL）或失败（立即释放）。"""

    __slots__ = ("deadline", "delivered", "done")

    def __init__(self, deadline: float) -> None:
        self.deadline = deadline
        self.delivered = False
        self.done = threading.Event()


def _claim_request(request_id: str | None, *, now: float | None = None) -> tuple[bool, _Claim | None]:
    """返回 (是否由本请求发送, 认领)。无/旧版 request id 保持兼容：直接发送、无认领。"""
    if not request_id or re.fullmatch(r"[A-Za-z0-9._:-]{1,160}", request_id) is None:
        return True, None
    current = time.monotonic() if now is None else now
    with _request_lock:
        expired = [key for key, claim in _requests.items() if claim.deadline <= current]
        for key in expired:
            _requests.pop(key, None)
        existing = _requests.get(request_id)
        if existing is not None:
            return False, existing
        claim = _Claim(current + REQUEST_TTL_SECONDS)
        _requests[request_id] = claim
        return True, claim


def _finish_request(request_id: str | None, claim: _Claim | None, delivered: bool) -> None:
    """送达则保留认领挡住后续重试；失败则释放 id，让等待者或发送端重试接手。"""
    if claim is None:
        return
    with _request_lock:
        claim.delivered = delivered
        if not delivered and _requests.get(request_id or "") is claim:
            _requests.pop(request_id or "", None)
    claim.done.set()


def _acquire(request_id: str | None) -> tuple[_Claim | None, int | None]:
    """返回 (本请求持有的认领, None) 或 (None, 直接应答码)。

    「处理中」不等于「已送达」：撞上 pending 的重复请求必须等首个请求出结果，
    送达才回 202；首个失败释放后由本请求接手发送；等不到结果回 503 交还发送端重试。
    """
    for _ in range(2):
        owner, claim = _claim_request(request_id)
        if owner:
            return claim, None
        assert claim is not None
        if not claim.done.wait(PENDING_WAIT_SECONDS):
            return None, 503
        if claim.delivered:
            return None, 202
    return None, 503


class Relay(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 标准库命名
        length = int(self.headers.get("content-length") or 0)
        if length <= 0 or length > 4096:
            self.send_response(413)
            self.end_headers()
            return
        body = self.rfile.read(length)
        request_id = self.headers.get("X-Hutuji-Notify-Id")
        claim, status = _acquire(request_id)
        if status is not None:
            self.send_response(status)
            self.end_headers()
            return
        delivered = False
        try:
            result = subprocess.run(
                [NOTIFY_SH, "--stdin"],
                input=body,
                timeout=15,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            delivered = result.returncode == 0
        except Exception:
            delivered = False
        finally:
            _finish_request(request_id, claim, delivered)
        if not delivered:
            # 释放幂等键并让发送端重试；响应仍不携带内部错误细节。
            self.send_response(503)
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()

    def log_message(self, fmt: str, *args: object) -> None:  # 不落 token/正文
        sys.stderr.write("tg-relay: req\n")


def main() -> int:
    bind = os.environ.get("TG_RELAY_BIND", "127.0.0.1")
    port = int(os.environ.get("TG_RELAY_PORT", "8092"))
    ThreadingHTTPServer((bind, port), Relay).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
