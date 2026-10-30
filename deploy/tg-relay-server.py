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
_request_lock = threading.Lock()
_request_expiry: dict[str, float] = {}


def _claim_request(request_id: str | None, *, now: float | None = None) -> bool:
    """同一通知重试只执行一次；无/旧版 request id 保持兼容并直接放行。"""
    if not request_id or re.fullmatch(r"[A-Za-z0-9._:-]{1,160}", request_id) is None:
        return True
    current = time.monotonic() if now is None else now
    with _request_lock:
        expired = [key for key, deadline in _request_expiry.items() if deadline <= current]
        for key in expired:
            _request_expiry.pop(key, None)
        if request_id in _request_expiry:
            return False
        _request_expiry[request_id] = current + REQUEST_TTL_SECONDS
        return True


def _release_request(request_id: str | None) -> None:
    if not request_id:
        return
    with _request_lock:
        _request_expiry.pop(request_id, None)


class Relay(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 标准库命名
        length = int(self.headers.get("content-length") or 0)
        if length <= 0 or length > 4096:
            self.send_response(413)
            self.end_headers()
            return
        body = self.rfile.read(length)
        request_id = self.headers.get("X-Hutuji-Notify-Id")
        if not _claim_request(request_id):
            self.send_response(202)
            self.end_headers()
            return
        try:
            result = subprocess.run(
                [NOTIFY_SH, "--stdin"],
                input=body,
                timeout=15,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            result = None
        if result is None or result.returncode != 0:
            # 释放幂等键并让发送端重试；响应仍不携带内部错误细节。
            _release_request(request_id)
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
