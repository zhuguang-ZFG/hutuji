#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hutuji 枢纽仓 Agent 门禁。

定义见 docs/agent-gate.md。不替代 fz 的 Grbl 门禁。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
REPORT_JSON = RESULTS / "hutuji_gate_last.json"
REPORT_MD = RESULTS / "hutuji_gate_last.md"

REQUIRED_FILES = [
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
    "docs/agent-handoff.md",
    "docs/agent-gate.md",
    "docs/agent-anti-drift.md",
    "docs/agent-constraint-matrix.md",
    "docs/worktree-inventory.md",
    "docs/agent-edit-allowlist.json",
    "docs/foundation-checklist.md",
    "docs/release-readiness.md",
    "docs/protocol.md",
    "docs/bringup-sop.md",
    "docs/development.md",
    "docs/deployment.md",
    "docs/hardware.md",
    "docs/hardware/mk38-board.md",
    "docs/hardware/lichuang-szpi-esp32s3-board.md",
    "docs/hardware/lichuang-szpi-official/README.md",
    "deploy/README.md",
    "deploy/env.mcp.example",
    "deploy/env.bridge.example",
    "deploy/prepare-host.sh",
    "deploy/preflight.sh",
    "docs/templates/device-test-record.md",
    "docs/templates/release-manifest.md",
    "mcp-server/README.md",
    "firmware/README.md",
    "deploy/README.md",
    "deploy/env.example",
    ".cursor/rules/hutuji-agent-rails.mdc",
]

ALLOWLIST_REQUIRED_KEYS = [
    "version",
    "grbl_section9_only",
    "grbl_core_headers_restore_only",
    "grbl_forbidden_symbols",
    "claims_forbidden",
]

ANTI_DRIFT_MARKERS = [
    "无证据不宣称",
    "无清单不改代码",
    "不覆盖用户工作树",
    "不为门禁绿去改产品行为",
]

CONSTRAINT_MATRIX_MARKERS = [
    "三仓 Agent 开发约束矩阵",
    "门禁矩阵",
    "禁止改动矩阵",
    "Grbl_Esp32",
    "xiaozhi-esp32",
]

# 根 README 决策锚点（缺失 = 文档层红）
DECISION_ANCHORS = [
    "双芯片",
    "WiFi Telnet",
    "117.72.118.95",
    "protocol.md",
    "§9",
]

# 在「现行方案」语境复活否决项时判红（允许出现在已否决叙述）
REVIVED_PATTERNS = [
    (
        "usb_host_as_current",
        re.compile(
            r"(板间|链路|通讯).{0,24}(USB\s*Host|原生\s*USB)|"
            r"(现行|当前|改用|采用).{0,16}USB\s*Host",
            re.I,
        ),
        ["docs/agent-handoff.md", "docs/protocol.md", "README.md", "AGENTS.md"],
    ),
    (
        "ch9350_as_current",
        re.compile(r"(现行|当前|改用|采用).{0,16}CH9350|(板间|链路).{0,16}CH9350", re.I),
        ["docs/agent-handoff.md", "README.md", "AGENTS.md"],
    ),
    (
        "aliyun_as_prod",
        re.compile(r"(部署|生产|落点|VPS).{0,16}阿里云|阿里云.{0,16}(部署|生产|VPS)", re.I),
        ["docs/deployment.md", "README.md", "AGENTS.md", "docs/agent-handoff.md"],
    ),
]

CLAIMS_FORBIDDEN = [
    "可出货",
    "商业放行",
    "§9 已完成",
    "§9 已修好",
    "硬件已验证",
    "联调完成",
    "京东云已上线",
    "M2 已完成",
]

