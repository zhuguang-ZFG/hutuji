#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hutuji 枢纽仓 Agent 门禁。

定义见 docs/agent-gate.md。不替代 fz 的 Grbl 门禁。
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
REPORT_JSON = RESULTS / "hutuji_gate_last.json"
REPORT_MD = RESULTS / "hutuji_gate_last.md"
RUNNING_MARK = RESULTS / ".hutuji_gate_running"

# 行号内容指纹基线（入库 tracked）。指纹层查的是「行号在界内但指向内容已变」的
# 半漂移；基线只能由 --accept-line-refs 显式重新登记，重新登记前必须人工核对
# 被引用行仍是当初证据所指——否则指纹层退化成自动追认。
LINE_REF_BASELINE_REL = "docs/agent-line-refs-baseline.json"

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
    "docs/xiaozhi-agent/README.md",
    "docs/xiaozhi-agent/role-prompt.md",
    "docs/xiaozhi-agent/knowledge-base.md",
    "docs/templates/device-test-record.md",
    "docs/templates/release-manifest.md",
    "mcp-server/README.md",
    "firmware/README.md",
    "deploy/env.example",
    ".cursor/rules/hutuji-agent-rails.mdc",
    LINE_REF_BASELINE_REL,
]

ALLOWLIST_REQUIRED_KEYS = [
    "version",
    "description",
    "decision_count",
    "grbl_section9_only",
    "grbl_core_headers_restore_only",
    "grbl_forbidden_symbols",
    "grbl_forbidden_intents",
    "hub_safe_doc_globs",
    "claims_forbidden",
    "rejected_current_link",
]

ALLOWLIST_EXACT = {
    "version": 1,
    "description": "Agent 改码白名单（机读）。越界改动须用户点名。门禁 docs 层校验本文件存在且含关键键。",
    "decision_count": 7,
    "grbl_section9_only": [
        "Grbl_Esp32/src/WebUI/TelnetServer.cpp",
        "Grbl_Esp32/src/PaperSystem.cpp",
        "Grbl_Esp32/src/Protocol.cpp",
        "Grbl_Esp32/src/ProtocolDecisionCore.h",
        "Grbl_Esp32/src/WebUI/BTConfig.cpp",
        "Grbl_Esp32/src/WebUI/WebSettings.cpp",
    ],
    "grbl_core_headers_restore_only": [
        "Grbl_Esp32/src/LicenseCore.h",
        "Grbl_Esp32/src/PaperBtAckCore.h",
        "Grbl_Esp32/src/PaperSearchCore.h",
        "Grbl_Esp32/src/PaperSystemCore.h",
        "Grbl_Esp32/src/WebUI/BTStateCore.h",
    ],
    "grbl_forbidden_symbols": ["report_realtime_status", "report_state_text"],
    "grbl_forbidden_intents": [
        "change_paper_mechanics_or_timing",
        "change_motion_planner",
        "change_license_algorithm_behavior",
        "change_u1_grbl_protocol",
        "broadcast_all_paper_auto_messages_client_all",
    ],
    "hub_safe_doc_globs": [
        "docs/**",
        "AGENTS.md",
        "CLAUDE.md",
        "README.md",
        ".cursor/rules/**",
        "scripts/agent_gate.py",
        "deploy/**",
        "firmware/*.md",
    ],
    "rejected_current_link": [
        "USB Host as current board link",
        "CH9350 as current board link",
        "Aliyun as production VPS",
    ],
}

ANTI_DRIFT_MARKERS = [
    "无证据不宣称",
    "无清单不改代码",
    "不覆盖用户工作树",
    "不为门禁绿去改产品行为",
    "不准简化设计",
    "先读后动",
    "全称必证",
]

CONSTRAINT_MATRIX_MARKERS = [
    "三仓 Agent 开发约束矩阵",
    "门禁矩阵",
    "禁止改动矩阵",
    "Grbl_Esp32",
    "xiaozhi-esp32",
]

# 根 README 决策锚点（缺失 = 文档层红）。与 docs/agent-gate.md §4 逐条对应。
# 第 3 项「哑管道 / 云端生成」是「或」关系，其硬检查见 check_decision_anchors()。
DECISION_ANCHORS = [
    "双芯片",
    "WiFi Telnet",
    "哑管道",
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
            re.I | re.S,
        ),
        None,
    ),
    (
        "ch9350_as_current",
        re.compile(
            r"(现行|当前|改用|采用).{0,16}CH9350|(板间|链路).{0,16}CH9350",
            re.I | re.S,
        ),
        None,
    ),
    (
        "aliyun_as_prod",
        re.compile(
            r"(部署|生产|落点|VPS).{0,16}阿里云|阿里云.{0,16}(部署|生产|VPS)",
            re.I | re.S,
        ),
        None,
    ),
]

# 复活扫描与禁语扫描共同的文件面。原先三条 REVIVED_PATTERNS 各带一份手写清单，
# 合计只覆盖 5 个文件，漏掉的恰是优先级最高的几份：`CLAUDE.md`（对 agent 是
# OVERRIDE 级指令）、`.cursor/rules/hutuji-agent-rails.mdc`（alwaysApply 硬轨）、
# 以及 `release-readiness.md`/`worktree-inventory.md`（进度真值）。实测把
# 「当前采用 USB Host，已改用 CH9350，生产落点阿里云」写进 CLAUDE.md、或把
# 「当前采用 CH9350」写进 protocol.md（ch9350 规则不含该文件），门禁均全绿。
# 治理面要么全扫，要么这层就是装饰。新增治理文档请加进本清单。
GOVERNANCE_DOCS = [
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
    "docs/agent-handoff.md",
    "docs/agent-anti-drift.md",
    "docs/agent-constraint-matrix.md",
    "docs/agent-gate.md",
    "docs/protocol.md",
    "docs/deployment.md",
    "docs/release-readiness.md",
    "docs/worktree-inventory.md",
    "docs/foundation-checklist.md",
    "docs/bringup-sop.md",
    # runbook 决定「哪几步真机验证可以跳过」：它写「已完成/可跳过」，后续代理就
    # 不再上真机复验。此前它不在本清单，其状态措辞完全无机器把关——与上面注释里
    # 记的 CLAUDE.md 漏扫是同一形态。加入后禁语与复活两层同时覆盖它。
    "docs/daytime-tuning-runbook.md",
    "docs/operations.md",
    "deploy/README.md",
    "mcp-server/README.md",
    ".cursor/rules/hutuji-agent-rails.mdc",
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
    (
        "Grbl_Esp32/src/PaperSystem.cpp",
        ["compare_exchange_strong", "std::atomic<bool> paper_auto_change_running"],
        "§9-E′ 换纸入口原子认领",
    ),
]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git_head() -> str | None:
    """当前 HEAD 完整 SHA；非 git 环境返回 None 而不是炸掉门禁。

    报告绑定 HEAD 是给「肉眼读报告」的消费方一个新旧判据：报告里的 HEAD
    与 `git rev-parse HEAD` 不符即过期。所有失败路径都必须静默降级
    （测试打桩 subprocess.run 覆盖无 git / 非 git 仓两条路径）。"""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None

def _pid_is_alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        # 无权探测也不能覆盖可能仍在运行的其他门禁。
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return False
    return True


