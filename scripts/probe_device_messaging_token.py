#!/usr/bin/env python3
"""HIL：验证设备 messaging token 是否可用（DRAW-BIND 前提）。

用法（PowerShell，token 勿入库）:
    $env:XIAOZHI_DEVICE_TOKEN = "<主题配置 URL 的 token 参数>"
    python scripts/probe_device_messaging_token.py

可选对照（若已知 NVS 内 token 与主题 token 是否一致）:
    python scripts/probe_device_messaging_token.py --reference-token "<另一份 token>"

退出码:
    0 — tools/list 与 hutuji.status 均成功
    1 — 缺环境 / API 失败
    2 — 用法错误
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "draw-portal"))

from draw_portal.probe import probe_device_token  # noqa: E402


def _fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:12]


def _tools_list(api_base: str, device_token: str) -> tuple[bool, str]:
    import urllib.error
    import urllib.request

    url = f"{api_base.rstrip('/')}/messaging/device/tools/list"
    req = urllib.request.Request(
        url,
        data=b"{}",
        headers={
            "Authorization": f"Bearer {device_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
            if isinstance(data, dict) and data.get("error"):
                return False, "tools/list JSON-RPC error"
            return True, "tools/list ok"
    except urllib.error.HTTPError as exc:
        return False, f"tools/list HTTP {exc.code}"
    except Exception as exc:
        return False, f"tools/list {type(exc).__name__}"


def main() -> int:
    parser = argparse.ArgumentParser(description="探活小智设备 messaging token")
    parser.add_argument(
        "--reference-token",
        help="可选：与 XIAOZHI_DEVICE_TOKEN 做 SHA256 前缀对照（不打印明文）",
    )
    parser.add_argument(
        "--api-base",
        default=os.environ.get("XIAOZHI_API", "https://xiaozhi.me/api"),
        help="小智 API 基址",
    )
    args = parser.parse_args()

    token = os.environ.get("XIAOZHI_DEVICE_TOKEN", "").strip()
    if not token:
        print("FAIL: 未设置 XIAOZHI_DEVICE_TOKEN", file=sys.stderr)
        print("从小智控制台 → 设备 → 主题配置 URL 读取 token 参数", file=sys.stderr)
        return 1

    print(f"device_token_fp={_fingerprint(token)}")

    if args.reference_token:
        ref = args.reference_token.strip()
        same = _fingerprint(token) == _fingerprint(ref)
        print(f"reference_fp={_fingerprint(ref)} match={same}")
        if not same:
            print("WARN: 两份 token 指纹不一致 — NVS websocket.token 可能不等于主题 URL token（未验证项）")

    ok_list, msg_list = _tools_list(args.api_base, token)
    print(f"tools_list: {'PASS' if ok_list else 'FAIL'} ({msg_list})")

    ok_status, msg_status = probe_device_token(api_base=args.api_base, device_token=token)
    print(f"hutuji.status: {'PASS' if ok_status else 'FAIL'} ({msg_status})")

    if ok_list and ok_status:
        print("OVERALL=PASS")
        print("NOTE: 本脚本未读板端 NVS；websocket.token 与主题 token 是否同值须固件侧对照或 M2b announce HIL")
        return 0

    print("OVERALL=FAIL")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
