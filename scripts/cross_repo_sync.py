#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三仓状态自动同步与协调工具。

解决问题：
1. 跨仓引用容易过期（worktree-inventory.md 记录手动维护）
2. 门禁需要跨三仓协调（fz 门禁 + hub 门禁手动运行）
3. 版本同步困难（固件版本、协议版本、文档引用）
4. 部署流程手动化（scp + SHA-256 人工核对）

用法：
    python scripts/cross_repo_sync.py --check       # 检查三仓状态
    python scripts/cross_repo_sync.py --sync-docs   # 同步文档引用
    python scripts/cross_repo_sync.py --gate-all    # 运行全部门禁
    python scripts/cross_repo_sync.py --deploy-plan # 生成部署计划
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 三仓路径配置（从环境变量或默认路径）
HUTUJI_ROOT = Path(os.getenv("HUTUJI_ROOT", str(Path(__file__).resolve().parents[1]))).resolve()
GRBL_ROOT = Path(os.getenv("GRBL_ROOT", str(HUTUJI_ROOT.parent / "Grbl_Esp32"))).resolve()
XIAOZHI_ROOT = Path(os.getenv("XIAOZHI_ROOT", str(HUTUJI_ROOT.parent / "xiaozhi-esp32"))).resolve()
FZ_ROOT = Path(os.getenv("FZ_ROOT", str(HUTUJI_ROOT.parent / "zhugu/fz"))).resolve()


@dataclass
class RepoState:
    """仓库状态快照。"""
    name: str
    path: Path
    head_commit: str
    head_short: str
    branch: str
    dirty_files: list[str]
    untracked_files: list[str]
    timestamp: str

    def is_clean(self) -> bool:
        return not self.dirty_files and not self.untracked_files


def run_command(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """运行命令并返回结果。"""
    env = dict(os.environ, HUTUJI_ROOT=str(HUTUJI_ROOT), GRBL_ROOT=str(GRBL_ROOT),
               XIAOZHI_ROOT=str(XIAOZHI_ROOT), FZ_ROOT=str(FZ_ROOT),
               PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(HUTUJI_ROOT / "mcp-server"),
                                                   str(HUTUJI_ROOT / "draw-portal"), env.get("PYTHONPATH"))))
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
        encoding="utf-8",
        timeout=1800,
        env=env,
    )