def _write_running_mark(profile: str, plan: list[str]) -> str:
    """以 O_EXCL 原子占有门禁；返回本轮 owner token，防并发进程互删标记。"""
    RESULTS.mkdir(parents=True, exist_ok=True)
    owner = uuid.uuid4().hex
    payload = {
        "pid": os.getpid(),
        "owner": owner,
        "profile": profile,
        "started": _now(),
        "git_head": _git_head(),
        "plan": plan,
    }
    fd = os.open(RUNNING_MARK, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return owner


def _release_running_mark(owner: str) -> None:
    try:
        payload = json.loads(RUNNING_MARK.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if payload.get("owner") == owner:
        RUNNING_MARK.unlink(missing_ok=True)


def _layer(lid: str, name: str, status: str, detail: str = "", **extra: Any) -> dict[str, Any]:
    out = {"id": lid, "name": name, "status": status, "detail": detail}
    out.update(extra)
    return out


def required_file_set() -> list[str]:
    """必备文件面 = REQUIRED_FILES ∪ GOVERNANCE_DOCS（保序去重）。

    并集在**调用时**现算，而不是把治理文档手抄进 REQUIRED_FILES。手抄版只能靠一条
    静态包含关系测试兜，那条测试证明的是「当下没漂」，管不住下一次新增：两个扫描器
    对读不到的文件都只 continue，`forbidden_claims` 的 pass 文案却照打
    len(GOVERNANCE_DOCS)，于是新增的治理文档一旦被删/改名，`docs` 层（不跑 pytest）
    会静默少扫一份并印出与实际扫描面脱钩的份数。现算并集把「治理文档必存在」变成
    结构不变量：往 GOVERNANCE_DOCS 加一行，它当场就受必备文件层保护。
    """
    seen: dict[str, None] = dict.fromkeys(REQUIRED_FILES)
    seen.update(dict.fromkeys(GOVERNANCE_DOCS))
    return list(seen)


def check_required_files() -> dict[str, Any]:
    # lstat 语义：symlink 视为缺失。门禁校验的是真实落盘的必备文件，
    # 链接到别处的文件可能在部署后被抽走/换指向而不被发现。
    required = required_file_set()
    missing = [
        p
        for p in required
        if (ROOT / p).is_symlink() or not (ROOT / p).is_file()
    ]
    if missing:
        return _layer("required_files", "必备文件", "fail", f"missing={missing}")
    return _layer("required_files", "必备文件", "pass", f"count={len(required)}")


def check_decision_anchors() -> dict[str, Any]:
    # R5-GATE-01：治理文档 read_text 必须守卫 OSError——治理文档被删/不可读时
    # 裸抛会穿透 main() 层循环（旧行为），这里塌成本层结构化 fail（双保险）。
    try:
        text = (ROOT / "README.md").read_text(encoding="utf-8", errors="replace")
        protocol = (ROOT / "docs/protocol.md").read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return _layer("decision_anchors", "决策锚点", "fail", f"无法读取治理文档（{type(exc).__name__}）")
    missing = [a for a in DECISION_ANCHORS if a not in text and a not in protocol]
    # §9 必须在 protocol；双芯片等必须在 README
    hard_missing = []
    if "双芯片" not in text:
        hard_missing.append("双芯片@README")
    if "WiFi Telnet" not in text:
        hard_missing.append("WiFi Telnet@README")
    if "117.72.118.95" not in text:
        hard_missing.append("117.72.118.95@README")
    # 决策 ③ 哑管道原则（docs/agent-gate.md §4 第 3 项，「哑管道」或「云端生成」二者之一）
    if "哑管道" not in text and "云端生成" not in text:
        hard_missing.append("哑管道|云端生成@README")
    if "## 9." not in protocol and "§9" not in protocol:
        hard_missing.append("§9@protocol")
    if hard_missing:
        return _layer("decision_anchors", "决策锚点", "fail", f"missing={hard_missing}")
    if missing:
        # GATE-P2-02：此前这里报的是 `[a for a in missing if a in protocol or a in text]`。
        # missing 的构造条件恰是「既不在 text 也不在 protocol」，故该推导式恒为空列表——
        # 软锚点缺多少条，报告里永远印 `soft_absent_in_readme_only=[]`。软锚点缺失是
        # 「决策锚点在两份真值文档里都找不到」，只是不判红，不代表不该说出来。直接报 missing。
        return _layer(
            "decision_anchors",
            "决策锚点",
            "pass",
            f"ok; soft_absent={missing}（两份真值文档里都未出现，不判红但请复核）",
        )
    return _layer("decision_anchors", "决策锚点", "pass", "all present")


def _rejection_clause(text: str, match: re.Match[str]) -> str:
    """只在命中所在短语内认可「已否决」语境，不能借用相邻选项的结论。"""
    boundaries = "\n：:，,。！？!?；;（）()、"
    separators = ("——",)
    starts = [text.rfind(char, 0, match.start()) for char in boundaries]
    starts.extend(text.rfind(separator, 0, match.start()) for separator in separators)
    start = max(starts, default=-1) + 1
    ends = [text.find(char, match.end()) for char in boundaries]
    ends.extend(text.find(separator, match.end()) for separator in separators)
    end = min((value for value in ends if value >= 0), default=len(text))
    return text[start:end]

# 「现在改成这个了」的采纳断言词。命中短语里出现它们，说明该短语自身在做主张，
# 上文的禁令清单掩护不了它。刻意只收动词性的采纳表述，不收 `落点`/`落地`/`当`——
# 那些是 REVIVED_PATTERNS 自己的关键词或列举用语，收进来会把合法的否决清单项判红。
# 判据取向是 fail-closed：真要在禁令清单里写「采用 X」，请改用反引号引述形态。
_ADOPTION_RE = re.compile(r"现已|已改用|已采用|改用|采用|改为|换成|现行方案|当前方案")


def _has_rejection_heading(text: str, match: re.Match[str]) -> bool:
    """整行的否决/禁令清单适用于其后的各个并列项。

    命中若落在「禁止重开：A / B / C」这类并列清单里，否定词只在标题上，
    `_rejection_clause` 按标点切分后会把它切走，于是每个并列项单独看都像在做主张。
    同一行内、命中之前出现否决类标题即视为整行受该标题约束。

    `agent-gate.md` 自身的「若重新主张下列**已否决**方向 → 红：」+ 列表项，以及
    `AGENTS.md` 的「**禁止宣称**（…）：可出货、商业放行、…」都属这种形态；只认
    「禁止重开」两种字样时它们都会误报（实测 revived 与 claims 两层各误报一处）。
    """
    # 命中短语自身就在做「现在改成这个了」的断言时，任何上文禁令都不豁免它。禁令清单
    # 只掩护**列举**否决项的并列项（`- USB Host`、`- 阿里云`），掩护不了混进清单里的
    # 采纳句。实测 `## 禁止重开` + `- 现已改用 USB Host 直连`、以及标题名自带否决词的
    # `## 已否决方案的替代实现` + `- 采用 CH9350 桥接替代`，在只看上文否决词时都全绿。
    # 只看命中所在短语，不看整行——行尾常有「这些都已否决或已落地」之类的收尾语。
    if _ADOPTION_RE.search(_rejection_clause(text, match)):
        return False
    line_start = text.rfind("\n", 0, match.start()) + 1
    prefix = text[line_start : match.start()]
    if re.search(r"(?:禁止重开|明确不要做的事)(?:\*\*)?\s*[：:]", prefix):
        return True
    # 否决/禁令类标题 + 冒号，冒号后为并列项。粗体标记与括号补充说明都可穿过。
    #
    # 否决词与冒号之间**只允许**粗体标记与括号补充，不允许再有一段谓语。原先此处放宽
    # 到 `[^：:\n]{0,40}`，等于「同一行里任何位置出现过禁令词 + 冒号」就豁免整行余下
    # 内容，实测 `禁止在出图中发 M 命令：板间链路改用 USB Host 直连。` 与
    # `禁止跳过 bringup-sop：本机硬件已验证。` 两句都能全绿——前半句的禁令管的是它自己
    # 的宾语，管不到冒号后另起的主张。`不证明：`/`**禁止宣称**（除非…）：` 这类真引述
    # 清单里，否决词本身就是整个导语，故收紧后仍豁免。
    if re.search(
        r"(?:禁止宣称|禁止|不得|不可|严禁|勿|别再|已否决|否决|"
        r"claims_forbidden|不许|无权|不证明)"
        r"(?:\*\*)?\s*(?:[（(][^）)\n]{0,80}[）)])?\s*[：:]",
        prefix,
    ):
        return True
    # 上方最近的非空行是引出否决清单的导语或 Markdown 标题。覆盖两种排版：
    #   「不证明：」+ 换行 + `- 项`（导语以「：」收尾）
    #   `## 禁止宣称（…）` + 空行 + 并列项（标题行，不以「：」收尾）
    # `.cursor/rules/hutuji-agent-rails.mdc` 的禁语清单正是后者，只看同一行会误报。
    # 向上找引出清单的导语/标题，跨过空行与**同级列表项**（清单有多项时，命中所在
    # 项的上一行往往是另一个并列项，遇到就停会漏掉导语——`agent-gate.md` 的
    # 「若重新主张下列**已否决**方向 → 红：」+ 三个列表项即此形态）。
    # 只跨列表项与空行，遇到普通散文段落即停，避免把远处无关的否决语借过来。
    in_list = bool(re.match(r"\s*[-*+]\s|\s*\d+[.)]\s", text[line_start : match.start()] or " "))
    for prev in reversed(text[:line_start].splitlines()):
        if not prev.strip():
            continue
        negated = re.search(
            r"已否决|否决|禁止|禁语|不得|不可|勿|别再|推翻|过时|不可行|红|不证明|无权",
            prev,
        )
        if negated and (prev.lstrip().startswith("#") or prev.rstrip().endswith(("：", ":"))):
            return True
        # 同级并列项：继续往上找导语
        if in_list and re.match(r"\s*[-*+]\s|\s*\d+[.)]\s", prev):
            continue
        break
    return False



def _strip_markdown_noise(text: str) -> str:
    """去掉可切断治理短语的零语义装饰；保留换行，避免改变句窗边界。"""
    text = re.sub(
        r"<!--[\s\S]*?-->",
        lambda match: "\n" * match.group(0).count("\n"),
        text,
    )
    return re.sub(r"(?<!\\)(?:\*\*|__)", "", text)


def _revival_clause_is_rejected(text: str, match: re.Match[str]) -> bool:
    """只认可直接否定命中方案的短语；采纳句不能借后半句无关禁令脱身。"""
    clause = _rejection_clause(text, match).strip()
    matched = match.group(0)
    # 方案短语可能从「通讯」起匹配，故否定词不一定在 clause 前缀；只接受
    # 否定词紧贴同一方案名，且不能把「当前采用 X（不是 X）」误判成否决。
    target_denial = re.search(
        r"(?:不是|并非|不使用|未采用)\s*(?:USB\s*Host|原生\s*USB|CH9350|阿里云)",
        matched,
        re.I,
    )
    if target_denial and not _ADOPTION_RE.search(matched[: target_denial.start()]):
        return True
    # `当前禁止采用 X` 的「采用」是被禁止的宾语，不是采纳断言；直接否定优先。
    if re.search(r"^(?:当前)?(?:已否决|否决|不要|禁止|别再|推翻|过时|不可行)", clause):
        return True
    return False


def check_revived_decisions() -> dict[str, Any]:
    hits: list[str] = []
    for kid, pattern, files in REVIVED_PATTERNS:
        for rel in files if files is not None else GOVERNANCE_DOCS:
            path = ROOT / rel
            if not path.is_file():
                continue
            text = _strip_markdown_noise(
                _strip_invisible(path.read_text(encoding="utf-8", errors="replace"))
            )
            for m in pattern.finditer(text):
                if _has_rejection_heading(text, m) or _revival_clause_is_rejected(text, m):
                    continue
                snippet = text[max(0, m.start() - 40) : m.end() + 40].replace("\n", " ")
                hits.append(f"{kid}:{rel}:{snippet.strip()[:120]}")
    if hits:
        return _layer("revived_decisions", "否决项复活扫描", "fail", "; ".join(hits[:5]))
    return _layer("revived_decisions", "否决项复活扫描", "pass", "no revival")


# 禁语的「引述」语境：这些词出现在命中之前，说明是在讲「不得这样说」的规则，
# 而不是在做出该宣称。`claims_forbidden` 列表本身、`agent-gate.md` §5 的表格、
# 以及各文档里「本条不支持『可出货』」这类免责句都靠它放行。
#
# **词表刻意只留「承重词」**（2026-08-07 收紧）。此前 41 个 token 里 **32 个在当时的 17 份
# 治理文档上零覆盖**——它们不放行任何真实句子，只扩大逃逸面。而其中 `未` / `非` /
# `缺` / `待` / `若` / `别` / `不能` 是汉语高频泛用词，随便一句无关叙述就能给同句的裸
# 宣称当挡箭牌，实测「未来三年规划已定，本机现已可出货。」「该批次非常稳定，硬件已验证。」
# 「谁也不能否认，硬件已验证。」全部逃逸。故删掉零覆盖 token，只保留实测有唯一豁免
# 贡献的：`不得` `不可` `禁止` `不支持` `不提升` 与符号形态 `=` / `!=` / `≠`
# （`禁语` 随判据自述保留）。`=` 必须留：`写了说明 = 代码已改 = 硬件已验证 = 可出货`
# 是本仓反模式句式的标准写法，实测有 3 处唯一豁免。
# 新增豁免词必须先证明它在真实文档上有唯一覆盖，否则一律不加。
_CLAIM_QUOTE_RE = re.compile(
    r"不得|不可|禁止|禁语|不支持|不提升|不升级|=|!=|≠|<>"
)

# 「在讨论门禁判据本身」的元语境标记。只用于给**反引号包裹**的命中开豁免（见
# `_is_quoted_claim`），本身不能豁免散文裸宣称——否则一句「判红标准是本机已联调完成」
# 就能借元语境脱身。本文件与 `agent-gate.md` §5 必须能举反例（`现已可出货，后续不升级`），
# 这类举例正是它存在的唯一理由。
_CLAIM_META_RE = re.compile(
    r"判红|判绿|误报|漏报|滥用|逃逸|口径|句窗|禁语|扫描|门禁|举例|示例|形态|用例|反例|变异"
)

# 「禁语 → 前置条件」定义表右侧单元格的证据类措辞。只用于给**表格首列**命中开豁免。
# 刻意不收 `须` / `需` / `条件` 这类泛用字：它们在任何状态表右列都可能出现，收进来
# 等于把该层还原成无条件放行。这几个词的共同点是「指向一份可复核的外部凭据」。
# `勾选` 是 2026-08-07 实测补的：§5 的 `京东云已上线` 一行右列写的是「`deployment.md`
# 实机项全部勾选」，缺它时这张判据表自己被判红。
_CLAIM_PRECOND_RE = re.compile(r"证据|签核|记录表|前置条件|判据|已填|签署|勾选")


def _claim_sentence(text: str, match: re.Match[str]) -> str:
    """命中所在的整句，用于判断是否处于否定/引述语境。

    比 `_rejection_clause` 的短语窗口宽：否决语境常隔着顿号、斜杠或 `≠` 才到否定词，
    故不按顿号切——那正是并列免责句的分隔符。

    但**起点**要在冒号处停：`禁止跳过 bringup-sop：本机硬件已验证。` 里冒号前的禁令
    管的是它自己的宾语，跨过去就把无关否定词借来当豁免（实测该句原先全绿）。真正
    需要跨冒号的引述形态（`不证明：…`、`**禁止宣称**（除非…）：…`）由
    `_has_rejection_heading` 的导语规则处理，不依赖句窗。
    """
    bounds = "\n。！？!?"
    starts = [text.rfind(ch, 0, match.start()) for ch in bounds]
    starts.extend(text.rfind(ch, 0, match.start()) for ch in "：:")
    start = max(starts, default=-1) + 1
    ends = [text.find(ch, match.end()) for ch in bounds]
    end = min((v for v in ends if v >= 0), default=len(text))
    return text[start:end]


def _is_quoted_claim(text: str, match: re.Match[str]) -> bool:
    """禁语是被「引述」而非「使用」的两种排版形态。

    做出宣称的人写的是散文（「本机已联调完成」），不会把词放进代码引号或
    规则表格的定义列里。但这两层**此前是无条件放行**，于是排版本身成了万能豁免
    （2026-08-07 实测收紧）：
    - `结论：` `硬件已验证` `，可以发货。` 与 `- 当前状态 ``联调完成``` 都靠单纯的
      反引号逃逸；反引号在本仓是「引用术语」的常规排版，不含任何否定语义。
    - `| 硬件已验证 | 是 |`、`| 联调完成 | 2026-08-07 |` 都靠单纯的表格首列逃逸；
      而当时的 17 份治理文档里真正依赖该层的**只有 1 处**（`agent-handoff.md` 的反模式行，
      其命中本身还被「」包着）。

    故两层各加条件：
    - 反引号命中：同句还须有元语境标记（在讨论判据/举例/口径）或常规否决语境，
      否则视为「用反引号强调的裸宣称」。
    - 表格首列：该行后续单元格须出现前置条件/否决类措辞，或命中被「」/『』包裹，
      否则视为「状态表里把话说出去了」。
    """
    line_start = text.rfind("\n", 0, match.start()) + 1
    line_end = text.find("\n", match.end())
    line = text[line_start : line_end if line_end >= 0 else len(text)]
    col = match.start() - line_start
    # 「禁语 → 前置条件」定义表：行以 | 起头，且命中落在首个单元格。
    # **必须排在反引号层之前**：§5 的定义表把禁语写成 `| `可出货` / `商业放行` | … |`，
    # 既在反引号内又在首列。反引号层先返回时，整行没有句号故句窗即整行，也不含元语境
    # 词，于是这张**判据表自己**被判红（2026-08-07 实测 4 处）。表格语义比排版更强，先判。
    if line.lstrip().startswith("|"):
        first_sep = line.find("|", line.index("|") + 1)
        if first_sep < 0 or col < first_sep:
            rest = line[first_sep:] if first_sep >= 0 else ""
            return bool(
                line[max(col - 1, 0)] in "「『"
                or _CLAIM_PRECOND_RE.search(rest)
                or _CLAIM_QUOTE_RE.search(rest)
                or _CLAIM_META_RE.search(rest)
            )
    # 反引号包裹：命中左侧奇数个反引号即位于 inline code 内。
    #
    # AG-F1 加固：计数与元语境窗口的起点从「行首」改为「段首」。模糊模式含 `\s*`，
    # 命中可以跨行（`可\n出货`），此时 `line_start` 落在命中**末行**的行首，前面几行
    # 的反引号全被漏掉——实测 `禁止宣称：\`可\n出货\` / \`硬件 已 验证\`。` 里第二处
    # 命中前有 3 个反引号（奇数，确实在 inline code 内），按行计数只看到 2 个而误判红。
    # 不改成「全文计数」：那样会丢掉元语境层（2026-08-07 加固，docstring 列的三种裸
    # 宣称靠它拦住），且一处不配对的反引号会翻转其后整篇文档的奇偶。段落是 Markdown
    # 里 inline code 的天然上界（反引号不跨空行配对），故按段落计数既修跨行又保住该层。
    #
    # 元语境判定同样取段首到命中处，而非 `_claim_sentence`：后者遇 `：` 即停，
    # 而引述句的标准写法恰是「元语境导语 + `：` + 反引号例子」（`滥用形态举例：
    # \`现已可出货，后续不升级。\``），句窗会把导语整段切掉，把作者的举例判成裸宣称
    # （2026-08-07 实测 E5 判红）。
    para_start = 0
    for sep in ("\n\n", "\r\n\r\n"):
        found = text.rfind(sep, 0, match.start())
        if found >= 0:
            para_start = max(para_start, found + len(sep))
    if text[para_start : match.start()].count("`") % 2 == 1:
        head = text[para_start : match.start()] + _claim_sentence(text, match)
        return bool(_CLAIM_META_RE.search(head) or _CLAIM_QUOTE_RE.search(head))
    return False


def _claim_has_rejection_prefix(text: str, match: re.Match[str]) -> bool:
    """只认可直接约束禁语的否定前缀，拒绝借用同句无关否定词或等号。"""
    sentence = _claim_sentence(text, match)
    claim_at = sentence.find(match.group(0))
    if claim_at < 0:
        return False
    before = sentence[:claim_at]
    compact_before = re.sub(r"[\s`*_「」『』（）()]", "", before)
    compact_before = re.sub(r"^>+", "", compact_before)
    if re.search(r"(?:≠|!=|<>)$", compact_before):
        return True
    compact_overlap = re.sub(r"[\s`*_「」『』（）()]", "", sentence[: claim_at + 1])
    compact_overlap = re.sub(r"^>+", "", compact_overlap)
    if re.search(r"(?:不支持|不提升|不升级|不证明|不得宣称|不得把.{0,32}说成|禁止宣称|无权宣称).{0,32}$", compact_before):
        return True
    return bool(re.search(r"(?:不可|禁止)$", compact_overlap))

def _has_claim_rejection_heading(text: str, match: re.Match[str]) -> bool:
    """只接受专门讨论禁语的标题，不让「禁止删除日志」豁免状态断言。"""
    line_start = text.rfind("\n", 0, match.start()) + 1
    prefix = text[line_start : match.start()]
    if re.search(r"(?:禁止宣称|claims_forbidden|禁语|不证明)\s*[：:]?", prefix):
        return True
    in_list = bool(re.match(r"\s*[-*+]\s|\s*\d+[.)]\s", text[line_start : match.start()] or " "))
    for previous in reversed(text[:line_start].splitlines()):
        if not previous.strip():
            continue
        if previous.lstrip().startswith("#"):
            return bool(re.search(r"禁止宣称|claims_forbidden|禁语", previous))
        if in_list and re.match(r"\s*[-*+]\s|\s*\d+[.)]\s", previous):
            continue
        break
    return False


def check_forbidden_claims() -> dict[str, Any]:
    """扫描治理文档正文里是否做出了 claims_forbidden 中的宣称。"""
    hits: list[str] = []
    scanned = 0
    for rel in GOVERNANCE_DOCS:
        path = ROOT / rel
        if not path.is_file():
            continue
        text = _strip_markdown_noise(
            _strip_invisible(path.read_text(encoding="utf-8", errors="replace"))
        )
        scanned += 1
        for claim in CLAIMS_FORBIDDEN:
            normalized_claim = re.sub(r"\s+", "", claim)
            fuzzy_pattern = r"\s*".join(re.escape(char) for char in normalized_claim)
            for match in re.finditer(fuzzy_pattern, text):
                if _is_quoted_claim(text, match):
                    continue
                if _has_claim_rejection_heading(text, match):
                    continue
                if _claim_has_rejection_prefix(text, match):
                    continue
                sentence = _claim_sentence(text, match)
                snippet = sentence.strip().replace("\n", " ")[:100]
                hits.append(f"{rel}: 「{claim}」于「{snippet}」")
    if hits:
        detail = (
            f"{len(hits)} 处使用了禁止宣称（须先满足 docs/agent-gate.md §5 前置条件）："
            + "; ".join(hits[:5])
        )
        return _layer("forbidden_claims", "禁止宣称扫描", "fail", detail)
    detail = f"治理文档 {scanned}/{len(GOVERNANCE_DOCS)} 份均未做出 {len(CLAIMS_FORBIDDEN)} 条禁止宣称"
    if scanned != len(GOVERNANCE_DOCS):
        detail += "；未扫到的份数由 required_files 层判红"
    return _layer("forbidden_claims", "禁止宣称扫描", "pass", detail)


def check_edit_allowlist() -> dict[str, Any]:
    path = ROOT / "docs/agent-edit-allowlist.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _layer("edit_allowlist", "改码白名单 JSON", "fail", str(exc))
    missing = [key for key in ALLOWLIST_REQUIRED_KEYS if key not in data]
    if missing:
        return _layer("edit_allowlist", "改码白名单 JSON", "fail", f"missing_keys={missing}")
    mismatches = [
        key
        for key, expected in ALLOWLIST_EXACT.items()
        if data.get(key) != expected
    ]
    if data.get("claims_forbidden") != CLAIMS_FORBIDDEN:
        mismatches.append("claims_forbidden")
    if mismatches:
        return _layer(
            "edit_allowlist",
            "改码白名单 JSON",
            "fail",
            f"白名单精确契约不一致={sorted(set(mismatches))}",
        )
    return _layer("edit_allowlist", "改码白名单 JSON", "pass", f"keys={len(data)}")
def check_anti_drift_doc() -> dict[str, Any]:
    # R5-GATE-01：治理文档 read_text 守卫 OSError，塌成本层 fail 而非穿透 main()。
    path = ROOT / "docs/agent-anti-drift.md"
    inv = ROOT / "docs/worktree-inventory.md"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return _layer("anti_drift", "防幻觉文档", "fail", f"无法读取治理文档（{type(exc).__name__}）")
    missing = [m for m in ANTI_DRIFT_MARKERS if m not in text]
    if not inv.is_file():
        return _layer("anti_drift", "防幻觉文档", "fail", "missing worktree-inventory.md")
    try:
        inv_text = inv.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return _layer("anti_drift", "防幻觉文档", "fail", f"无法读取治理文档（{type(exc).__name__}）")
    if "Grbl_Esp32" not in inv_text or "不要" not in inv_text:
        return _layer("anti_drift", "防幻觉文档", "fail", "worktree-inventory 缺三仓/禁令要点")
    if missing:
        return _layer("anti_drift", "防幻觉文档", "fail", f"anti-drift missing={missing}")
    return _layer("anti_drift", "防幻觉文档", "pass", "iron laws + inventory present")


def check_constraint_matrix() -> dict[str, Any]:
    # R5-GATE-01：治理文档 read_text 守卫 OSError，塌成本层 fail 而非穿透 main()。
    path = ROOT / "docs/agent-constraint-matrix.md"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return _layer("constraint_matrix", "三仓约束矩阵", "fail", f"无法读取治理文档（{type(exc).__name__}）")
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


# 文档里的 `path:line` / `path:line-line` 引用。只认带扩展名的相对路径，避免把
# `127.0.0.1:8300` 这类端口写法当成行号引用。
#
# GATE-P2-03a：扩展名白名单原先漏了 `conf`/`service`/`ino`，而本仓文档里这三类
# 引用**能解析到真实文件**：`docs/agent-handoff.md` 引 `nginx-hutuji.conf` 的行号、
# `docs/protocol.md` 引 `Grbl_Esp32.ino` 的行号，两条都是真活指针，却因不匹配本
# 正则而从未进入三层校验中的任何一层（2026-08-11 实测）。
# **刻意不收 `txt`/`log`**：本仓这两类命中全部落在 gitignore 的 `results/`，纳入会让
# 门禁结论随机器上有没有残留产物而变。
_DOC_REF_RE = re.compile(
    r"([A-Za-z0-9_./\\-]+\.(?:py|toml|c|cc|cpp|h|hpp|sh|json|mdc|md|conf|service|ino)):(\d+)(?:-(\d+))?"
)

# 零宽/不可见字符（零宽空格、ZWNJ/ZWJ、词连接符、BOM、软连字符）。Markdown 渲染
# 不可见，却能切断三层正则的匹配面：禁语「可出货」写成「可\u200b出货」逃过
# forbidden_claims，`x.cc:2\u200b35` 逃过行号引用三层（R13-SIM-01）。扫描前统一
# 剥掉——剥了行号不变（零宽字符不是换行），且剥离方向是 fail-closed（藏了零宽的
# 引用反而变得可解析、可校验）。
_INVISIBLE_RE = re.compile("[\u200b\u200c\u200d\u2060\ufeff\u00ad]")

def _strip_invisible(text: str) -> str:
    return _INVISIBLE_RE.sub("", text)


# 全角冒号的 path：line 引用（R13-SIM-02）：_DOC_REF_RE 只认 ASCII ':'，全角形态
# 不进任何一层（越界/外仓/指纹全跳过，基线也无 key），过期行号借此永久假绿。
# 这类形态没有合法用途（行号引用约定就是 ASCII），见到即判红。
_DOC_REF_FULLWIDTH_RE = re.compile(
    r"([A-Za-z0-9_./\\-]+\.(?:py|toml|c|cc|cpp|h|hpp|sh|json|mdc|md|conf|service|ino))：(\d+)"
)

# basename 兜底解析时跳过的目录：虚拟环境 / 缓存 / 产物 / 代理临时 worktree
# （`.claude/worktrees/*` 是整仓副本，会让每个 basename 都有多个命中）。既避免误判
# 到第三方同名文件，也避免同名把「唯一命中」打破、导致本该校验的引用被白白跳过。
_REF_SKIP_DIRS = {
    ".venv",
    ".git",
    ".claude",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    "output",
    "results",
}


def _doc_scan_files() -> list[Path]:
    """行号三层（越界 / 外仓 / 指纹）共用的扫描源集合，是「谁写的 path:line 会被校验」的唯一真值。

    抽成一处的理由同 required_file_set()：此前三层各自内联同一份 glob，新增一类
    文档要改三处，漏改一处就是那一层静默失去覆盖。
    纳入 `.cursor/rules/*.mdc` 与 `firmware/*.md`：两者都在
    docs/agent-edit-allowlist.json 的 hub_safe_doc_globs 内（agent 可改），`.mdc`
    更已在 _DOC_REF_RE 的扩展名白名单里（说明本就预期它参与行号引用），却都不在
    扫描源中——在其中写过期行号，三层一条都查不出来。2026-08-11 实测：
    GOVERNANCE_DOCS 18 份里 `.cursor/rules/hutuji-agent-rails.mdc` 是唯一漏网者。
    """
    candidates = {
        *ROOT.glob("*.md"),
        *ROOT.glob("docs/**/*.md"),
        *ROOT.glob("*/README.md"),
        *ROOT.glob(".cursor/rules/*.mdc"),
        *ROOT.glob("firmware/*.md"),
    }
    return sorted(
        path
        for path in candidates
        if not any(part in _REF_SKIP_DIRS for part in path.relative_to(ROOT).parts)
    )


def _normalize_doc_ref(raw: str) -> str:
    """把引用原文规范化成仓相对路径写法。

    必须用 removeprefix 而非 lstrip("./")：lstrip 按字符集剥离，会把
    `.cursor/rules/x.mdc` 的前导点一起吃掉、变成 `cursor/rules/x.mdc`，于是该引用
    永远解析不到，被计进 skipped 而不是判红——过期行号照样全绿（2026-08-11 实测）。
    removeprefix 只剥真正的 "./" 前缀，`./docs/x.md` 的结果与旧行为一致。
    """
    return raw.replace("\\", "/").removeprefix("./")


# full profile 额外解析的外仓/参考仓。本仓文档里 114 处 `path:line` 引用有 103 处是
# 裸文件名且指向这三处（`hutuji_pipe.cc` 11 次、`Serial.cpp`/`Protocol.cpp` 各 9 次…），
# 只在本仓解析等于放着最大的漂移面不管。路径优先取环境变量，缺省用 AGENTS.md 记载的
# 本机布局；目录不存在就整仓跳过——换机器不该因为别人没有这些 fork 而变红。
# 每项为 (环境变量, 默认路径, 只扫这些子目录)。
# inkscape_px 里另有一份旧 `Grbl_Esp32/` 源码副本，纳进索引会让本仓文档里几乎所有
# Grbl 源码引用（`Serial.cpp`/`Protocol.cpp`/`Report.cpp`…）都变成同名两份而被跳过，
# 整层退化成假绿，所以只索引 `inkscape-clean/`；Grbl 侧补 `doc/`（`stream.py`
# 与 `simple_stream.py` 在 `doc/script/` 下，不在 `Grbl_Esp32/` 子树里）。
_EXTERNAL_REPOS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("GRBL_ROOT", "D:/Users/Grbl_Esp32", ("Grbl_Esp32", "doc")),
    ("XIAOZHI_ROOT", "D:/Users/xiaozhi-esp32", ("main",)),
    ("INKSCAPE_PX_ROOT", "D:/GitHub/inkscape_px", ("inkscape-clean",)),
)

# 外仓里必须跳过的目录：构建产物与依赖副本，以及 inkscape_px 的 release*/ 打包副本
# （同一份 grbl_plotter.py 在 release 下有 8 份，不排掉 basename 永远不唯一，
# 整层就会退化成全跳过的假绿）。
_EXT_SKIP_DIRS = {
    ".git",
    ".pio",
    ".venv",
    "build",
    "dist",
    "managed_components",
    "node_modules",
    "__pycache__",
}
_EXT_SKIP_PREFIXES = ("release",)


def _repo_file_index() -> dict[str, list[Path]]:
    """本仓文件名 → 路径列表，供文档引用的 basename 兜底解析。"""
    index: dict[str, list[Path]] = {}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in _REF_SKIP_DIRS]
        for name in filenames:
            index.setdefault(name, []).append(Path(dirpath) / name)
    return index


