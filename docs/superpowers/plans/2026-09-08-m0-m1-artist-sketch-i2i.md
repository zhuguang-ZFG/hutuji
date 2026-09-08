# M0 控机冒烟 + M1 艺术家素描图生图 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 先确认小程序能把图推到写字机并做基础控制（M0）；再把照片 i2i 从「上色本均匀描边」拉到 **艺术家素描（笔式可画）** 并通过样张包 + 随手拍目视（M1）。

**Architecture:** i2i 提示词与通道策略仍集中在 `hutuji_mcp/modelscope_client.py` 的 `I2I_LINE_ART_PROMPT`（TCB 同源引用）；portal `_refine_photo_job` 继续双通道对冲，但增加「素描质量门」：若胜出通道持续达不到素描及格表，可用 env 关 TCB 回落魔搭单通道。描摹 / §5 / 纸面风险闸不放宽。M0 纯真机冒烟，无产品行为改动。

**Tech Stack:** Python 3.12+、httpx、pytest、draw-portal FastAPI、微信小程序、魔搭 Qwen-Image-Edit-2509、TCB 混元 i2i、skimage 中心线描摹链。

**Spec:** `docs/superpowers/specs/2026-09-08-ship-roadmap-design.md`（画质档已补强为艺术家素描）。

## Global Constraints

- 硬闸含图生图目视过关：样张包 + ≥5 张随手拍；及格表见规格 M1。
- 「艺术家素描」= 结构线 + 排线疏密表达体积；**不是**灰阶照片、**不是**上色本均匀单线。
- 单笔绘图仪无连续灰阶；禁止为像照片而放宽 `validate_gcode` §5 或 `paper_risk`。
- 提示词唯一真源：`modelscope_client.I2I_LINE_ART_PROMPT`；TCB 必须同源（现有测试钉死）。
- 隐私样张只落 `results/local-only/`（gitignore），不进 git。
- 凭据不入库；部署仍 scp + 显式 restart；本仓门禁 `python scripts/agent_gate.py --profile hub`。
- 未过目视不得宣称画质完成 / 可出货。
- 本计划 **不含** M2–M5（控机完整、小程序去简陋、30 轮、合规）；另开计划。

---

## File map

| 路径 | 职责 |
|------|------|
| `mcp-server/hutuji_mcp/modelscope_client.py` | `I2I_LINE_ART_PROMPT` v3（素描语言） |
| `mcp-server/hutuji_mcp/tcb_i2i.py` | 默认 prompt 仍引用同源常量（通常零改） |
| `mcp-server/tests/test_modelscope.py` | 钉死 payload.prompt == 常量 |
| `mcp-server/tests/test_tcb_i2i.py` | 钉死 TCB 与魔搭同源 |
| `mcp-server/tests/test_i2i_prompt_sketch.py` | **新建**：素描关键词 / 禁灰阶词契约（不依赖付费 API） |
| `scripts/i2i_sketch_eval.py` | **新建**：对本机样张跑 i2i→落盘 refined PNG（人工目视；可选） |
| `docs/protocol.md` §10.4.9 | 提示词目标档与及格表一句同步 |
| `docs/release-readiness.md` | M0/M1 证据条 |
| `docs/worktree-inventory.md` | 脏树若有 |
| `draw-portal/` / weapp | M0 只测不改；M1 默认不改前端（除非双预览文案必须点名「素描精修」） |

---

### Task 1: M0 控机冒烟（真机，无代码）

**Files:**
- Create（证据，本机不入库）: `results/local-only/2026-09-08/m0-weapp-control-smoke.md`
- Modify（冒烟通过后）: `docs/release-readiness.md`（文首加 M0 证据条）

**Interfaces:**
- Consumes: 已绑定账号、设备在线、体验版/开发版小程序、今晚推送自愈已部署的生产
- Produces: M0 pass/fail 书面结论；fail 则本计划 **停止**，先开链路修复，不进 Task 2+

- [ ] **Step 1: 确认绑定与在线**

小程序「我的」或绑机页确认已绑定。任选：

```powershell
# 若有设备 token 环境，只读探活（勿把 token 写入仓库）
cd D:\Users\hutuji
# 用既有运维方式调用 hutuji.status；期望 connected/ready/authorized 为真
```

或小程序控制页刷新后能显示非空 status。

Expected: 设备在线；否则记 fail 原因（token/UUID/离线）并停。

- [ ] **Step 2: 推图上屏**

小程序传一张图（或历史图）→ 推送到设备 → 屏上出现预览。

Expected: 预览可见；记时间与 job 大致来源。失败则查 portal journal / 自愈 401/403/404，**停在 M0**。

- [ ] **Step 3: 控制页三键**

在有任务或可触发状态下点：暂停 → 继续 → 停止（确认对话框）。

