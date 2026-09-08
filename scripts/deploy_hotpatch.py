#!/usr/bin/env python3
"""双机热修发版助手：从 Git blob 导出 LF 候选 → 打印 scp/restart/preflight 清单。

不代替人工核 SHA、不自动写 mcp.env、不宣称 preflight 全绿。
用法：
  python scripts/deploy_hotpatch.py --paths draw-portal/draw_portal/app.py draw-portal/draw_admin/app.py
  python scripts/deploy_hotpatch.py --paths mcp-server/hutuji_mcp/server.py --restart mcp,bridge

凭据：SSH 走本机已配置的密钥；禁止把密码写进本脚本。
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALIYUN = "47.112.162.80"
JDCLOUD = "117.72.118.95"
REMOTE_ROOT = "/opt/hutuji"


def _blob_sha(rel: str) -> tuple[str, bytes]:
    raw = subprocess.check_output(
        ["git", "cat-file", "blob", f"HEAD:{rel.replace(chr(92), '/')}"],
        cwd=ROOT,
    )
    # 工作区可能 CRLF；候选必须以 blob 为准并确保 LF
    if b"\r\n" in raw:
        raise SystemExit(f"CRLF in git blob for {rel} — unexpected; abort")
    digest = hashlib.sha256(raw).hexdigest()
    return digest, raw


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--paths", nargs="+", required=True, help="相对仓根的路径")
    p.add_argument(
        "--restart",
        default="portal",
        help="逗号分隔：portal,admin,mcp,bridge,worker（仅打印命令）",
    )
    p.add_argument("--export-dir", default="", help="若指定则写出 LF 候选文件")
    p.add_argument(
        "--from-workdir",
        action="store_true",
        help="用工作树文件（未提交热修）；默认 git blob HEAD",
    )
    args = p.parse_args()

    export = Path(args.export_dir) if args.export_dir else None
    if export:
        export.mkdir(parents=True, exist_ok=True)

    print("=== 候选 SHA-256（CR=0）===")
    for rel in args.paths:
        rel_norm = rel.replace("\\", "/")
        if args.from_workdir:
            path = ROOT / rel_norm
            raw = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            digest = hashlib.sha256(raw).hexdigest()
            src = "workdir"
        else:
            digest, raw = _blob_sha(rel_norm)
            src = "HEAD"
        print(f"{digest}  {rel_norm}  bytes={len(raw)}  src={src}")
        if export:
            # 保留相对路径，避免同名 app.py 互相覆盖
            out = export / rel_norm.replace("/", "__")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(raw)
            print(f"  wrote {out}")

    print("\n=== scp 示例（逐文件，属主 hutuji）===")
    for host in (ALIYUN, JDCLOUD):
        for rel in args.paths:
            rel_norm = rel.replace("\\", "/")
            remote = f"{REMOTE_ROOT}/{rel_norm}"
            print(f"scp {rel_norm} root@{host}:{remote}")
            print(f"ssh root@{host} 'chown hutuji:hutuji {remote}'")

    restart_map = {
        "portal": "systemctl restart hutuji-draw-portal",
        "admin": "systemctl restart hutuji-draw-admin",
        "mcp": "systemctl restart hutuji-mcp",
        "bridge": "systemctl restart hutuji-cloud-bridge",
        "worker": "systemctl restart hutuji-draw-worker@{1..8}",
    }
    print("\n=== restart（unit 变更须先 daemon-reload；enable --now 不重启 active）===")
    for host in (ALIYUN, JDCLOUD):
        cmds = []
        for key in args.restart.split(","):
            key = key.strip()
            if key in restart_map:
                cmds.append(restart_map[key])
        if "mcp" in args.restart or "bridge" in args.restart:
            # 京东热备可能不跑 bridge/worker——人工按节点裁剪
            pass
        joined = " && ".join(cmds) if cmds else "echo nothing"
        print(f"ssh root@{host} '{joined}'")

    print("\n=== messaging token 半自动（勿盲写 mcp.env）===")
    print("  1) journal 见 401/403/404 → 看门狗/报警")
    print("  2) 用控制台铸新 messaging JWT，写入 /var/lib/hutuji/device_messaging.token")
    print("  3) 可选同步 portal 绑定表；见 hutuji_mcp/device_messaging.py")
    print("  4) 探活：python scripts/xiaozhi_console.py device-call hutuji.status")

    print("\n=== preflight ===")
    print(f"ssh root@{ALIYUN} 'bash /opt/hutuji/deploy/preflight.sh'   # 按节点口径读 pass")
    print(f"ssh root@{JDCLOUD} 'bash /opt/hutuji/deploy/preflight.sh'")
    print("禁止把单节点历史 30/30 当双机共同结果。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