def _repo_file(ref: str) -> Path | None:
    """把引用当仓相对路径落盘，且**必须真的落在本仓内**；否则返回 None。

    Windows 上 `ROOT / ref` 会被绝对路径逃逸：文档里写 `D:/Users/xiaozhi-esp32/…
    /hutuji_pipe.cc:45`（docs/kxnx/findings/21-transport-watchdog-and-sd-executor.md
    就有一处），_DOC_REF_RE 的字符集不含 `:`，捕获到的是去掉盘符的
    `/Users/xiaozhi-esp32/…`，而 pathlib 对「驱动器相对绝对路径」的语义是保留左侧
    盘符、替换整个路径 —— `Path("D:/Users/hutuji") / "/Users/xiaozhi-esp32/x.cc"`
    等于 `D:/Users/xiaozhi-esp32/x.cc`，该文件真实存在故 is_file() 为真，
    _resolve_doc_ref 就把一个仓外路径当成本仓命中返回，随后
    `target.relative_to(ROOT)` 抛 ValueError 把整个门禁打死（不是判红，是没有结论）。
    旧代码的 `lstrip("./")` 顺手剥掉了前导 `/` 才一直没暴露——是巧合，不是防线。
    """
    try:
        root = ROOT.resolve(strict=True)
        candidate = (root / ref).resolve(strict=True)
        candidate.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return None
    return candidate if candidate.is_file() else None


