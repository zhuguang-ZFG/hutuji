"""供 OMP 使用的独立 PC 仿真 MCP；不挂入生产 /mcp。"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import contextvars
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

import mcp.server.stdio
import mcp.types as types
from mcp.server.lowlevel import Server

from .gcode_gen import GcodeError, MAX_LINE_PAYLOAD_BYTES, svg_to_gcode, validate_gcode

ROOT = Path(__file__).resolve().parents[1]
TEST_SVG = ROOT / "tests" / "test_star.svg"
SERVER_NAME = "hutuji-pc-simulation"
SERVER_VERSION = "1.0.0"
LOCAL_E2E_TIMEOUT_SECONDS = 120
SIL_LIMITATIONS = [
    "PC SIL 不证明真实换纸机械行为",
    "PC SIL 不证明 App 语音、云端链路、烧录或 HIL 验收",
]

# 取消传播槽：`asyncio.to_thread` 会 copy_context，故这里放的可变 list 在工作线程内
# 是同一对象。线程把活的 Popen 注册进来，async 侧被取消时据此 kill 子进程，
# 让阻塞的 subprocess 立刻返回、线程归还默认执行器。
# 不这样做的话：取消只让 await 立即返回，线程与子进程仍活到 120s 超时，
# 反复取消可占满 min(32, cpu+4) 个默认执行器线程，阻塞同进程一切 to_thread。
_ACTIVE_CHILDREN: contextvars.ContextVar[list[subprocess.Popen] | None] = (
    contextvars.ContextVar("hutuji_sim_active_children", default=None)
)


def build_evidence(
    *,
    ok: bool,
    scenario: str,
    observations: list[dict[str, Any]],
    contract_refs: list[str],
    artifacts: list[Path] | None = None,
    limitations: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "status": "pass" if ok else "fail",
        "scenario": scenario,
        "observations": observations,
        "contract_refs": contract_refs,
        "artifacts": [path.as_posix() for path in artifacts or []],
        "limitations": list(limitations or SIL_LIMITATIONS),
    }


def _normal_svg(output_dir: Path) -> dict[str, Any]:
    gcode_path = output_dir / "normal_svg.gcode"
    stats = svg_to_gcode(TEST_SVG, gcode_path)
    return build_evidence(
        ok=True,
        scenario="normal_svg",
        observations=[
            {"name": "gcode_lines", "value": stats.lines},
            {"name": "gcode_bytes", "value": stats.bytes},
            {"name": "pen_lifts", "value": stats.pen_lifts},
        ],
        contract_refs=["docs/protocol.md §5", "docs/protocol.md §5.1"],
        artifacts=[gcode_path],
    )



def _kill_process_tree_on_close(child: "subprocess.Popen[str]") -> None:
    """Windows：把子进程挂进 kill-on-close job object，被杀时连孙进程一起回收。

    e2e_local.py 会 spawn grbl_sim 孙进程；`_run_scenario_cancellable` 的
    `child.kill()` 是 TerminateProcess，不跑 e2e 的 finally（那里本应对 grbl_sim
    `_stop`），孙进程会变孤儿占用 2323。job object 设 KILL_ON_JOB_CLOSE 后，job
    句柄全关时其内全部进程被 OS 终止，故 e2e 一被杀 grbl_sim 随之回收。
    POSIX 非本场景目标，直接 no-op。句柄存到 child 上阻止 GC 提前关闭。"""
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
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = kernel32.SetInformationJobObject(
        wintypes.HANDLE(job),
        JobObjectExtendedLimitInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    if not ok or not kernel32.AssignProcessToJobObject(
        wintypes.HANDLE(job), wintypes.HANDLE(int(child._handle))
    ):
        kernel32.CloseHandle(wintypes.HANDLE(job))
        return
    child._hutuji_job = job  # 阻止 GC 提前关句柄；child 释放时整树回收

def _terminate_whole_tree(child: "subprocess.Popen[str]") -> None:
    """终止子进程及其全部后代。优先 TerminateJobObject 杀 job 内整树（含 grbl_sim
    孙进程）；无 job（非 Windows 或挂接失败）时退回 child.kill() 只杀直接子进程。"""
    job = getattr(child, "_hutuji_job", None)
    if job:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.TerminateJobObject(wintypes.HANDLE(job), 1)
        kernel32.CloseHandle(wintypes.HANDLE(job))
        child._hutuji_job = None
        return
    child.kill()

def _local_stream_e2e(_output_dir: Path) -> dict[str, Any]:
    script = ROOT / "scripts" / "e2e_local.py"
    try:
        with subprocess.Popen(
            [sys.executable, str(script)],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONUTF8": "1"},
        ) as child:
            _kill_process_tree_on_close(child)
            # 注册后 async 侧才能在取消时 kill；with 块保证任何出口都关句柄。
            sink = _ACTIVE_CHILDREN.get()
            if sink is not None:
                sink.append(child)
            try:
                stdout, stderr = child.communicate(timeout=LOCAL_E2E_TIMEOUT_SECONDS)
            except (subprocess.TimeoutExpired, TimeoutError):
                child.kill()
                child.communicate()
                raise
    except (subprocess.TimeoutExpired, TimeoutError):
        return build_evidence(
            ok=False,
            scenario="local_stream_e2e",
            observations=[{"name": "error", "value": "本地闭环超时"}],
            contract_refs=["docs/protocol.md §3", "docs/protocol.md §5.1"],
        )
    returncode = child.returncode
    output = (stdout or "") + (stderr or "")
    passed = returncode == 0 and any(
        line.strip() == "PASS" for line in output.splitlines()
    )
    return build_evidence(
        ok=passed,
        scenario="local_stream_e2e",
        observations=[
            {"name": "exit_code", "value": returncode},
            {"name": "result", "value": "PASS" if passed else "FAIL"},
        ],
        contract_refs=["docs/protocol.md §3", "docs/protocol.md §5.1"],
    )


def _minimal_gcode(extra: str = "") -> str:
    return (
        "G21\nG90\nF8000\n"
        "G1G90 Z0.0F10000\nG1G90 Z0.0F10000\n"
        f"{extra}"
        "G1G90 Z0.0F10000\n"
    )


def _expect_valid(scenario: str, text: str, contract_refs: list[str]) -> dict[str, Any]:
    stats = validate_gcode(text)
    return build_evidence(
        ok=True,
        scenario=scenario,
        observations=[
            {"name": "expected", "value": "accepted"},
            {"name": "gcode_lines", "value": stats.lines},
            {"name": "gcode_bytes", "value": stats.bytes},
        ],
        contract_refs=contract_refs,
    )


def _expect_rejected(scenario: str, text: str, contract_refs: list[str]) -> dict[str, Any]:
    try:
        validate_gcode(text)
    except GcodeError as exc:
        return build_evidence(
            ok=True,
            scenario=scenario,
            observations=[
                {"name": "expected", "value": "rejected"},
                {"name": "error", "value": str(exc)},
            ],
            contract_refs=contract_refs,
        )
    return build_evidence(
        ok=False,
        scenario=scenario,
        observations=[{"name": "error", "value": "生产校验器意外放行"}],
        contract_refs=contract_refs,
    )


def _line_payload_254(_output_dir: Path) -> dict[str, Any]:
    line = ";" + "x" * (MAX_LINE_PAYLOAD_BYTES - 1)
    return _expect_valid("line_payload_254", _minimal_gcode(line + "\n"), ["docs/protocol.md §3", "docs/protocol.md §5"])


def _line_payload_255(_output_dir: Path) -> dict[str, Any]:
    line = ";" + "x" * MAX_LINE_PAYLOAD_BYTES
    return _expect_rejected("line_payload_255", _minimal_gcode(line + "\n"), ["docs/protocol.md §3", "docs/protocol.md §5"])


def _reject_g91(_output_dir: Path) -> dict[str, Any]:
    return _expect_rejected("reject_g91", _minimal_gcode("G91\n"), ["docs/protocol.md §5"])


def _reject_g92(_output_dir: Path) -> dict[str, Any]:
    return _expect_rejected("reject_g92", _minimal_gcode("G92 Z0\n"), ["docs/protocol.md §5"])


def _reject_out_of_bounds(_output_dir: Path) -> dict[str, Any]:
    return _expect_rejected(
        "reject_out_of_bounds",
        _minimal_gcode("G1 X191 Y0 F8000\n"),
        ["docs/protocol.md §5"],
    )


SCENARIOS: dict[str, dict[str, Any]] = {
    "local_stream_e2e": {
        "description": "运行既有 SVG→pipe_sim→grbl_sim 本地闭环。",
        "contract_refs": ["docs/protocol.md §3", "docs/protocol.md §5.1"],
        "limitations": SIL_LIMITATIONS,
        "runner": _local_stream_e2e,
    },
    "line_payload_254": {
        "description": "254 字节 payload 必须被生产校验器接受。",
        "contract_refs": ["docs/protocol.md §3", "docs/protocol.md §5"],
        "limitations": SIL_LIMITATIONS,
        "runner": _line_payload_254,
    },
    "line_payload_255": {
        "description": "255 字节 payload 必须被生产校验器拒绝。",
        "contract_refs": ["docs/protocol.md §3", "docs/protocol.md §5"],
        "limitations": SIL_LIMITATIONS,
        "runner": _line_payload_255,
    },
    "normal_svg": {
        "description": "测试 SVG 经 vpype、生产校验器生成合法 G-code。",
        "contract_refs": ["docs/protocol.md §5", "docs/protocol.md §5.1"],
        "limitations": SIL_LIMITATIONS,
        "runner": _normal_svg,
    },
    "reject_g91": {
        "description": "中途 G91 必须被生产校验器拒绝。",
        "contract_refs": ["docs/protocol.md §5"],
        "limitations": SIL_LIMITATIONS,
        "runner": _reject_g91,
    },
    "reject_g92": {
        "description": "下载文件中的 G92 必须被生产校验器拒绝。",
        "contract_refs": ["docs/protocol.md §5"],
        "limitations": SIL_LIMITATIONS,
        "runner": _reject_g92,
    },
    "reject_out_of_bounds": {
        "description": "X 超过 190mm 必须被生产校验器拒绝。",
        "contract_refs": ["docs/protocol.md §5"],
        "limitations": SIL_LIMITATIONS,
        "runner": _reject_out_of_bounds,
    },
}


def list_scenarios() -> list[dict[str, Any]]:
    return [
        {
            "id": scenario_id,
            "description": SCENARIOS[scenario_id]["description"],
            "contract_refs": list(SCENARIOS[scenario_id]["contract_refs"]),
            "limitations": list(SCENARIOS[scenario_id]["limitations"]),
        }
        for scenario_id in sorted(SCENARIOS)
    ]


def run_scenario(scenario_id: str, *, output_dir: Path | None = None) -> dict[str, Any]:
    entry = SCENARIOS.get(scenario_id)
    if entry is None:
        raise ValueError(f"scenario is not allowlisted: {scenario_id}")
    if output_dir is not None:
        return entry["runner"](output_dir)
    with tempfile.TemporaryDirectory(prefix="hutuji-sim-") as tmp:
        result = entry["runner"](Path(tmp))
        artifacts = [Path(path) for path in result["artifacts"]]
        if artifacts:
            artifact = artifacts[0]
            raw = artifact.read_bytes()
            result["embedded_artifact"] = {
                "name": artifact.name,
                "mime_type": "text/x-gcode",
                "text": raw.decode("ascii"),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        result["artifacts"] = []
        return result


async def _run_scenario_cancellable(scenario_id: str) -> dict[str, Any]:
    """在工作线程跑场景，但让取消能真正终止其子进程。

    `asyncio.to_thread` 不可中断：裸取消会立刻返回，而线程与它 spawn 的
    e2e 子进程继续跑到 120s 超时，占着默认执行器的线程位（上限
    `min(32, cpu+4)`），重复取消即耗尽整个进程的 to_thread 能力。
    这里先建 kill 槽，取消时先杀子进程让线程尽快退出，再等线程收尾。
    """
    children: list[subprocess.Popen[str]] = []
    token = _ACTIVE_CHILDREN.set(children)
    try:
        task = asyncio.ensure_future(asyncio.to_thread(run_scenario, scenario_id))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            for child in list(children):
                if child.poll() is None:
                    with contextlib.suppress(OSError):
                        _terminate_whole_tree(child)
            # 子进程已死 → 线程的 communicate() 立即返回，线程位随即释放。
            with contextlib.suppress(BaseException):
                await task
            raise
    finally:
        _ACTIVE_CHILDREN.reset(token)


def validate_gcode_tool(text: str) -> dict[str, Any]:
    try:
        stats = validate_gcode(text)
    except GcodeError as exc:
        return build_evidence(
            ok=False,
            scenario="validate_gcode",
            observations=[{"name": "error", "value": str(exc)}],
            contract_refs=["docs/protocol.md §5"],
        )
    return build_evidence(
        ok=True,
        scenario="validate_gcode",
        observations=[
            {"name": "gcode_lines", "value": stats.lines},
            {"name": "gcode_bytes", "value": stats.bytes},
            {"name": "pen_lifts", "value": stats.pen_lifts},
        ],
        contract_refs=["docs/protocol.md §5"],
    )


def create_server() -> Server:
    server = Server(
        SERVER_NAME,
        version=SERVER_VERSION,
        instructions=(
            "Hutuji PC SIL。结果必须保留 limitations；仿真不证明机械、云端语音、烧录或 HIL。"
        ),
    )

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        annotations = types.ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        )
        return [
            types.Tool(
                name="list_scenarios",
                description="列出固定、白名单化的 hutuji PC 仿真场景。",
                inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
                annotations=types.ToolAnnotations(readOnlyHint=True, openWorldHint=False),
            ),
            types.Tool(
                name="run_scenario",
                description="运行固定 hutuji PC 仿真场景并返回结构化证据。",
                inputSchema={
                    "type": "object",
                    "properties": {"scenario_id": {"type": "string", "enum": sorted(SCENARIOS)}},
                    "required": ["scenario_id"],
                    "additionalProperties": False,
                },
                annotations=annotations,
            ),
            types.Tool(
                name="validate_gcode",
                description="使用生产 validate_gcode() 校验 G-code，不复制合同逻辑。",
                inputSchema={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                    "additionalProperties": False,
                },
                annotations=types.ToolAnnotations(
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        if name == "list_scenarios":
            envelope: Any = {"ok": True, "scenarios": list_scenarios(), "limitations": SIL_LIMITATIONS}
        elif name == "run_scenario":
            envelope = await _run_scenario_cancellable(arguments["scenario_id"])
        elif name == "validate_gcode":
            envelope = validate_gcode_tool(arguments["text"])
        else:
            raise ValueError(f"unknown hutuji simulation tool: {name}")
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(envelope, ensure_ascii=False, indent=2))],
            structuredContent=envelope,
            isError=not bool(envelope.get("ok")),
        )

    return server


SERVER = create_server()


async def run_stdio() -> None:
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await SERVER.run(read_stream, write_stream, SERVER.create_initialization_options())


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="hutuji PC simulation MCP stdio server")
    parser.add_argument("--check", action="store_true", help="打印工具与场景，不启动 stdio")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.check:
        print(json.dumps({
            "server": SERVER_NAME,
            "version": SERVER_VERSION,
            "tools": ["list_scenarios", "run_scenario", "validate_gcode"],
            "scenarios": sorted(SCENARIOS),
            "limitations": SIL_LIMITATIONS,
        }, ensure_ascii=False, indent=2))
        return 0
    asyncio.run(run_stdio())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
