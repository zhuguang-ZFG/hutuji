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
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NOTIFY_SH = "/opt/hutuji/deploy/tg-notify.sh"
MAX_BODY = 4096


class Relay(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 标准库命名
        length = int(self.headers.get("content-length") or 0)
        if length <= 0 or length > 4096:
            self.send_response(413)
            self.end_headers()
            return
        body = self.rfile.read(length)
        try:
            subprocess.run(
                [NOTIFY_SH, "--stdin"],
                input=body,
                timeout=15,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            # 中继不回 500 细节：发不出去接收端也无能为力，统一 202 吞掉。
            pass
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
