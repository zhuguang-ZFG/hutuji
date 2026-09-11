#!/usr/bin/env python3
"""Telegram 中继接收端（只跑阿里云主节点）：jd 节点直连 api.telegram.org 被
DNS 污染+SNI 封锁（2026-09-12 实测解析到 Meta IP + timeout），SSH over
tailnet 握手亦挂起（TCP 通、banner 交换卡死），故收口为 tailnet 内 HTTP：
jd 的 tg-notify.sh 把正文 POST 到本服务（WireGuard 加密，出 tailnet 即明文
HTTP 的暴露面只剩 tailnet 成员），本进程在本机直连 TG 发出。

只 bind tailnet IP（TG_RELAY_BIND，默认 100.103.82.78）；正文 ≤4KB；
不解析路径/头，POST body 原样喂 tg-notify.sh --stdin。
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
    bind = os.environ.get("TG_RELAY_BIND", "100.103.82.78")
    port = int(os.environ.get("TG_RELAY_PORT", "8092"))
    ThreadingHTTPServer((bind, port), Relay).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