Expected: 每次有 toast/屏显/状态变化之一；换纸中停止允许诚实「无法即停」。

- [ ] **Step 4: 写证据**

创建 `results/local-only/2026-09-08/m0-weapp-control-smoke.md`（本机），含：时间、板型、是否上屏、三键结果、失败则根因。

- [ ] **Step 5: 勾 readiness**

仅当 Step 1–3 全过：在 `docs/release-readiness.md` 文首追加一条「M0 小程序控机冒烟 ✅ …」，注明证据路径。未过则写 🔴 阻塞，**不要**开始 Task 2。

- [ ] **Step 6: Commit（仅文档，且仅用户要求提交时）**

```powershell
# 仅当用户明确要求 commit；否则跳过
cd D:\Users\hutuji
git add docs/release-readiness.md
git commit -m "$( @'
docs: 登记 M0 小程序控机冒烟证据

'@ )"
```

---

### Task 2: 素描提示词契约测试（RED）

**Files:**
- Create: `mcp-server/tests/test_i2i_prompt_sketch.py`
- Test: 同上

**Interfaces:**
- Consumes: `hutuji_mcp.modelscope_client.I2I_LINE_ART_PROMPT`
- Produces: 失败测试锁定 v3 必须含素描语言、禁灰阶照片词、题材不限死「只画人」

- [ ] **Step 1: 写入失败测试**

```python
"""i2i 提示词必须导向艺术家素描（笔式可画），而非上色本描边或灰阶照片。"""
from __future__ import annotations

from hutuji_mcp import modelscope_client

PROMPT = modelscope_client.I2I_LINE_ART_PROMPT


def test_i2i_prompt_demands_sketch_language() -> None:
    # 须显式要素描/排线/结构（中英任一稳定子串）
    sketch_markers = ("素描", "排线", "结构线", "cross-hatch", "hatching", "sketch")
    assert any(m in PROMPT for m in sketch_markers), PROMPT


def test_i2i_prompt_forbids_photo_gray_wash() -> None:
    # 必须禁止灰阶/照片感（现网已有「不要阴影」族；v3 须保留或加强）
    forbid_markers = ("不要阴影", "不要灰度", "不要颜色")
    assert all(m in PROMPT for m in forbid_markers), PROMPT


def test_i2i_prompt_not_flat_coloring_book_only() -> None:
    # 旧 v2「只用均匀的黑色单线条勾勒」会把模型锁死上色本；v3 不得保留该整句
    assert "只用均匀的黑色单线条勾勒" not in PROMPT, PROMPT


def test_i2i_prompt_subject_agnostic_and_likeness() -> None:
    # 人像要像本人；同时覆盖非人像物体/场景（不限死只处理人物）
    assert "本人" in PROMPT or "五官" in PROMPT or "identity" in PROMPT.lower()
    assert any(x in PROMPT for x in ("物体", "场景", "主体", "subject")), PROMPT
```

- [ ] **Step 2: 跑测试确认 RED**

```powershell
cd D:\Users\hutuji\mcp-server
.venv\Scripts\python -m pytest tests/test_i2i_prompt_sketch.py -q
```

Expected: FAIL（当前 v2 含「只用均匀的黑色单线条勾勒」，且缺素描/排线标记）。

- [ ] **Step 3: Commit（用户要求时）**

```powershell
git add mcp-server/tests/test_i2i_prompt_sketch.py
git commit -m "$( @'
test: 钉死 i2i 艺术家素描提示词契约（先红）

'@ )"
```

---

### Task 3: 实现 `I2I_LINE_ART_PROMPT` v3（GREEN）

**Files:**
- Modify: `mcp-server/hutuji_mcp/modelscope_client.py`（常量 `I2I_LINE_ART_PROMPT` 与上方注释）
- Test: `tests/test_i2i_prompt_sketch.py`、`tests/test_modelscope.py`、`tests/test_tcb_i2i.py`

**Interfaces:**
- Consumes: Task 2 契约
- Produces: 新字符串常量；`generate_i2i_png` / `tcb_i2i.generate_i2i_png` 默认 prompt 自动跟新

- [ ] **Step 1: 替换常量为 v3 全文**

在 `modelscope_client.py` 将 `I2I_LINE_ART_PROMPT` 改为（可微调用词，但必须仍过 Task 2 断言）：

```python
# 照片→艺术家素描（笔式可画）提示词 v3（2026-09-08）：
# 目标=结构线+排线疏密表达体积，像手绘素描；禁止灰阶照片糊描与上色本均匀单线。
# 人像须能认出本人；静物/风景/宠物等非人像同样适用。
I2I_LINE_ART_PROMPT = (
    "把这张照片画成黑白钢笔素描：保留主体身份与关键特征"
    "（人物须严格保持五官、脸型、发型与表情，必须能看出是本人；"
    "物体与场景须能一眼辨认），纯白背景，用清晰的轮廓线与结构线，"
    "并用疏密不同的排线或交叉排线表现体积与明暗，达到艺术家素描水准；"
    "只要黑色线条，不要阴影涂抹、不要灰度渐变、不要颜色、不要文字、不要水印，"
    "不要均匀单线描边上色本风格，不要照片写实灰阶"
)
```