def _narrow_by_path_suffix(ref: str, cands: list[Path]) -> list[Path]:
    """带目录的引用要求候选路径以该相对路径结尾，避免同名文件误配。

    `"/" + ref` 会对**根相对**引用拼出双斜杠：文档写 `D:/Users/xiaozhi-esp32/…
    /hutuji_pipe.cc:45` 时 _DOC_REF_RE 捕获的是去掉盘符的 `/Users/xiaozhi-esp32/…`，
    拼成 `//Users/…` 后没有任何路径以它结尾，该引用直接失去校验（本仓实测有 2 处
    这样的引用）。ref 已带前导 `/` 时不再补。
    """
    if "/" not in ref:
        return cands
    suffix = ref if ref.startswith("/") else "/" + ref
    return [p for p in cands if p.as_posix().endswith(suffix)]


def _resolve_doc_ref(ref: str, index: dict[str, list[Path]]) -> Path | None:
    """把文档里的引用落到本仓文件；无法唯一确定就返回 None（该条跳过）。

    文档习惯只写文件名（`gcode_gen.py:235`）或外仓相对路径
    （`Grbl/src/Report.cpp:605`）。前者要求在本仓内唯一命中才校验；后者本仓没有
    对应文件，自然跳过——本层管「引用行号过期」，不管「路径拼错」。
    """
    direct = _repo_file(ref)
    if direct is not None:
        return direct
    cands = index.get(ref.rsplit("/", 1)[-1], [])
    cands = _narrow_by_path_suffix(ref, cands)
    return cands[0] if len(cands) == 1 else None


