#!/usr/bin/env python3
"""拉取生产（京东云）MCP/bridge 日志并做脱敏与故障统计——「云端故障」取证通道。

用法：
    python scripts/cloud_health_pull.py                      # 最近 24h，落 results/local-only/<日期>/
    python scripts/cloud_health_pull.py --since "2 hours ago" -o results/local-only/2026-08-23/

设计约束（仓规）：
- ssh 认证完全走用户既有配置（~/.ssh/config / agent），脚本不读任何凭据文件；
  主机默认 root@117.72.118.95，可用 --host 覆盖。
- 落盘前对能力文件名 token 段脱敏（draw_YYYYMMDD_HHMMSS_<22 位 token>：
  按固定格式拆——token 是 urlsafe base64 本身可能含下划线，不能按最后一个
  下划线切），与 mcp-server 的 _redact_capability_name 同口径。
- 退出码：0 成功；2 ssh/传输失败；1 本地写盘失败。

产出两个文件：
- cloud-health-<yyyymmdd-HHMMSS>.log         原始 journalctl（脱敏后）
- cloud-health-<yyyymmdd-HHMMSS>.summary.txt 关键事件计数 + 时间窗首尾
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import shlex
import subprocess
import sys
from pathlib import Path

DEFAULT_HOST = "root@117.72.118.95"
UNITS = ("hutuji-mcp", "hutuji-cloud-bridge")

# draw_YYYYMMDD_HHMMSS_<22 位>——前缀定长（5+8+1+6+1=21 字符），token 段整段打码。
# 尾部用负前瞻而非 \b：token 第 22 位是 `-`（非单词字符）时 \b 不存在会整段漏码；
# 超长串保持整条不匹配（宁可异常可见，不做半截打码）。
_CAPABILITY_RE = re.compile(r"\bdraw_\d{8}_\d{6}_[A-Za-z0-9_-]{22}(?![A-Za-z0-9_-])")


def redact(text: str) -> str:
    """能力文件名 token 段整段打码；其余内容原样保留。"""
    return _CAPABILITY_RE.sub(lambda m: m.group(0)[:21] + "***", text)


# 关键事件指纹：(标签, 不区分大小写子串)。只统计，不改写原文。
_PATTERNS: tuple[tuple[str, str], ...] = (
    ("traceback", "traceback (most recent call last)"),
    ("session_terminated", "session terminated"),
    ("endpoint_not_found", "endpoint agent"),
    ("proxy_respawn", "重建 mcp-proxy"),
    ("idle_probe", "主动探活"),
    ("idle_probe_ok", "探活通过"),
    ("wss_disconnect", "云端连接断开"),
    ("wss_connect", "云端连接已建立"),
    ("tools_call_error", "iserror"),
    ("http_5xx", " 500 "),
    ("http_429", " 429 "),
    ("restart", "started hutuji"),
)


def summarize(text: str) -> str:
    """对脱敏后的日志做关键事件计数，输出摘要文本。"""
    lines = text.splitlines()
    lower_lines = [line.lower() for line in lines]
    out = [f"总行数: {len(lines)}"]
    if lines:
        out.append(f"时间窗首行: {lines[0][:120]}")
        out.append(f"时间窗尾行: {lines[-1][:120]}")
    for label, needle in _PATTERNS:
        count = sum(1 for line in lower_lines if needle in line)
        if count:
            out.append(f"{label}: {count}")
    if not any(needle in line for line in lower_lines for _, needle in _PATTERNS):
        out.append("无关键事件命中")
    return "\n".join(out) + "\n"


def pull(host: str, since: str) -> str:
    """经 ssh 拉 journalctl 原文；失败抛 RuntimeError。"""
    unit_args = " ".join(f"-u {unit}" for unit in UNITS)
    # 远端是 POSIX shell：用 shlex.quote（list2cmdline 是 Windows 风格，双引号不抑制远端 $() 展开）。
    cmd = (
        f"journalctl {unit_args} --since {shlex.quote(since)} "
        "--no-pager -q"
    )
    proc = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", host, cmd],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ssh/journalctl 失败（exit {proc.returncode}）：{proc.stderr.strip()[:200]}")
    return proc.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST, help="ssh 目标（默认 %(default)s）")
    parser.add_argument("--since", default="24 hours ago", help="journalctl --since（默认 %(default)s）")
    parser.add_argument("-o", "--out-dir", default=None, help="输出目录（默认 results/local-only/<今天>/）")
    args = parser.parse_args()

    out_dir = Path(args.out_dir) if args.out_dir else (
        Path(__file__).resolve().parents[1] / "results" / "local-only" / _dt.date.today().isoformat()
    )

    try:
        raw = pull(args.host, args.since)
    except (RuntimeError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        print(f"拉取失败：{exc}", file=sys.stderr)
        return 2

    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        redacted = redact(raw)
        summary = summarize(redacted)
        (out_dir / f"cloud-health-{stamp}.log").write_text(redacted, encoding="utf-8")
        (out_dir / f"cloud-health-{stamp}.summary.txt").write_text(summary, encoding="utf-8")
    except OSError as exc:
        print(f"写盘失败：{exc}", file=sys.stderr)
        return 1

    print(f"已落盘：{out_dir / f'cloud-health-{stamp}.log'}")
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