同步更新该常量上方中文注释（写明 v3 / 艺术家素描 / supersede 均匀单线）。

- [ ] **Step 2: 跑素描契约 + 同源测试**

```powershell
cd D:\Users\hutuji\mcp-server
.venv\Scripts\python -m pytest tests/test_i2i_prompt_sketch.py tests/test_modelscope.py tests/test_tcb_i2i.py -q
```

Expected: PASS（`test_tcb_i2i` / `test_modelscope` 仍断言 `payload["prompt"] == I2I_LINE_ART_PROMPT`）。

- [ ] **Step 3: Commit（用户要求时）**

```powershell
git add mcp-server/hutuji_mcp/modelscope_client.py mcp-server/tests/test_i2i_prompt_sketch.py
git commit -m "$( @'
feat: i2i 提示词升到艺术家素描（笔式可画）v3

'@ )"
```

---

### Task 4: 本机样张评估脚本（人工目视，可选付费调用）

**Files:**
- Create: `scripts/i2i_sketch_eval.py`
- Create（本机）: `results/local-only/i2i-samples/README.txt`（说明放 5 张样张，gitignore 已覆盖 results）

**Interfaces:**
- Consumes: `MODELSCOPE_TOKEN` 或环境已有 `HUTUJI_MODELSCOPE_TOKEN`；样张目录 PNG/JPEG
- Produces: `results/local-only/i2i-eval/<stamp>/` 下每张的 `in.*` 拷贝 + `out.png`；打印路径供目视

- [ ] **Step 1: 实现评估脚本**

```python
"""对本机样张跑魔搭 i2i，落盘供「艺术家素描」目视。不入库、不写凭据。

用法（PowerShell）:
  $env:HUTUJI_MODELSCOPE_TOKEN='...'   # 或 MODELSCOPE_TOKEN
  .\\mcp-server\\.venv\\Scripts\\python.exe scripts\\i2i_sketch_eval.py `
      --input-dir results/local-only/i2i-samples `
      --out-dir results/local-only/i2i-eval

缺 token 或目录为空时 exit 2，并打印中文原因。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp-server"))

from hutuji_mcp import modelscope_client  # noqa: E402


def _token() -> str:
    return (
        os.environ.get("HUTUJI_MODELSCOPE_TOKEN", "").strip()
        or os.environ.get("MODELSCOPE_TOKEN", "").strip()
    )


async def _one(client: httpx.AsyncClient, token: str, src: Path, dest: Path) -> None:
    raw = src.read_bytes()
    out = await modelscope_client.generate_i2i_png(client, token, raw)
    dest.write_bytes(out)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--input-dir", type=Path, required=True)
    p.add_argument("--out-dir", type=Path, required=True)
    args = p.parse_args()
    token = _token()
    if not token:
        print("缺少 HUTUJI_MODELSCOPE_TOKEN / MODELSCOPE_TOKEN", file=sys.stderr)
        return 2
    files = sorted(
        [
            *args.input_dir.glob("*.png"),
            *args.input_dir.glob("*.jpg"),
            *args.input_dir.glob("*.jpeg"),
            *args.input_dir.glob("*.webp"),
        ]
    )
    if not files:
        print(f"样张目录为空: {args.input_dir}", file=sys.stderr)
        return 2
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_root = args.out_dir / stamp
    out_root.mkdir(parents=True, exist_ok=True)

    async def run() -> None:
        async with httpx.AsyncClient(timeout=120.0) as client:
            for src in files:
                sub = out_root / src.stem
                sub.mkdir(parents=True, exist_ok=True)
                (sub / f"in{src.suffix.lower()}").write_bytes(src.read_bytes())
                print(f"精修中 {src.name} …")
                await _one(client, token, src, sub / "out.png")
                print(f"  -> {sub / 'out.png'}")

    asyncio.run(run())
    print(f"完成 {len(files)} 张，目录 {out_root}")
    print("目视及格表: 像主题 / 素描排线 / 干净可画 / 愿称素描作品")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: 准备样张目录（人工）**

在 `results/local-only/i2i-samples/` 放入约 5 张：人像×2、静物、风景、宠物（用户提供或选用可公开测试图）。写 `README.txt` 一行说明「仅本机，不提交」。

- [ ] **Step 3: 跑评估（需 token；免费层可能排队数分钟/张）**

```powershell
cd D:\Users\hutuji
$env:HUTUJI_MODELSCOPE_TOKEN='<本机已有，勿提交>'
.\mcp-server\.venv\Scripts\python.exe scripts\i2i_sketch_eval.py `
  --input-dir results\local-only\i2i-samples `
  --out-dir results\local-only\i2i-eval