def _doc_ref_range(m: re.Match[str]) -> tuple[int, int, str | None]:
    """引用的行号区间，附带区间本身的语义错误说明（None = 区间合法）。

    GATE-P2-03b：区间写法自己可以是错的（`:207-205` 倒序、`:0-3` 从零起），而
    `doc_line_refs` / `external_doc_line_refs` 此前只算 `max(start, end)` 再判越界——
    倒序区间的较大端在范围内即整条放行。指纹层却用 `start`/`end` 原值切片，
    `start > end` 得到空 body、`start < 1` 触发负索引，两者都进 `broken` 并判红。
    同一条引用在这两层绿、在指纹层红，读报告的人只看到「越界残留」却在越界层
    查不出任何越界。区间语义故须在这两层就判红，三层结论一致。
    """
    start = int(m.group(2))
    end = int(m.group(3) or m.group(2))
    if start < 1:
        return start, end, "行号从 0 起（行号自 1 计）"
    if start > end:
        return start, end, "区间倒序（起点大于终点）"
    return start, end, None


def check_doc_line_refs() -> dict[str, Any]:
    """校验文档里的 `path:line` 引用没有漂移（文件存在且行号在范围内）。

    代码搬家后行号是最容易悄悄失效的东西，而失效的证据引用等于没有证据。只校验
    能唯一落到本仓内的引用；fork 侧路径（`Grbl_Esp32/`、`xiaozhi-esp32/`）和逆向
    产物（`machine/grbl/*.java`）本仓无对应文件，一律跳过——本层管的是「引用行号
    过期」，不是「路径拼错」。
    """
    md_files = _doc_scan_files()
    index = _repo_file_index()
    bad: list[str] = []
    checked = 0
    skipped = 0
    for md in md_files:
        rel_md = md.relative_to(ROOT).as_posix()
        try:
            text = _strip_invisible(md.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            return _layer(
                "doc_line_refs",
                "文档 path:line 引用",
                "fail",
                f"无法读取治理文档 {rel_md}（{type(exc).__name__}）",
            )
        # R13-SIM-02：全角冒号形态不是合法引用写法，却长得足以以假乱真——
        # 它不进 _DOC_REF_RE，三层校验全跳过，见到即判红。
        for fw in _DOC_REF_FULLWIDTH_RE.finditer(text):
            bad.append(
                f"{rel_md} → {fw.group(1)}：{fw.group(2)}"
                "（全角冒号不会被解析为行号引用，请改用 ASCII ':'）"
            )
        for m in _DOC_REF_RE.finditer(text):
            ref = _normalize_doc_ref(m.group(1))
            target = _resolve_doc_ref(ref, index)
            if target is None:
                skipped += 1
                continue
            start, end, range_err = _doc_ref_range(m)
            checked += 1
            if range_err is not None:
                bad.append(f"{rel_md} → {ref}:{start}-{end}（{range_err}）")
                continue
            # 区间合法后只校验较大端点：末行在范围内则整段在范围内
            lineno = end
            try:
                total = len(target.read_text(encoding="utf-8", errors="replace").splitlines())
            except OSError as exc:
                rel_target = target.relative_to(ROOT).as_posix()
                return _layer(
                    "doc_line_refs",
                    "文档 path:line 引用",
                    "fail",
                    f"无法读取引用目标 {rel_target}（{type(exc).__name__}）",
                )
            # start < 1 已由 _doc_ref_range 判掉，故此处只剩「末行超出文件长度」
            if lineno > total:
                rel_target = target.relative_to(ROOT).as_posix()
                bad.append(f"{rel_md} → {ref}:{lineno}（{rel_target} 共 {total} 行）")
    if bad:
        detail = f"{len(bad)}/{checked} 处行号引用不合法（越界/区间语义错/全角冒号）：" + "; ".join(bad[:10])
        return _layer("doc_line_refs", "文档 path:line 引用", "fail", detail)
    if checked == 0:
        # 一条都解析不到就是空转门禁；宁可红也不要假绿
        return _layer(
            "doc_line_refs",
            "文档 path:line 引用",
            "fail",
            f"本仓内一条引用都没解析到（跳过 {skipped} 处）——解析规则或文档约定已变，先修门禁",
        )
    return _layer(
        "doc_line_refs",
        "文档 path:line 引用",
        "pass",
        f"校验 {checked} 处行号均在范围内；跳过 {skipped} 处（外仓/无法唯一定位）",
    )


def _external_index() -> tuple[dict[str, list[Path]], list[str], list[str]]:
    """外仓文件名 → 路径列表，附带已纳入 / 未找到的仓标签。

    索引三个仓合并成一张表：跨仓同名（如 inkscape_px 里也有一份 `Grbl_Esp32/`）时
    命中不唯一，自动落到「跳过」——无法判断文档指哪一份时宁可漏判，不可误判。
    """
    index: dict[str, list[Path]] = {}
    present: list[str] = []
    absent: list[str] = []
    for env, default, subdirs in _EXTERNAL_REPOS:
        root = Path(os.environ.get(env) or default)
        if not root.is_dir():
            absent.append(f"{env}={root}")
            continue
        present.append(f"{env}={root}")
        for sub in subdirs:
            base = root / sub
            if not base.is_dir():
                continue
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = [
                    d
                    for d in dirnames
                    if d not in _EXT_SKIP_DIRS and not d.startswith(_EXT_SKIP_PREFIXES)
                ]
                for name in filenames:
                    index.setdefault(name, []).append(Path(dirpath) / name)
    return index, present, absent


def check_external_doc_line_refs(*, require_repos: bool = True) -> dict[str, Any]:
    """把文档引用的 fork / 参考仓行号也校验一遍。

    doc_line_refs 只认本仓文件，外仓引用一律跳过（211 处里有 164 处）。行号漂掉的
    证据引用等于没有证据，故 hub 与 full 两层都要把这批校验掉；命中不唯一仍跳过，
    见 _external_index。

    require_repos 区分两层对「外仓不存在」的态度：full 本来就要求 GRBL_ROOT（改
    Grbl 才跑），缺仓即 fail；hub 是日常层，换到没有 fork 的机器不该因此变红，缺仓
    时如实报「无可校验」，而不是假装通过了一次校验。
    """
    label = "文档 path:line 外仓引用"
    index, present, absent = _external_index()
    if not present:
        detail = "三个外仓路径都不存在（" + "; ".join(absent) + "）；设 GRBL_ROOT/XIAOZHI_ROOT/INKSCAPE_PX_ROOT"
        if require_repos:
            return _layer("external_doc_line_refs", label, "fail", detail)
        return _layer(
            "external_doc_line_refs",
            label,
            "pass",
            detail + "；本层无可校验（改 Grbl 前须跑 full）",
        )
    md_files = _doc_scan_files()
    bad: list[str] = []
    checked = 0
    skipped = 0
    for md in md_files:
        rel_md = md.relative_to(ROOT).as_posix()
        try:
            text = _strip_invisible(md.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            return _layer(
                "external_doc_line_refs",
                label,
                "fail",
                f"无法读取治理文档 {rel_md}（{type(exc).__name__}）",
            )
        for m in _DOC_REF_RE.finditer(text):
            ref = _normalize_doc_ref(m.group(1))
            # 用 _repo_file 而非 `(ROOT / ref).is_file()`：后者会被驱动器相对绝对
            # 路径逃逸（见 _repo_file 注释），把仓外文件误判成「本仓的，归上一层管」，
            # 于是两层都不校验它——本该判红的外仓过期行号静默消失。
            if _repo_file(ref) is not None:
                continue  # 本仓的归 doc_line_refs 层管
            cands = index.get(ref.rsplit("/", 1)[-1], [])
            cands = _narrow_by_path_suffix(ref, cands)
            if len(cands) != 1:
                skipped += 1
                continue
            target = cands[0]
            # 区间语义与 doc_line_refs 同一判据（GATE-P2-03b）：倒序 / 从零起在本层
            # 也必须红，否则同一条引用本层绿、指纹层红，报告自相矛盾。
            start, end, range_err = _doc_ref_range(m)
            checked += 1
            if range_err is not None:
                bad.append(f"{rel_md} → {ref}:{start}-{end}（{range_err}）")
                continue
            lineno = end
            try:
                total = len(target.read_text(encoding="utf-8", errors="replace").splitlines())
            except OSError as exc:
                return _layer(
                    "external_doc_line_refs",
                    label,
                    "fail",
                    f"无法读取外仓引用目标 {ref}（{type(exc).__name__}）",
                )
            if lineno > total:
                bad.append(f"{rel_md} → {ref}:{lineno}（实际 {total} 行）")
    if bad:
        tail = "；fork 工作树有未提交改动时行号会漂，先分清是文档过期还是源码已变"
        detail = f"{len(bad)}/{checked} 处行号引用不合法（越界或区间语义错）：" + "; ".join(bad[:8]) + tail
        return _layer("external_doc_line_refs", label, "fail", detail)
    if checked == 0:
        detail = f"一条外仓引用都没解析到（跳过 {skipped} 处）——子目录白名单或文档约定已变，先修门禁"
        return _layer("external_doc_line_refs", label, "fail", detail)
    note = f"；未找到 {', '.join(absent)}" if absent else ""
    detail = f"校验 {checked} 处行号均在范围内；跳过 {skipped} 处（同名多份/无法定位）{note}"
    return _layer("external_doc_line_refs", label, "pass", detail)




# ---------------------------------------------------------------------------
# 行号内容指纹（半漂移防线）
# ---------------------------------------------------------------------------
# doc_line_refs / external_doc_line_refs 只查「行号越界」，查不出行号在界内但
# 指向内容已变的半漂移。社区共识（mdBook 官方文档与 issue #811）是行号引用
# 无法根治此问题，正解是锚点/符号引用；在迁移锚点之前，先用「行内容指纹 +
# 显式登记基线」把半漂移变成可判红事件。


def _line_ref_baseline_path() -> Path:
    return ROOT / LINE_REF_BASELINE_REL


def _sha8(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8", "replace")).hexdigest()[:8]


def _is_external_target(target: str) -> bool:
    """基线里登记的 target 是外仓（绝对路径）还是本仓（仓相对路径）。

    `_iter_doc_line_refs` 对本仓命中写 `relative_to(ROOT)`（`scripts/agent_gate.py`），
    对外仓命中写绝对路径（`D:/Users/Grbl_Esp32/…`）。故前导 `/` 或 Windows 盘符
    就是「外仓」的判据。GATE-P2-06 靠它把「fork 缺席（容忍）」与「本仓文件被删
    或改名（判红）」分开。
    """
    return target.startswith("/") or re.match(r"^[A-Za-z]:[/\\]", target) is not None


class DocRefScan(NamedTuple):
    """一次文档引用扫描的分类结果。

    - current: 解析成功、行号在界内、内容非空 → 可算指纹
    - blank:   解析成功、行号在界内，但目标行全为空白 → 指纹恒为空串哈希，
               等于「指向了什么都没有」。必须判红，不得登记（2026-08-11 review AG-F2）
    - broken:  解析成功但行号越界，或区间语义本身就错（倒序/从零起）
               → accept 不得保留其旧指纹（AGR-003 / GATE-P2-03b）
    - unresolved: 落不到任何仓的引用（key → ref 原文）。此前只是 `continue`，
               于是「本仓文件被删/改名」与「外仓整个缺席」在三层里都只表现为
               计数变小，没有任何一层会红（GATE-P2-06）。判红要靠基线区分这两者，
               故本层只负责把它们收集出来。
    - seen:    文档里出现过的全部 key（含解析不到的）
    """

    current: dict[str, dict[str, str]]
    blank: dict[str, str]
    broken: dict[str, str]
    unresolved: dict[str, str]
    seen: set[str]
    error: str | None


def _iter_doc_line_refs() -> DocRefScan:
    """枚举文档里的 `path:line` 引用并算目标行内容指纹。

    key = "<doc 相对路径> -> <ref 原文（规范化）>"；同一文档同一引用出现多次
    只算一条——指纹校验的是「这句话指向的内容」，与出现次数无关。

    `seen` 要含解析不到的 key：判「陈旧」用它，否则外仓缺席时其基线条目会被
    误判成「文档已删除该引用」。
    """
    md_files = _doc_scan_files()
    internal_index = _repo_file_index()
    external_index, _present, _absent = _external_index()
    lines_cache: dict[Path, list[str]] = {}
    current: dict[str, dict[str, str]] = {}
    blank: dict[str, str] = {}
    broken: dict[str, str] = {}
    unresolved: dict[str, str] = {}
    seen: set[str] = set()
    for md in md_files:
        rel_md = md.relative_to(ROOT).as_posix()
        try:
            text = _strip_invisible(md.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            return DocRefScan(
                {}, {}, {}, {}, set(), f"无法读取治理文档 {rel_md}（{type(exc).__name__}）"
            )
        for m in _DOC_REF_RE.finditer(text):
            ref = _normalize_doc_ref(m.group(1))
            start, end, range_err = _doc_ref_range(m)
            key = f"{rel_md} -> {ref}:{m.group(2)}{'-' + m.group(3) if m.group(3) else ''}"
            seen.add(key)
            if key in current or key in blank or key in broken or key in unresolved:
                continue
            target = _resolve_doc_ref(ref, internal_index)
            rel_target: str | None = None
            if target is not None:
                rel_target = target.relative_to(ROOT).as_posix()
            else:
                cands = external_index.get(ref.rsplit("/", 1)[-1], [])
                cands = _narrow_by_path_suffix(ref, cands)
                if len(cands) == 1:
                    target = cands[0]
                    rel_target = target.as_posix()
            if target is None or rel_target is None:
                unresolved[key] = ref
                continue
            if target not in lines_cache:
                try:
                    lines_cache[target] = target.read_text(
                        encoding="utf-8", errors="replace"
                    ).splitlines()
                except OSError as exc:
                    return DocRefScan(
                        {}, {}, {}, {}, set(),
                        f"无法读取引用目标 {rel_target}（{type(exc).__name__}）",
                    )
            lines = lines_cache[target]
            # 行号越界与区间语义错由 doc_line_refs / external_doc_line_refs 两层判红；
            # 本层算不出指纹，但要记进 broken——accept 不得为它保留旧指纹。
            if range_err is not None or end > len(lines):
                broken[key] = rel_target
                continue
            body = lines[start - 1 : end]
            # 目标行全空白 = 这条引用指向「什么都没有」。空串的 sha256 前 8 位恒为
            # e3b0c442，于是它永远与基线相符、永远不报漂移，却被计进「指纹 N 处全对」。
            # 实测本仓有 5 条这样的锚点，全部是行号已漂 1–2 行的陈旧指针（内容就在
            # 邻行），指纹层却把它们当成正确证据（2026-08-11 review AG-F2）。
            if not any(line.strip() for line in body):
                blank[key] = rel_target
                continue
            current[key] = {"target": rel_target, "sha8": _sha8(body)}
    return DocRefScan(current, blank, broken, unresolved, seen, None)


def check_line_ref_fingerprints() -> dict[str, Any]:
    """比对文档引用的行内容指纹与基线，抓「行号在界内但内容已变」的半漂移。

    六类问题全判红：未登记（新增引用没跑 --accept-line-refs）、半漂移（指纹
    不符）、陈旧（基线条目在文档里已不存在）、空锚点（引用指向空白行）、
    越界残留（基线仍留着行号已越界的条目）、目标消失（基线登记过仓相对
    路径而现在解析不到 —— 本仓文件被删或改名，GATE-P2-06）。基线只能由
    --accept-line-refs 显式重登，重新登记前必须人工核对被引用行仍是当初
    证据所指——否则本层退化成自动追认。"""
    label = "行号内容指纹"
    scan = _iter_doc_line_refs()
    if scan.error:
        return _layer("line_ref_fingerprints", label, "fail", scan.error)
    current, blank, broken, seen = scan.current, scan.blank, scan.broken, scan.seen
    unresolved = scan.unresolved
    baseline_path = _line_ref_baseline_path()
    if not baseline_path.is_file():
        return _layer(
            "line_ref_fingerprints",
            label,
            "fail",
            f"基线 {LINE_REF_BASELINE_REL} 不存在；先跑 python scripts/agent_gate.py --accept-line-refs 登记",
        )
    try:
        data = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _layer(
            "line_ref_fingerprints",
            label,
            "fail",
            f"基线 {LINE_REF_BASELINE_REL} 不可读/已损坏（{type(exc).__name__}）",
        )
    baseline: dict[str, Any] = data.get("refs", {}) if isinstance(data, dict) else {}
    unregistered = sorted(k for k in current if k not in baseline)
    drifted = sorted(
        k for k in current if k in baseline and baseline[k].get("sha8") != current[k]["sha8"]
    )
    stale = sorted(k for k in baseline if k not in seen)
    # 空锚点判红，不看基线：指向空白行的引用本身就不是证据。它的指纹恒等于空串
    # 哈希，登记后永远相符，于是「行号漂了 1 行、正文在邻行」这种最常见的漂移被
    # 当成正确证据计进「全对」。
    blank_hits = sorted(blank)
    # 基线里仍留着越界条目 = 上一次 accept 把坏引用洗白了（AGR-003）。
    broken_kept = sorted(k for k in broken if k in baseline)
    # GATE-P2-06：本仓文件被删/改名后，指向它的引用在三层里全部只表现为「计数变小」
    # ——doc_line_refs 记 skipped、外仓层记 skipped、本层把它算进 kept。没有任何一层
    # 会红，于是一条曾经真实校验过的证据引用可以静默失去全部校验。
    #
    # 基线是唯一能区分「本来就在外仓（fork 可能整个缺席，须容忍）」与「本仓文件没了
    # （只有删/改名一种解释，须判红）」的凭据：_iter_doc_line_refs 对本仓命中登记
    # 仓相对路径、对外仓命中登记绝对路径，故按基线里 target 的形态分流。
    # 未登记过的新引用不在此列——那是「新写的引用还解析不到」，与本条缺陷无关。
    vanished = sorted(
        k
        for k in unresolved
        if isinstance(baseline.get(k), dict)
        and not _is_external_target(str(baseline[k].get("target", "")))
    )
    if unregistered or drifted or stale or blank_hits or broken_kept or vanished:
        parts: list[str] = []
        if drifted:
            parts.append(f"半漂移 {len(drifted)} 处：" + "; ".join(drifted[:6]))
        if unregistered:
            parts.append(f"未登记 {len(unregistered)} 处：" + "; ".join(unregistered[:6]))
        if stale:
            parts.append(f"陈旧 {len(stale)} 处：" + "; ".join(stale[:6]))
        if blank_hits:
            parts.append(
                f"空锚点 {len(blank_hits)} 处（引用指向空白行，须改指真正的证据行）："
                + "; ".join(blank_hits[:6])
            )
        if broken_kept:
            parts.append(
                f"越界残留 {len(broken_kept)} 处（基线仍登记着行号已越界的引用）："
                + "; ".join(broken_kept[:6])
            )
        if vanished:
            parts.append(
                f"目标消失 {len(vanished)} 处（基线登记的是本仓相对路径，现已解析不到——"
                f"文件被删或改名，该引用已静默失去三层校验）：" + "; ".join(vanished[:6])
            )
        parts.append("人工核对被引用行后跑 python scripts/agent_gate.py --accept-line-refs 重登基线")
        return _layer("line_ref_fingerprints", label, "fail", "；".join(parts))
    kept = sum(1 for k in baseline if k in seen and k not in current)
    # 基线的 git_head 一直在写却从没被读过（2026-08-11 review AGR-001）。它是
    # 「这批指纹是在哪个 HEAD 上人工核对的」的唯一凭据；不打出来，读报告的人无法
    # 判断基线是本轮核对的还是几十个提交前的。不判红——HEAD 每次提交都变，判红
    # 等于要求每次提交重登基线，那才是真的自动追认。
    baseline_head = data.get("git_head") if isinstance(data, dict) else None
    head_now = _git_head()
    if baseline_head and head_now and baseline_head != head_now:
        vintage = f"基线登记于 `{baseline_head[:8]}`（当前 HEAD `{head_now[:8]}`，已前进）"
    elif baseline_head and head_now:
        vintage = f"基线登记于当前 HEAD `{baseline_head[:8]}`"
    else:
        vintage = "基线未记 HEAD"
    return _layer(
        "line_ref_fingerprints",
        label,
        "pass",
        f"指纹 {len(current)} 处全对；外仓缺席等未解析条目保留 {kept} 处；{vintage}",
    )


def accept_line_refs() -> int:
    """把当前文档引用的行内容指纹重登为基线（--accept-line-refs）。

    当前解析不到的基线条目（外仓缺席等）只要文档仍引用就原样保留，
    避免在缺 fork 的机器上跑一次 accept 把外仓登记冲掉、回到有 fork
    的机器整层假绿。

    但**空锚点与越界引用一律拒绝登记**（2026-08-11 review AG-F2/AGR-003）：
    这两类引用指不到任何证据，登记它们等于用 accept 把坏引用洗白成永久绿。
    先把文档里的行号改对，再登记。
    """
    scan = _iter_doc_line_refs()
    if scan.error:
        print(f"accept-line-refs 中止：{scan.error}")
        return 1
    bad: list[str] = []
    for key, target in sorted(scan.blank.items()):
        bad.append(f"  空锚点（指向空白行）：{key}  → {target}")
    for key, target in sorted(scan.broken.items()):
        bad.append(f"  越界（行号超出文件长度）：{key}  → {target}")
    if bad:
        print("accept-line-refs 中止：以下引用指不到证据，登记它们等于把坏引用洗白成永久绿。")
        print("先把文档里的行号改成真正的证据行，再重跑本命令。")
        for line in bad:
            print(line)
        return 1
    current, seen = scan.current, scan.seen
    baseline_path = _line_ref_baseline_path()
    kept: dict[str, Any] = {}
    old_refs: dict[str, Any] = {}
    if baseline_path.is_file():
        try:
            old = json.loads(baseline_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            old = {}
        old_refs = old.get("refs", {}) if isinstance(old, dict) else {}
        for key, value in old_refs.items():
            if key in seen and key not in current:
                kept[key] = value
    # R13-SIM-03：accept 会把漂移指纹静默洗成新基线——落盘前必须把「这次到底
    # 改了多少条」摆到台面上，逐条人工核对的责任才有落点（AGENTS.md：新增或
    # 改动 path:line 引用后必须先人工核对再登记，无脑重登等于退化成自动追认）。
    added = sorted(k for k in current if k not in old_refs)
    changed = sorted(
        k for k in current
        if k in old_refs and old_refs[k].get("sha8") != current[k]["sha8"]
    )
    if added or changed:
        print(
            f"重登明细：新增 {len(added)} 处、指纹变更 {len(changed)} 处"
            "——应全部对应本次有意编辑，否则立即 git checkout 基线并排查"
        )
        for key in (added + changed)[:20]:
            tag = "新增" if key in added else "变更"
            print(f"  [{tag}] {key}")
        if len(added) + len(changed) > 20:
            print(f"  … 其余 {len(added) + len(changed) - 20} 处从略")
    refs = {**kept, **current}  # 同 key 以当前指纹为准
    payload = {
        "version": 1,
        "updated": _now(),
        "git_head": _git_head(),
        "refs": refs,
    }
    _atomic_write(
        baseline_path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    print(
        f"已登记 {len(current)} 处指纹，保留未解析条目 {len(kept)} 处 → {baseline_path}"
    )
    return 0


def _mcp_python() -> Path | None:
    win = ROOT / "mcp-server" / ".venv" / "Scripts" / "python.exe"
    unix = ROOT / "mcp-server" / ".venv" / "bin" / "python"
    if win.is_file():
        return win
    if unix.is_file():
        return unix
    return None


def _pytest_tmp() -> Path:
    """为本次 pytest 新建一个独占的 basetemp 目录。

    放在仓库内 results/.tmp/ 下，避开系统 %TEMP% 的权限问题。每次调用都用
    mkdtemp 拿一个全新目录：pytest 启动时会 rmtree 掉 basetemp，若沿用固定
    名字而该目录被其它账号（如沙箱进程）持有，删除会 PermissionError，导致
    所有用 tmp fixture 的用例在 setup 阶段假失败。目录由 run_pytest 收尾删除。
    """
    base = RESULTS / ".tmp"
    try:
        base.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix="pytest-", dir=base))
    except OSError as exc:
        raise RuntimeError(f"无法在 {base} 创建 pytest basetemp: {exc}") from exc


def run_pytest() -> dict[str, Any]:
    py = _mcp_python()
    if py is None:
        return _layer("pytest", "mcp-server pytest", "fail", "缺少 mcp-server/.venv，请先建 venv")
    try:
        pytest_tmp = _pytest_tmp()
    except RuntimeError as exc:
        return _layer("pytest", "mcp-server pytest", "fail", str(exc))
    env = os.environ.copy()
    env.update(
        {
            "TMP": str(pytest_tmp),
            "TEMP": str(pytest_tmp),
            "TMPDIR": str(pytest_tmp),
            # Windows 下子进程 stdout 默认按 locale（GBK）编码，门禁按 utf-8 解码
            # 会得到 \ufffd，打印到终端再触发 UnicodeEncodeError；强制 utf-8。
            "PYTHONIOENCODING": "utf-8",
        }
    )
    try:
        proc = subprocess.run(
            [str(py), "-m", "pytest", "-q", "-p", "no:cacheprovider", "--basetemp", str(pytest_tmp)],
            cwd=ROOT / "mcp-server",
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            # 门禁必须有界：无 timeout 时一条挂死的用例会让整个门禁永久卡住，
            # 结果是「没有结论」而不是 fail——无人值守下最糟的一种失败。
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        return _layer("pytest", "mcp-server pytest", "fail", "pytest 超过 600s 未返回（疑似挂死）")
    except OSError as exc:
        # GATE-P2-04：subprocess.run 除 TimeoutExpired 还会抛 OSError——解释器被删、
        # venv 换了架构、权限拒绝、ENOEXEC 都走这条。此前只捕 Timeout，异常会一路
        # 穿过 main() 的层循环（那里没有 try），于是报告两个文件都不落盘、tombstone
        # 留在盘上、终端只剩 traceback。结果是门禁「没有结论」而不是判红，正是本仓
        # 反复禁止的形态：读报告的人看到的是上一轮的旧报告。
        return _layer(
            "pytest", "mcp-server pytest", "fail",
            f"pytest 无法启动（{type(exc).__name__}: {exc}）",
        )
    finally:
        shutil.rmtree(pytest_tmp, ignore_errors=True)
    tail = (proc.stdout + proc.stderr).strip().splitlines()
    detail = tail[-1] if tail else f"exit={proc.returncode}"
    status = "pass" if proc.returncode == 0 else "fail"
    return _layer("pytest", "mcp-server pytest", status, detail, exit_code=proc.returncode)


def _kill_process_tree_on_close(child: "subprocess.Popen[str]") -> None:
    """Windows：把 e2e_local 挂进 kill-on-close job object，超时整树回收。

    e2e_local.py 会 spawn grbl_sim 孙进程；communicate 超时只杀直接子进程
    （TerminateProcess），grbl_sim 会成孤儿永占 2323，之后每次门禁 e2e 层都因
    「端口已被占用」连环 FAIL（R11-E2E-02）。与 sim_server.py:85-185 同款范式
    （门禁脚本不得 import mcp-server 包，故自带最小实现）：job 设
    KILL_ON_JOB_CLOSE 后句柄全关时其内全部进程被 OS 终止。POSIX 非本场景目标
    （走 start_new_session + killpg，见 _terminate_whole_tree），直接 no-op。
    句柄存到 child 上阻止 GC 提前关闭。"""
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    # 用与 Win32 头文件一致的嵌套布局，避免手算字节偏移（此前 c_byte 数组版本
    # 因 sizeof 与系统期望不符而 SetInformationJobObject 返回 ERROR_BAD_LENGTH）。
    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(f, ctypes.c_uint64) for f in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    JobObjectExtendedLimitInformation = 9

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # 必须显式声明：默认 restype 是 c_int(32 位)，返回 HANDLE 的函数在 64 位下
    # 有截断风险；实测当前句柄值小（0x1f8）未触发，但不可依赖。
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD
    ]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = kernel32.SetInformationJobObject(
        job,
        JobObjectExtendedLimitInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    if not ok or not kernel32.AssignProcessToJobObject(job, int(child._handle)):
        kernel32.CloseHandle(wintypes.HANDLE(job))
        return
    # 句柄由 `_release_job` 在 run_e2e 的 finally 里确定性关闭——不依赖 GC，
    # 否则 KILL_ON_JOB_CLOSE 的触发时机不可知。
    child._hutuji_job = job


def _release_job(child: "subprocess.Popen[str]") -> None:
    """关闭 job 句柄。这是 KILL_ON_JOB_CLOSE 真正生效的时刻：句柄全关时 OS 终止
    job 内残留进程，故正常路径也必须调，既防句柄泄漏又兜底回收漏杀的孙进程。"""
    job = getattr(child, "_hutuji_job", None)
    if not job:
        return
    import ctypes
    from ctypes import wintypes

    ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(wintypes.HANDLE(job))
    child._hutuji_job = None


def _killpg(pid: int) -> None:
    """POSIX：杀整个进程组。start_new_session 让 e2e_local 成为新会话组长，
    grbl_sim 同组，组杀一并回收（R11-E2E-02）。"""
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except OSError:
        pass  # 进程组已退出（ProcessLookupError 等），无需再杀


def _terminate_whole_tree(child: "subprocess.Popen[str]") -> None:
    """终止子进程及其全部后代。优先 TerminateJobObject 杀 job 内整树（含 grbl_sim
    孙进程）；无 job（POSIX 或挂接失败）时 Windows 退回 child.kill() 只杀直接
    子进程、POSIX 走 killpg。"""
    job = getattr(child, "_hutuji_job", None)
    if not job:
        if os.name == "nt":
            child.kill()
        else:
            _killpg(child.pid)
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateJobObject(wintypes.HANDLE(job), 1)
    _release_job(child)


def run_e2e() -> dict[str, Any]:
    py = _mcp_python()
    if py is None:
        return _layer("e2e_local", "e2e_local.py", "fail", "缺少 mcp-server/.venv")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"  # 同 run_pytest：防 GBK stdout 解出 \ufffd
    # 超时口径与 sim_server.LOCAL_E2E_TIMEOUT_SECONDS 统一为 300（R11-SIM-02）：
    # 同一 e2e_local 在两个入口被不同时限截断会分岔。门禁脚本不得 import
    # mcp-server 包，这里只能注释交叉引用——改一侧必须同步另一侧。
    try:
        child = subprocess.Popen(
            [str(py), "scripts/e2e_local.py"],
            cwd=ROOT / "mcp-server",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            # POSIX：e2e_local 成新会话组长、grbl_sim 同组，超时 killpg 可整树回收；
            # Windows 由 _kill_process_tree_on_close 的 job object 兜底（R11-E2E-02）。
            start_new_session=os.name != "nt",
        )
    except OSError as exc:
        # 同 run_pytest（GATE-P2-04）：启动失败必须是本层判红，不能穿透到 main()。
        return _layer(
            "e2e_local", "e2e_local.py", "fail",
            f"e2e_local 无法启动（{type(exc).__name__}: {exc}）",
        )
    _kill_process_tree_on_close(child)
    try:
        stdout, stderr = child.communicate(timeout=300)  # 见上注释：统一 300s
    except subprocess.TimeoutExpired:
        # 必须整树终止：communicate 超时只杀直接子进程，grbl_sim 孙进程会成孤儿
        # 永占 2323，之后每次门禁 e2e 层都因端口占用 FAIL（R11-E2E-02，已实证）。
        _terminate_whole_tree(child)
        try:
            child.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)
        return _layer("e2e_local", "e2e_local.py", "fail", "e2e_local 超过 300s 未返回（疑似挂死）")
    finally:
        _release_job(child)
    out = ((stdout or "") + (stderr or "")).strip()
    ok = child.returncode == 0 and re.search(r"\bPASS\b", out) is not None
    detail = out.splitlines()[-1] if out else f"exit={child.returncode}"
    return _layer("e2e_local", "e2e_local.py", "pass" if ok else "fail", detail, exit_code=child.returncode)


def check_section9(grbl_root: Path | None) -> dict[str, Any]:
    if grbl_root is None:
        # full profile 的定义就是 hub + §9 源码标记；GRBL_ROOT 未设时这一层
        # 一条证据也没有，只能判未通过，不能用 skip 让 full 假绿。
        return _layer(
            "section9_markers",
            "Grbl §9 源码标记",
            "fail",
            "未设置 GRBL_ROOT（full 必须给出 §9 证据）；改 Grbl 后设置并重跑 full，且另跑 fz 门禁",
        )
    if not grbl_root.is_dir():
        return _layer("section9_markers", "Grbl §9 源码标记", "fail", f"GRBL_ROOT 不存在: {grbl_root}")
    agents = grbl_root / "AGENTS.md"
    if not agents.is_file():
        return _layer(
            "section9_markers", "Grbl §9 源码标记", "fail",
            f"GRBL_ROOT 缺少 AGENTS.md，无法确认仓身份: {grbl_root}",
        )
    agents_text = agents.read_text(encoding="utf-8", errors="replace")
    if "Grbl_Esp32" not in agents_text or "protocol.md" not in agents_text or "§9" not in agents_text:
        return _layer(
            "section9_markers", "Grbl §9 源码标记", "fail",
            f"GRBL_ROOT/AGENTS.md 缺仓身份或 §9 契约锚点: {grbl_root}",
        )
    missing: list[str] = []
    for rel, needles, label in SECTION9_MARKERS:
        path = grbl_root / rel
        if not path.is_file():
            missing.append(f"{label}: missing file {rel}")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for needle in needles:
            if needle not in text:
                missing.append(f"{label}: missing `{needle}` in {rel}")
    if missing:
        return _layer("section9_markers", "Grbl §9 源码标记", "fail", "; ".join(missing))
    return _layer(
        "section9_markers",
        "Grbl §9 源码标记",
        "pass",
        "源码标记齐；仍须 fz agent_gate overall=pass 才能声称 §9 修好",
    )


def _section9_layer() -> dict[str, Any]:
    """GRBL_ROOT 在调用时读，不在建计划时读——测试可 monkeypatch 环境。"""
    grbl = os.environ.get("GRBL_ROOT")
    return check_section9(Path(grbl) if grbl else None)


def profile_layers(profile: str) -> list[tuple[str, Any]]:
    """profile → [(层 id, 零参可调用)]，是分层覆盖范围的唯一真值。

    抽成纯函数是为了能被测试钉死：此前 profile 分支只存在于 main() 里，
    要验证「hub 到底查了哪些层」就得真跑一遍门禁（会递归 spawn pytest），
    于是分层缩水没有任何自动化防线。docs/agent-gate.md 是文字定义，本函数是
    机器定义，两者须一致。
    """
    layers: list[tuple[str, Any]] = [
        ("required_files", check_required_files),
        ("decision_anchors", check_decision_anchors),
        ("revived_decisions", check_revived_decisions),
        ("forbidden_claims", check_forbidden_claims),
        ("edit_allowlist", check_edit_allowlist),
        ("anti_drift", check_anti_drift_doc),
        ("constraint_matrix", check_constraint_matrix),
        ("doc_line_refs", check_doc_line_refs),
        ("line_ref_fingerprints", check_line_ref_fingerprints),
    ]
    if profile in ("hub", "full"):
        # 外仓行号：hub 缺 fork 时如实报「无可校验」，full 缺 fork 即 fail。
        layers.append(
            (
                "external_doc_line_refs",
                functools.partial(
                    check_external_doc_line_refs,
                    require_repos=(profile == "full"),
                ),
            )
        )
        layers.append(("pytest", run_pytest))
        layers.append(("e2e_local", run_e2e))
    if profile == "full":
        layers.append(("section9_markers", _section9_layer))
    return layers


def _atomic_write(path: Path, text: str) -> None:
    """同目录临时文件 + os.replace 原子落盘，避免进程中断留下半截报告。

    门禁报告是后续 agent 的决策依据：损坏/错配的报告可能被误读为 pass，
    而 JSON 与 MD 分两次直写无法保证两文件一致。tmp 名带 pid + uuid：
    不同进程并发时 pid 已隔离；同进程内多线程/多任务并发时 pid 相同，
    uuid 保证互不覆盖，避免后写者 replace 走先写者的临时文件。"""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_reports(report: dict[str, Any]) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    _atomic_write(
        REPORT_JSON,
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
    )
    lines = [
        f"# hutuji gate — {report['overall_status']}",
        "",
        f"- profile: `{report['profile']}`",
        f"- time: {report['timestamp']}",
        f"- overall: **{report['overall_status']}** (exit={report['overall_exit']})",
        f"- HEAD: `{report['git_head'] or '未知（非 git 环境）'}`",
        "- 判读: 退出码是唯一真值；`.hutuji_gate_running` 在场 = 上次没跑完，本报告不可信",
    ]
    if report.get("stale_run_mark"):
        stale = report["stale_run_mark"]
        lines.append(
            f"- **上一轮门禁未跑完**（tombstone 在场，profile=`{stale.get('profile')}` "
            f"started={stale.get('started')} HEAD=`{stale.get('git_head')}`）；"
            "本报告是本轮重跑的结果，上一份报告不可信"
        )
    lines += [
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
    _atomic_write(REPORT_MD, "\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认 GBK：detail 里的 \ufffd（子进程解码兜底产物）或任何
    # 非 GBK 字符都会让 print 抛 UnicodeEncodeError，门禁整体崩在报告阶段。
    # 统一重配为 UTF-8（errors=replace 兜底），与报告文件口径一致。
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description="hutuji 枢纽仓 Agent 门禁")
    parser.add_argument(
        "--profile",
        choices=("docs", "hub", "full"),
        default="hub",
        help="docs=文档层; hub=文档+外仓行号+MCP(默认); full=hub+§9源码标记",
    )
    parser.add_argument(
        "--accept-line-refs",
        action="store_true",
        help="把当前文档 path:line 引用的行内容指纹重登为基线后退出（登记前必须人工核对被引用行）",
    )
    args = parser.parse_args(argv)
    if args.accept_line_refs:
        return accept_line_refs()

    plan = profile_layers(args.profile)
    stale_mark: dict[str, Any] | None = None
    if RUNNING_MARK.is_file():
        try:
            existing_mark = json.loads(RUNNING_MARK.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            existing_mark = {"unreadable": type(exc).__name__}
        if _pid_is_alive(existing_mark.get("pid")):
            print(
                f"FAIL：{RUNNING_MARK} 已由 pid={existing_mark.get('pid')} 的门禁占用；"
                "本轮未覆盖报告或 tombstone",
                file=sys.stderr,
            )
            return 1
        stale_mark = existing_mark
        RUNNING_MARK.unlink(missing_ok=True)
        print(f"注意：{RUNNING_MARK} 是陈旧崩溃标记；本次将覆盖并在报告中留痕")
    try:
        run_owner = _write_running_mark(args.profile, [layer_id for layer_id, _ in plan])
    except FileExistsError:
        print(f"FAIL：{RUNNING_MARK} 被并发门禁原子占有；本轮未覆盖报告", file=sys.stderr)
        return 1

    layers: list[dict[str, Any]] = []
    for layer_id, run_layer in plan:
        # TOOL-UX-02：每层开始打一行进度（hub/full 最长 15 分钟黑屏的 UX 修复）。
        # 只加 stdout 进度输出，不进报告 JSON/MD（write_reports 只从 layers 拼）——
        # 不改变任何判据与报告形状，报告格式钉死测试不受影响。
        print(f"[running] {layer_id}")
        try:
            layer = run_layer()
        except Exception as exc:  # noqa: BLE001 - 故意的兜底 fail-closed
            # R5-GATE-01：层内部抛出的任何未捕异常（治理文档 read_text 的 OSError、
            # 解析崩、空指针）必须塌成「对应层判红」，而不是穿透 main() 让解释器
            # 裸退。裸退的后果是「没有结论」：报告两个文件不落盘、tombstone 留盘
            # 误导「上次没跑完」、读报告的人看到的仍是上一轮旧绿。与 GATE-P2-04
            # 只补 subprocess OSError 同一类漏项，这里在层循环兜底覆盖全部层。
            # 判红即 overall=fail、exit 1，退出码是真值的承诺不变，仍 fail-closed。
            layer = _layer(
                layer_id,
                "层执行异常",
                "fail",
                f"层 {layer_id} 抛出未捕获异常（{type(exc).__name__}），已塌为判红",
            )
        required_keys = {"id", "name", "status", "detail"}
        if not isinstance(layer, dict) or not required_keys.issubset(layer):
            layer = _layer(
                layer_id,
                "层返回结构不合法",
                "fail",
                f"层 {layer_id} 返回结构不合法，已塌为判红",
            )
        elif layer["id"] != layer_id or layer["status"] not in {"pass", "fail"}:
            layer = _layer(
                layer_id,
                "层返回结构不合法",
                "fail",
                f"层 {layer_id} 返回 id/status 不合法，已塌为判红",
            )
        layers.append(layer)

    failed = [L for L in layers if L["status"] == "fail"]
    overall = "fail" if failed else "pass"
    exit_code = 1 if failed else 0

    report: dict[str, Any] = {
        "suite": "hutuji_agent_gate",
        "version": 1,
        "profile": args.profile,
        "timestamp": _now(),
        "root": str(ROOT),
        "git_head": _git_head(),
        "overall_status": overall,
        "overall_exit": exit_code,
        "layers": layers,
        "failures": failed,
        # 上一轮门禁是否死在半路（tombstone 在场）。None = 上轮干净收尾。
        "stale_run_mark": stale_mark,
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
    # 仅本轮 owner 可清自己的标记；并发失败者不得删除正在运行者的 tombstone。
    _release_running_mark(run_owner)

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