def get_repo_state(name: str, path: Path) -> RepoState | None:
    """获取仓库当前状态。"""
    if not (path / ".git").exists():
        print(f"⚠️  {name} 仓库不存在: {path}", file=sys.stderr)
        return None

    try:
        # HEAD commit
        result = run_command(["git", "rev-parse", "HEAD"], cwd=path)
        head_commit = result.stdout.strip()
        head_short = head_commit[:7]

        # 当前分支
        result = run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=path)
        branch = result.stdout.strip()

        # NUL 保留中文、空格及换行文件名；XY 同时包含暂存区和工作树。
        result = run_command(["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"], cwd=path)
        records = iter(result.stdout.split("\0"))
        dirty_files, untracked_files = [], []
        for record in records:
            if not record:
                continue
            status, filename = record[:2], record[3:]
            if status == "??":
                untracked_files.append(filename)
            else:
                dirty_files.append(filename)
                if "R" in status or "C" in status:
                    dirty_files.append(next(records))
        dirty_files = sorted(set(dirty_files))

        return RepoState(
            name=name,
            path=path,
            head_commit=head_commit,
            head_short=head_short,
            branch=branch,
            dirty_files=dirty_files,
            untracked_files=untracked_files,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
    except (OSError, subprocess.SubprocessError, StopIteration) as e:
        print(f"获取 {name} 状态失败：{type(e).__name__}", file=sys.stderr)
        return None


def check_all_repos() -> dict[str, RepoState]:
    """检查所有仓库状态。"""
    print("🔍 检查三仓状态...")
    print()

    repos = {
        "hutuji": get_repo_state("hutuji", HUTUJI_ROOT),
        "Grbl_Esp32": get_repo_state("Grbl_Esp32", GRBL_ROOT),
        "xiaozhi-esp32": get_repo_state("xiaozhi-esp32", XIAOZHI_ROOT),
        "fz": get_repo_state("fz", FZ_ROOT),
    }

    # 过滤掉不存在的仓库
    repos = {k: v for k, v in repos.items() if v is not None}

    # 打印状态
    for name, state in repos.items():
        status_icon = "✅" if state.is_clean() else "🟡"
        print(f"{status_icon} {name:15} | {state.branch:20} | {state.head_short} | "
              f"dirty={len(state.dirty_files):2} untracked={len(state.untracked_files):2}")

    print()

    # 详细脏文件列表
    for name, state in repos.items():
        if not state.is_clean():
            print(f"📝 {name} 脏文件:")
            for f in state.dirty_files[:10]:  # 最多显示 10 个
                print(f"   M {f}")
            if len(state.dirty_files) > 10:
                print(f"   ... 还有 {len(state.dirty_files) - 10} 个")
            for f in state.untracked_files[:10]:
                print(f"   ? {f}")
            if len(state.untracked_files) > 10:
                print(f"   ... 还有 {len(state.untracked_files) - 10} 个")
            print()

    return repos


def sync_worktree_inventory(repos: dict[str, RepoState]) -> bool:
    """只更新独立的当前快照块，历史提交和设备证据不得自动替换。"""
    inventory_path = HUTUJI_ROOT / "docs" / "worktree-inventory.md"
    if not inventory_path.exists() or len(repos) != 4:
        print("台账或必需仓库缺失，未更新快照", file=sys.stderr)
        return False
    content = inventory_path.read_text(encoding="utf-8")
    begin, end = "<!-- current-repo-snapshot:start -->", "<!-- current-repo-snapshot:end -->"
    snapshot = [begin, "当前工作树快照（只读 Git 结果，不代表部署/构建/实机状态）：", ""]
    for state in repos.values():
        snapshot.append(f"- {state.name}：HEAD `{state.head_commit}`；已跟踪变更 {len(state.dirty_files)}；未跟踪 {len(state.untracked_files)}；取证 {state.timestamp}。")
    snapshot.append(end)
    block = "\n".join(snapshot)
    if begin in content or end in content:
        if content.count(begin) != 1 or content.count(end) != 1 or content.index(begin) > content.index(end):
            print("当前快照边界损坏，保留原台账", file=sys.stderr)
            return False
        content = content[:content.index(begin)] + block + content[content.index(end) + len(end):]
    else:
        content = content.rstrip() + "\n\n" + block + "\n"
    inventory_path.write_text(content, encoding="utf-8")
    print("已更新当前 Git 快照，历史条目保留")
    return True


def run_all_gates(repos: dict[str, RepoState]) -> dict[str, bool]:
    """运行所有相关门禁。"""
    print("🚪 运行跨仓门禁...")
    print()

    results = {}
    missing = {"hutuji", "Grbl_Esp32", "xiaozhi-esp32", "fz"} - repos.keys()
    if missing:
        print("缺少必需仓库：" + ", ".join(sorted(missing)), file=sys.stderr)
        return {"required_repositories": False}

    # 1. hutuji hub 门禁
    if "hutuji" in repos:
        profile = "full" if not repos["Grbl_Esp32"].is_clean() else "hub"
        print(f"hutuji --profile {profile}")
        result = run_command(
            [sys.executable, "-B", "scripts/agent_gate.py", "--profile", profile],
            cwd=HUTUJI_ROOT,
            check=False,
        )
        results["hutuji_" + profile] = result.returncode == 0
        if results["hutuji_" + profile]:
            print("   ✅ PASS")
        else:
            print(f"   ❌ FAIL (exit={result.returncode})")
        print()

    # --gate-all 是显式完整检查请求；已提交的 Grbl 或 fz 改动不会出现在脏树里。
    print("fz --profile standard")
    result = run_command(
        [sys.executable, "-B", "scripts/agent_gate.py", "--profile", "standard"],
        cwd=FZ_ROOT, check=False,
    )
    results["fz_standard"] = result.returncode == 0
    print("   PASS" if result.returncode == 0 else f"   FAIL (exit={result.returncode})")

    # 枢纽门禁不执行 Portal，不能把服务端用户链路漏掉后仍报告全部通过。
    result = run_command(
        [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
        cwd=HUTUJI_ROOT / "draw-portal", check=False,
    )
    results["draw_portal"] = result.returncode == 0
    print("Portal", "PASS" if result.returncode == 0 else "FAIL")

    # release.py 没有 --check；这里执行真实 host 回归，镜像构建仍单独取证。
    result = run_command(
        [sys.executable, "-B", "-m", "unittest", "discover", "-s", "scripts/tests", "-v"],
        cwd=XIAOZHI_ROOT, check=False,
    )
    results["xiaozhi_host"] = result.returncode == 0
    print("S3 host", "PASS" if result.returncode == 0 else "FAIL")
    print("镜像构建未由本命令执行；须另核 CXX 编译与产物 SHA")

    # 汇总
    print("📊 门禁汇总:")
    for gate, passed in results.items():
        icon = "✅" if passed else "❌"
        print(f"   {icon} {gate}")
    print()

    all_passed = all(results.values())
    if all_passed:
        print("所列软件检查通过；镜像与实机仍须独立验证")
    else:
        print("⚠️  部分门禁失败，请修复后重试")

    return results


def generate_deployment_plan(repos: dict[str, RepoState]) -> None:
    """生成部署计划（包含 SHA-256 校验清单）。"""
    print("📋 生成部署计划...")
    print()

    if "hutuji" not in repos:
        print("❌ hutuji 仓库不可用", file=sys.stderr)
        return

    hutuji_state = repos["hutuji"]

    # 识别需要部署的文件
    deploy_files = []

    prefixes = ("mcp-server/hutuji_mcp/", "draw-portal/draw_portal/", "draw-portal/draw_admin/")
    for relative in sorted(set(hutuji_state.dirty_files + hutuji_state.untracked_files)):
        if not relative.startswith(prefixes):
            continue
        path = HUTUJI_ROOT / relative
        if path.is_symlink() or not path.resolve().is_relative_to(HUTUJI_ROOT.resolve()):
            raise ValueError("候选路径越界")
        if path.suffix not in (".py", ".toml", ".json", ".html", ".css", ".js", ".svg", ".png"):
            continue
        content = path.read_bytes() if path.is_file() else None
        deploy_files.append({"file": relative, "sha256": hashlib.sha256(content).hexdigest() if content is not None else None,
                             "size": len(content) if content is not None else 0,
                             "action": "review" if content is not None else "delete-review"})

    # 生成部署计划
    plan = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "hutuji_commit": hutuji_state.head_commit,
        "hutuji_branch": hutuji_state.branch,
        "source": "worktree-review-only",
        "deployable": False,
        "files": deploy_files,
        "checks": [
            "审查并提交明确文件，从该 commit blob 重新构建发布包",
            "按节点暂停入口、排空旧队列并协调 worker 后备份当前版本",
            "SHA-256 逐文件校验",
            "重启服务",
            "验证 HTTP 状态码、逐文件 SHA 和备机 bridge inactive",
            "preflight 验证",
        ],
    }

    # 保存到文件
    plan_file = HUTUJI_ROOT / "tmp" / f"deploy-plan-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    plan_file.parent.mkdir(exist_ok=True)
    plan_file.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"✅ 部署计划已生成: {plan_file}")
    print()
    print("📦 需要部署的文件:")
    for f in deploy_files:
        print(f"   {f['file']}")
        print(f"      SHA-256: {f['sha256'] or '已删除，需单独审查'}")
        print(f"      Size: {f['size']} bytes")
    print()
    print("🔧 部署步骤:")
    for i, check in enumerate(plan["checks"], 1):
        print(f"   {i}. {check}")


