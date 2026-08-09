#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hutuji 枢纽仓 Agent 门禁。

定义见 docs/agent-gate.md。不替代 fz 的 Grbl 门禁。
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import re
import shutil
import subprocess
import tempfile
import sys
import uuid
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
    "docs/xiaozhi-agent/README.md",
    "docs/xiaozhi-agent/role-prompt.md",
    "docs/xiaozhi-agent/knowledge-base.md",
    "docs/templates/device-test-record.md",
    "docs/templates/release-manifest.md",
    "mcp-server/README.md",
    "firmware/README.md",
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
            re.I,
        ),
        None,
    ),
    (
        "ch9350_as_current",
        re.compile(r"(现行|当前|改用|采用).{0,16}CH9350|(板间|链路).{0,16}CH9350", re.I),
        None,
    ),
    (
        "aliyun_as_prod",
        re.compile(r"(部署|生产|落点|VPS).{0,16}阿里云|阿里云.{0,16}(部署|生产|VPS)", re.I),
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
]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _layer(lid: str, name: str, status: str, detail: str = "", **extra: Any) -> dict[str, Any]:
    out = {"id": lid, "name": name, "status": status, "detail": detail}
    out.update(extra)
    return out


def check_required_files() -> dict[str, Any]:
    # lstat 语义：symlink 视为缺失。门禁校验的是真实落盘的必备文件，
    # 链接到别处的文件可能在部署后被抽走/换指向而不被发现。
    missing = [
        p
        for p in REQUIRED_FILES
        if (ROOT / p).is_symlink() or not (ROOT / p).is_file()
    ]
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
    # 决策 ③ 哑管道原则（docs/agent-gate.md §4 第 3 项，「哑管道」或「云端生成」二者之一）
    if "哑管道" not in text and "云端生成" not in text:
        hard_missing.append("哑管道|云端生成@README")
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



def check_revived_decisions() -> dict[str, Any]:
    hits: list[str] = []
    for kid, pattern, files in REVIVED_PATTERNS:
        for rel in files if files is not None else GOVERNANCE_DOCS:
            path = ROOT / rel
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for m in pattern.finditer(text):
                clause = _rejection_clause(text, m)
                if _has_rejection_heading(text, m) or re.search(
                    r"已否决|否决|不要|禁止|别再|推翻|过时|不可行", clause
                ):
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
# **词表刻意只留「承重词」**（2026-08-07 收紧）。此前 41 个 token 里 **32 个在 17 份
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
      而 17 份治理文档里真正依赖该层的**只有 1 处**（`agent-handoff.md` 的反模式行，
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
    # 元语境判定用「行内命中之前的散文」而非 `_claim_sentence`：后者遇 `：` 即停，
    # 而引述句的标准写法恰是「元语境导语 + `：` + 反引号例子」（`滥用形态举例：
    # \`现已可出货，后续不升级。\``），句窗会把导语整段切掉，把作者的举例判成裸宣称
    # （2026-08-07 实测 E5 判红）。取行首到命中处，正好覆盖「谁在说这句话」。
    if line[:col].count("`") % 2 == 1:
        head = line[:col] + _claim_sentence(text, match)
        return bool(_CLAIM_META_RE.search(head) or _CLAIM_QUOTE_RE.search(head))
    return False


def check_forbidden_claims() -> dict[str, Any]:
    """扫描治理文档正文里是否**做出**了 claims_forbidden 里的宣称。

    此前 CLAIMS_FORBIDDEN 只用于两件事：与白名单列表比对是否漂移，以及打印到报告
    末尾那句「仍受 claims_forbidden 约束」。**没有任何一层真的检查过文档里有没有
    使用这些禁语**——实测在 README.md 追加「硬件已验证，联调完成，现已可出货」，
    docs 层七项全绿并照常打印那句约束提示。门禁列出禁语却不执行，等于把最后一道
    「无证据不宣称」的闸门交给自觉。

    判据按短语切分（复用 _rejection_clause 的边界），短语内出现否定/引述词即视为
    在讲规则而非做宣称，予以放行；否则判红。这样 `agent-gate.md` §5 的表格、
    白名单 JSON 的列表、以及「本条不支持『可出货』任何一项宣称」都能正常存在。
    """
    hits: list[str] = []
    for rel in GOVERNANCE_DOCS:
        path = ROOT / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for claim in CLAIMS_FORBIDDEN:
            for m in re.finditer(re.escape(claim), text):
                if _is_quoted_claim(text, m):
                    continue
                if _has_rejection_heading(text, m):
                    continue
                # 语境窗口取「整句」而非最小短语：否定词常与禁语隔着顿号/斜杠/≠
                # （「写了说明 ≠ 代码已改 ≠ 硬件已验 ≠ 可出货」「不证明：…、可出货」
                # 「本条**不支持**「联调完成」「可出货」…任何一项宣称」）。按短语切分
                # 会把否定词切走，使这些免责句全部误报。句子边界只认换行与句号类。
                #
                # **但只认禁语之前的那半句**（2026-08-07 加固）。整句放行会让否定词
                # 反过来给裸宣称当挡箭牌：`现已可出货，后续不支持。` 与
                # `硬件已验证、可出货，本条不构成 M2 结论。` 在整句口径下双双放行——
                # 免责词落在禁语**后面**，改不了前半句已经把话说出去的事实。
                # 真实免责句式（`不得宣称可出货`、`≠ 可出货`、`本条不提升 … 可出货状态`）
                # 的否定词一律在禁语之前，故收紧后 17 份治理文档零新误报。
                # `+1` 是重叠窗口：`不可出货` 里禁语 `可出货` 与否定词 `不可` 共享
                # 一个字，切在禁语起点会把 `不可` 拦腰截断，导致 `> **不可出货**` 误报。
                sentence = _claim_sentence(text, m)
                claim_at = sentence.find(claim)
                head = sentence[: claim_at + 1] if claim_at >= 0 else sentence
                if _CLAIM_QUOTE_RE.search(head):
                    continue
                snippet = sentence.strip().replace("\n", " ")[:100]
                hits.append(f"{rel}: 「{claim}」于「{snippet}」")
    if hits:
        detail = (
            f"{len(hits)} 处使用了禁止宣称（须先满足 docs/agent-gate.md §5 前置条件）："
            + "; ".join(hits[:5])
        )
        return _layer("forbidden_claims", "禁止宣称扫描", "fail", detail)
    return _layer(
        "forbidden_claims", "禁止宣称扫描", "pass",
        f"治理文档 {len(GOVERNANCE_DOCS)} 份均未做出 {len(CLAIMS_FORBIDDEN)} 条禁止宣称",
    )