# Grbl §9 源码标记（full profile；不替代 fz overall=pass）
SECTION9_MARKERS = [
    (
        "Grbl_Esp32/src/WebUI/TelnetServer.cpp",
        ["TCP_KEEPIDLE", "TCP_KEEPINTVL", "TCP_KEEPCNT", "SO_KEEPALIVE"],
        "§9-A keepalive",
    ),
    (
        "Grbl_Esp32/src/PaperSystem.cpp",
        ['"[PaperStatus]', "CLIENT_ALL"],
        "§9-B1′ PaperStatus→CLIENT_ALL",
    ),
    (
        "Grbl_Esp32/src/PaperSystem.cpp",
        ["Changing=%s", "paper_auto_change_is_running"],
        "§9-B2′ Changing 字段",
    ),
]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _layer(lid: str, name: str, status: str, detail: str = "", **extra: Any) -> dict[str, Any]:
    out = {"id": lid, "name": name, "status": status, "detail": detail}
    out.update(extra)
    return out


def check_required_files() -> dict[str, Any]:
    missing = [p for p in REQUIRED_FILES if not (ROOT / p).is_file()]
    if missing:
        return _layer("required_files", "必备文件", "fail", f"missing={missing}")
    return _layer("required_files", "必备文件", "pass", f"count={len(REQUIRED_FILES)}")


def check_decision_anchors() -> dict[str, Any]:
    text = (ROOT / "README.md").read_text(encoding="utf-8", errors="replace")
    protocol = (ROOT / "docs/protocol.md").read_text(encoding="utf-8", errors="replace")
    missing = [a for a in DECISION_ANCHORS if a not in text and a not in protocol]
    # §9 必须在 protocol；双芯片等必须在 README
    hard_missing = []
    if "双芯片" not in text:
        hard_missing.append("双芯片@README")
    if "WiFi Telnet" not in text:
        hard_missing.append("WiFi Telnet@README")
    if "117.72.118.95" not in text:
        hard_missing.append("117.72.118.95@README")
    if "## 9." not in protocol and "§9" not in protocol:
        hard_missing.append("§9@protocol")
    if hard_missing:
        return _layer("decision_anchors", "决策锚点", "fail", f"missing={hard_missing}")
    if missing:
        return _layer(
            "decision_anchors",
            "决策锚点",
            "pass",
            f"ok; soft_absent_in_readme_only={[a for a in missing if a in protocol or a in text]}",
        )
    return _layer("decision_anchors", "决策锚点", "pass", "all present")


def check_revived_decisions() -> dict[str, Any]:
    hits: list[str] = []
    for kid, pattern, files in REVIVED_PATTERNS:
        for rel in files:
            path = ROOT / rel
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for m in pattern.finditer(text):
                snippet = text[max(0, m.start() - 40) : m.end() + 40].replace("\n", " ")
                # 允许「已否决 / 不要 / 禁止 / 三次推翻」语境
                window = text[max(0, m.start() - 80) : m.end() + 80]
                if re.search(r"已否决|否决|不要|禁止|别再|推翻|过时|不可行", window):
                    continue
                hits.append(f"{kid}:{rel}:{snippet.strip()[:120]}")
    if hits:
        return _layer("revived_decisions", "否决项复活扫描", "fail", "; ".join(hits[:5]))
    return _layer("revived_decisions", "否决项复活扫描", "pass", "no revival")


def check_edit_allowlist() -> dict[str, Any]:
    path = ROOT / "docs/agent-edit-allowlist.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _layer("edit_allowlist", "改码白名单 JSON", "fail", str(exc))
    missing = [k for k in ALLOWLIST_REQUIRED_KEYS if k not in data]
    if missing:
        return _layer("edit_allowlist", "改码白名单 JSON", "fail", f"missing_keys={missing}")
    if not isinstance(data.get("grbl_section9_only"), list) or not data["grbl_section9_only"]:
        return _layer("edit_allowlist", "改码白名单 JSON", "fail", "grbl_section9_only empty")
    if "report_realtime_status" not in data.get("grbl_forbidden_symbols", []):
        return _layer("edit_allowlist", "改码白名单 JSON", "fail", "forbidden symbols incomplete")
    return _layer("edit_allowlist", "改码白名单 JSON", "pass", f"keys={len(data)}")