def main():
    parser = argparse.ArgumentParser(description="三仓协调与自动化工具")
    parser.add_argument("--check", action="store_true", help="检查三仓状态")
    parser.add_argument("--sync-docs", action="store_true", help="同步文档引用")
    parser.add_argument("--gate-all", action="store_true", help="运行全部门禁")
    parser.add_argument("--deploy-plan", action="store_true", help="生成部署计划")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")

    args = parser.parse_args()

    # 默认：检查状态
    if not any([args.check, args.sync_docs, args.gate_all, args.deploy_plan]):
        args.check = True

    # 获取所有仓库状态
    with contextlib.redirect_stdout(sys.stderr) if args.json else contextlib.nullcontext():
        repos = check_all_repos()

    if args.json:
        if args.sync_docs or args.gate_all or args.deploy_plan:
            parser.error("--json 仅用于状态快照，不能省略已请求的修改或门禁")
        # JSON 输出
        output = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "repos": {
                name: {
                    "head": state.head_commit,
                    "branch": state.branch,
                    "clean": state.is_clean(),
                    "dirty_count": len(state.dirty_files),
                    "untracked_count": len(state.untracked_files),
                }
                for name, state in repos.items()
            }
        }
        print(json.dumps(output, indent=2))
        return int(len(repos) != 4)

    # 执行操作
    if args.sync_docs:
        if not sync_worktree_inventory(repos):
            return 1

    if args.gate_all:
        results = run_all_gates(repos)
        if not all(results.values()):
            sys.exit(1)

    if args.deploy_plan:
        generate_deployment_plan(repos)
    return int(len(repos) != 4)


if __name__ == "__main__":
    raise SystemExit(main())