def check_edit_allowlist() -> dict[str, Any]:
    path = ROOT / "docs/agent-edit-allowlist.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _layer("edit_allowlist", "改码白名单 JSON", "fail", str(exc))
    missing = [key for key in ALLOWLIST_REQUIRED_KEYS if key not in data]
    if missing:
        return _layer("edit_allowlist", "改码白名单 JSON", "fail", f"missing_keys={missing}")
    if not isinstance(data.get("grbl_section9_only"), list) or not data["grbl_section9_only"]:
        return _layer("edit_allowlist", "改码白名单 JSON", "fail", "grbl_section9_only empty")
    if "report_realtime_status" not in data.get("grbl_forbidden_symbols", []):
        return _layer("edit_allowlist", "改码白名单 JSON", "fail", "forbidden symbols incomplete")
    if data.get("claims_forbidden") != CLAIMS_FORBIDDEN:
        return _layer(
            "edit_allowlist", "改码白名单 JSON", "fail",
            "claims_forbidden 与 agent_gate.py 不一致",
        )
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


# 文档里的 `path:line` / `path:line-line` 引用。只认带扩展名的相对路径，避免把
# `127.0.0.1:8300` 这类端口写法当成行号引用。
_DOC_REF_RE = re.compile(
    r"([A-Za-z0-9_./\\-]+\.(?:py|toml|c|cc|cpp|h|hpp|sh|json|mdc|md)):(\d+)(?:-(\d+))?"
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


def _resolve_doc_ref(ref: str, index: dict[str, list[Path]]) -> Path | None:
    """把文档里的引用落到本仓文件；无法唯一确定就返回 None（该条跳过）。

    文档习惯只写文件名（`gcode_gen.py:235`）或外仓相对路径
    （`Grbl/src/Report.cpp:605`）。前者要求在本仓内唯一命中才校验；后者本仓没有
    对应文件，自然跳过——本层管「引用行号过期」，不管「路径拼错」。
    """
    direct = ROOT / ref
    if direct.is_file():
        return direct
    cands = index.get(ref.rsplit("/", 1)[-1], [])
    if "/" in ref:
        suffix = "/" + ref
        cands = [p for p in cands if p.as_posix().endswith(suffix)]
    return cands[0] if len(cands) == 1 else None


def check_doc_line_refs() -> dict[str, Any]:
    """校验文档里的 `path:line` 引用没有漂移（文件存在且行号在范围内）。

    代码搬家后行号是最容易悄悄失效的东西，而失效的证据引用等于没有证据。只校验
    能唯一落到本仓内的引用；fork 侧路径（`Grbl_Esp32/`、`xiaozhi-esp32/`）和逆向
    产物（`machine/grbl/*.java`）本仓无对应文件，一律跳过——本层管的是「引用行号
    过期」，不是「路径拼错」。
    """
    candidates = {*ROOT.glob("*.md"), *ROOT.glob("docs/**/*.md"), *ROOT.glob("*/README.md")}
    md_files = sorted(
        path
        for path in candidates
        if not any(part in _REF_SKIP_DIRS for part in path.relative_to(ROOT).parts)
    )
    index = _repo_file_index()
    bad: list[str] = []
    checked = 0
    skipped = 0
    for md in md_files:
        rel_md = md.relative_to(ROOT).as_posix()
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return _layer(
                "doc_line_refs",
                "文档 path:line 引用",
                "fail",
                f"无法读取治理文档 {rel_md}（{type(exc).__name__}）",
            )
        for m in _DOC_REF_RE.finditer(text):
            ref = m.group(1).replace("\\", "/").lstrip("./")
            target = _resolve_doc_ref(ref, index)
            if target is None:
                skipped += 1
                continue
            # 区间引用（`:205-207`）只校验较大的端点：末行在范围内则整段在范围内
            lineno = max(int(m.group(2)), int(m.group(3) or 0))
            checked += 1
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
            if lineno < 1 or lineno > total:
                rel_target = target.relative_to(ROOT).as_posix()
                bad.append(f"{rel_md} → {ref}:{lineno}（{rel_target} 共 {total} 行）")
    if bad:
        detail = f"{len(bad)}/{checked} 处行号越界：" + "; ".join(bad[:10])
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
    candidates = {*ROOT.glob("*.md"), *ROOT.glob("docs/**/*.md"), *ROOT.glob("*/README.md")}
    md_files = sorted(
        path
        for path in candidates
        if not any(part in _REF_SKIP_DIRS for part in path.relative_to(ROOT).parts)
    )
    bad: list[str] = []
    checked = 0
    skipped = 0
    for md in md_files:
        rel_md = md.relative_to(ROOT).as_posix()
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return _layer(
                "external_doc_line_refs",
                label,
                "fail",
                f"无法读取治理文档 {rel_md}（{type(exc).__name__}）",
            )
        for m in _DOC_REF_RE.finditer(text):
            ref = m.group(1).replace("\\", "/").lstrip("./")
            if (ROOT / ref).is_file():
                continue  # 本仓的归 doc_line_refs 层管
            cands = index.get(ref.rsplit("/", 1)[-1], [])
            if "/" in ref:
                suffix = "/" + ref
                cands = [p for p in cands if p.as_posix().endswith(suffix)]
            if len(cands) != 1:
                skipped += 1
                continue
            target = cands[0]
            lineno = max(int(m.group(2)), int(m.group(3) or 0))
            checked += 1
            try:
                total = len(target.read_text(encoding="utf-8", errors="replace").splitlines())
            except OSError as exc:
                return _layer(
                    "external_doc_line_refs",
                    label,
                    "fail",
                    f"无法读取外仓引用目标 {ref}（{type(exc).__name__}）",
                )
            if lineno < 1 or lineno > total:
                bad.append(f"{rel_md} → {ref}:{lineno}（实际 {total} 行）")
    if bad:
        tail = "；fork 工作树有未提交改动时行号会漂，先分清是文档过期还是源码已变"
        detail = f"{len(bad)}/{checked} 处行号越界：" + "; ".join(bad[:8]) + tail
        return _layer("external_doc_line_refs", label, "fail", detail)
    if checked == 0:
        detail = f"一条外仓引用都没解析到（跳过 {skipped} 处）——子目录白名单或文档约定已变，先修门禁"
        return _layer("external_doc_line_refs", label, "fail", detail)
    note = f"；未找到 {', '.join(absent)}" if absent else ""
    detail = f"校验 {checked} 处行号均在范围内；跳过 {skipped} 处（同名多份/无法定位）{note}"
    return _layer("external_doc_line_refs", label, "pass", detail)


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
    finally:
        shutil.rmtree(pytest_tmp, ignore_errors=True)
    tail = (proc.stdout + proc.stderr).strip().splitlines()
    detail = tail[-1] if tail else f"exit={proc.returncode}"
    status = "pass" if proc.returncode == 0 else "fail"
    return _layer("pytest", "mcp-server pytest", status, detail, exit_code=proc.returncode)


def run_e2e() -> dict[str, Any]:
    py = _mcp_python()
    if py is None:
        return _layer("e2e_local", "e2e_local.py", "fail", "缺少 mcp-server/.venv")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"  # 同 run_pytest：防 GBK stdout 解出 \ufffd
    try:
        proc = subprocess.run(
            [str(py), "scripts/e2e_local.py"],
            cwd=ROOT / "mcp-server",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=300,  # 同上：e2e 会起假 Grbl socket，卡住时必须判 fail 而不是干等
        )
    except subprocess.TimeoutExpired:
        return _layer("e2e_local", "e2e_local.py", "fail", "e2e_local 超过 300s 未返回（疑似挂死）")
    out = (proc.stdout + proc.stderr).strip()
    ok = proc.returncode == 0 and re.search(r"\bPASS\b", out) is not None
    detail = out.splitlines()[-1] if out else f"exit={proc.returncode}"
    return _layer("e2e_local", "e2e_local.py", "pass" if ok else "fail", detail, exit_code=proc.returncode)


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
    args = parser.parse_args(argv)

    layers: list[dict[str, Any]] = []
    for layer_id, run_layer in profile_layers(args.profile):
        layer = run_layer()
        # plan 的 id 与 _layer() 落盘的 id 必须一致，否则报告里的层名会与
        # docs/agent-gate.md 的分层定义脱钩，读报告的人无法判断查了什么。
        assert layer["id"] == layer_id, f"层 id 不一致: plan={layer_id} actual={layer['id']}"
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