def check_anti_drift_doc() -> dict[str, Any]:
    path = ROOT / "docs/agent-anti-drift.md"
    text = path.read_text(encoding="utf-8", errors="replace")
    missing = [m for m in ANTI_DRIFT_MARKERS if m not in text]
    inv = ROOT / "docs/worktree-inventory.md"
    if not inv.is_file():
        return _layer("anti_drift", "防幻觉文档", "fail", "missing worktree-inventory.md")
    inv_text = inv.read_text(encoding="utf-8", errors="replace")
    if "Grbl_Esp32" not in inv_text or "不要" not in inv_text:
        return _layer("anti_drift", "防幻觉文档", "fail", "worktree-inventory 缺三仓/禁令要点")
    if missing:
        return _layer("anti_drift", "防幻觉文档", "fail", f"anti-drift missing={missing}")
    return _layer("anti_drift", "防幻觉文档", "pass", "iron laws + inventory present")


def check_constraint_matrix() -> dict[str, Any]:
    path = ROOT / "docs/agent-constraint-matrix.md"
    text = path.read_text(encoding="utf-8", errors="replace")
    missing = [m for m in CONSTRAINT_MATRIX_MARKERS if m not in text]
    # 外仓 AGENTS 入口：存在则记入 detail；缺失只 warn 不红（外仓可能未检出）
    external = {
        "Grbl_Esp32/AGENTS.md": Path("D:/Users/Grbl_Esp32/AGENTS.md"),
        "xiaozhi-esp32/AGENTS.md": Path("D:/Users/xiaozhi-esp32/AGENTS.md"),
        "fz/AGENTS.md": Path("D:/Users/zhugu/fz/AGENTS.md"),
    }
    present = [name for name, p in external.items() if p.is_file()]
    absent = [name for name, p in external.items() if not p.is_file()]
    if missing:
        return _layer("constraint_matrix", "三仓约束矩阵", "fail", f"missing={missing}")
    detail = f"matrix_ok; external_agents_present={present}"
    if absent:
        detail += f"; absent={absent}（请在外仓补齐 AGENTS.md）"
        # Grbl 缺失视为红：商业固件无入口即约束未闭环
        if "Grbl_Esp32/AGENTS.md" in absent:
            return _layer("constraint_matrix", "三仓约束矩阵", "fail", detail)
    return _layer("constraint_matrix", "三仓约束矩阵", "pass", detail)


def _mcp_python() -> Path | None:
    win = ROOT / "mcp-server" / ".venv" / "Scripts" / "python.exe"
    unix = ROOT / "mcp-server" / ".venv" / "bin" / "python"
    if win.is_file():
        return win
    if unix.is_file():
        return unix
    return None