```

Expected: 每张有 `out.png`。按及格表目视；若整体仍像上色本或糊灰阶 → **回到 Task 3 改提示词文案**（保持契约测试绿），再重跑本步。勿放宽 §5。

- [ ] **Step 4: TCB 对照（可选）**

若生产 TCB 常胜出：用 portal 真传一张样张，对比 refined 与魔搭 `out.png`。若 TCB 明显达不到素描档，在部署笔记中建议临时 `HUTUJI_TCB_I2I_ENABLED=0`（契约已允许），质量门优先于对冲速度。

- [ ] **Step 5: Commit 脚本（用户要求时；不含样张）**

```powershell
git add scripts/i2i_sketch_eval.py
git commit -m "$( @'
chore: 本机 i2i 素描样张评估脚本

'@ )"
```

---

### Task 5: 契约文档与部署 + 真机随手拍

**Files:**
- Modify: `docs/protocol.md`（§10.4.9「明确不做 / 提示词」相关行：目标档改为艺术家素描笔式可画；仍不对用户开放自定义 prompt）
- Modify: `docs/release-readiness.md`
- Deploy: 阿里云 + 京东云 `hutuji_mcp/modelscope_client.py`（及若改了的测试无关文件不部署）

**Interfaces:**
- Consumes: Task 3 已合并的常量；Task 4 样张目视至少一轮可接受
- Produces: 生产跑 v3 prompt；随手拍 ≥5 记录

- [ ] **Step 1: 同步 protocol §10.4.9**

将提示词描述从「固定线稿导向」改为「固定为艺术家素描（结构线+排线、笔式可画）；不对用户开放自定义」。不改两段式 UX、不改下载闸/并发。

- [ ] **Step 2: 双机部署 modelscope_client.py**

按 `docs/deployment.md` 纪律：从 Git blob 导出 LF、SHA-256 对账、CR=0、备份、scp、阿里云 `systemctl restart hutuji-mcp hutuji-cloud-bridge hutuji-draw-portal`（及若 worker 共用包则 worker）、京东云 mcp+portal。冒烟：上传照片见 refined 非立刻 failed。

- [ ] **Step 3: 真机随手拍 ≥5**

用户用小程序连拍/传 ≥5 张不同题材，等精修，目视及格表；至少 1 张推到设备并出纸（若时间紧，预览+纸面可分两天，但 M1 闭合前纸面至少 2 张）。

- [ ] **Step 4: readiness 证据条**

文首写 M1：prompt v3 SHA/部署时间、样张结论、随手拍张数、纸面张数、未达标项（若有则保持 🟡）。

- [ ] **Step 5: hub 门禁**

```powershell
cd D:\Users\hutuji
python scripts\agent_gate.py --profile hub
```

Expected: overall pass（exit 0）。若改了 `path:line` 引用则先人工核对再 `--accept-line-refs`。

- [ ] **Step 6: Commit 文档（用户要求时）**

```powershell
git add docs/protocol.md docs/release-readiness.md docs/worktree-inventory.md
git commit -m "$( @'
docs: M1 艺术家素描 i2i 部署与目视证据

'@ )"
```

---

### Task 6: M1 闭合判定（无新代码）

**Files:**
- Modify: `docs/release-readiness.md`（仅当及格）

- [ ] **Step 1: 对照规格及格表逐条打勾**

样张包全过 + 随手拍 ≥5 过 + 纸面主题可辨 ≥2 → M1 ✅。  
任一条不过 → M1 保持 🟡，列下一轮提示词/关 TCB/后处理实验，**不得**写画质完成。

- [ ] **Step 2: 宣布下一计划边界**

M1 ✅ 后另开 **M2 小程序控机完整** 计划；本计划结束。

---

## Self-review（对照规格）

| 规格要求 | 任务 |
|----------|------|
| M0 冒烟 | Task 1 |
| 艺术家素描档 + 及格表 | Task 2–3 契约、Task 4–6 目视 |
| 样张包 + 随手拍 | Task 4、Task 5 Step 3 |
| 不放宽 §5 / 纸面闸 | Global Constraints + Task 3/4 禁止项 |
| 题材不限人像 | Task 2 `subject_agnostic` + v3 文案 |
| 第一刀不含 M2–M5 | Global Constraints 写明 |
| protocol / readiness 同步 | Task 5 |

占位符扫描：无 TBD/「适当处理」。类型名与现有 `I2I_LINE_ART_PROMPT` / `generate_i2i_png` 一致。