def run_pytest() -> dict[str, Any]:
    py = _mcp_python()
    if py is None:
        return _layer("pytest", "mcp-server pytest", "fail", "缺少 mcp-server/.venv，请先建 venv")
    proc = subprocess.run(
        [str(py), "-m", "pytest", "-q"],
        cwd=ROOT / "mcp-server",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    tail = (proc.stdout + proc.stderr).strip().splitlines()
    detail = tail[-1] if tail else f"exit={proc.returncode}"
    status = "pass" if proc.returncode == 0 else "fail"
    return _layer("pytest", "mcp-server pytest", status, detail, exit_code=proc.returncode)


def run_e2e() -> dict[str, Any]:
    py = _mcp_python()
    if py is None:
        return _layer("e2e_local", "e2e_local.py", "fail", "缺少 mcp-server/.venv")
    proc = subprocess.run(
        [str(py), "scripts/e2e_local.py"],
        cwd=ROOT / "mcp-server",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    out = (proc.stdout + proc.stderr).strip()
    ok = proc.returncode == 0 and re.search(r"\bPASS\b", out) is not None
    detail = out.splitlines()[-1] if out else f"exit={proc.returncode}"
    return _layer("e2e_local", "e2e_local.py", "pass" if ok else "fail", detail, exit_code=proc.returncode)


def check_section9(grbl_root: Path | None) -> dict[str, Any]:
    if grbl_root is None:
        return _layer(
            "section9_markers",
            "Grbl §9 源码标记",
            "skip",
            "未设置 GRBL_ROOT；改 Grbl 后必须设置并重跑 full，且另跑 fz 门禁",
        )
    if not grbl_root.is_dir():
        return _layer("section9_markers", "Grbl §9 源码标记", "fail", f"GRBL_ROOT 不存在: {grbl_root}")
    missing: list[str] = []
    for rel, needles, label in SECTION9_MARKERS:
        path = grbl_root / rel
        if not path.is_file():
            missing.append(f"{label}: missing file {rel}")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for n in needles:
            if n not in text:
                missing.append(f"{label}: missing `{n}` in {rel}")
    if missing:
        return _layer("section9_markers", "Grbl §9 源码标记", "fail", "; ".join(missing))
    return _layer(
        "section9_markers",
        "Grbl §9 源码标记",
        "pass",
        "源码标记齐；仍须 fz agent_gate overall=pass 才能声称 §9 修好",
    )


def write_reports(report: dict[str, Any]) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        f"# hutuji gate — {report['overall_status']}",
        "",
        f"- profile: `{report['profile']}`",
        f"- time: {report['timestamp']}",
        f"- overall: **{report['overall_status']}** (exit={report['overall_exit']})",
        "",
        "## layers",
        "",
    ]
    for layer in report["layers"]:
        lines.append(f"- `{layer['id']}`: **{layer['status']}** — {layer.get('detail', '')}")
    lines.extend(
        [
            "",
            "## claims_forbidden（绿了也不能说）",
            "",
        ]
    )
    for c in report["claims_forbidden"]:
        lines.append(f"- {c}")
    lines.extend(
        [
            "",
            "## next",
            "",
            f"- 报告: `{REPORT_JSON}`",
            "- 改 Grbl 后另跑: `python D:/Users/zhugu/fz/scripts/agent_gate.py --profile standard`",
            "- 防呆: `docs/agent-handoff.md`",
            "",
        ]
    )
    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="hutuji 枢纽仓 Agent 门禁")
    parser.add_argument(
        "--profile",
        choices=("docs", "hub", "full"),
        default="hub",
        help="docs=文档层; hub=文档+MCP(默认); full=hub+§9源码标记",
    )
    args = parser.parse_args(argv)

    layers: list[dict[str, Any]] = []
    layers.append(check_required_files())
    layers.append(check_decision_anchors())
    layers.append(check_revived_decisions())
    layers.append(check_edit_allowlist())
    layers.append(check_anti_drift_doc())
    layers.append(check_constraint_matrix())

    if args.profile in ("hub", "full"):
        layers.append(run_pytest())
        layers.append(run_e2e())

    if args.profile == "full":
        grbl = os.environ.get("GRBL_ROOT")
        layers.append(check_section9(Path(grbl) if grbl else None))

    failed = [L for L in layers if L["status"] == "fail"]
    overall = "fail" if failed else "pass"
    exit_code = 1 if failed else 0

    report: dict[str, Any] = {
        "suite": "hutuji_agent_gate",
        "version": 1,
        "profile": args.profile,
        "timestamp": _now(),
        "root": str(ROOT),
        "overall_status": overall,
        "overall_exit": exit_code,
        "layers": layers,
        "failures": failed,
        "claims_forbidden": CLAIMS_FORBIDDEN,
        "next_commands": {
            "docs": "python scripts/agent_gate.py --profile docs",
            "hub": "python scripts/agent_gate.py --profile hub",
            "full": "set GRBL_ROOT=D:\\Users\\Grbl_Esp32 && python scripts/agent_gate.py --profile full",
            "fz_grbl": "python D:/Users/zhugu/fz/scripts/agent_gate.py --profile standard",
        },
        "report_json": str(REPORT_JSON),
        "report_md": str(REPORT_MD),
    }
    write_reports(report)

    print(f"hutuji_gate profile={args.profile} overall={overall} exit={exit_code}")
    for L in layers:
        print(f"  [{L['status']}] {L['id']}: {L.get('detail', '')}")
    if failed:
        print("FAIL — 不得宣称本 profile 通过；见 results/hutuji_gate_last.md")
    else:
        print("PASS — 仍受 claims_forbidden 约束；见 docs/agent-gate.md")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
