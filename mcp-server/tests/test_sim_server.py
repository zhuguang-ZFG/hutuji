from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from hutuji_mcp import sim_server
from hutuji_mcp.sim_server import (
    SIL_LIMITATIONS,
    build_evidence,
    create_server,
    list_scenarios,
    run_scenario,
    validate_gcode_tool,
)


def test_list_scenarios_is_fixed_and_structured() -> None:
    scenarios = list_scenarios()
    assert [item["id"] for item in scenarios] == [
        "line_payload_254",
        "line_payload_255",
        "local_stream_e2e",
        "normal_svg",
        "reject_g91",
        "reject_g92",
        "reject_out_of_bounds",
    ]
    assert all(set(item) == {"id", "description", "contract_refs", "limitations"} for item in scenarios)
    assert all(item["limitations"] == SIL_LIMITATIONS for item in scenarios)


def test_evidence_envelope_preserves_limitations() -> None:
    result = build_evidence(
        ok=True,
        scenario="demo",
        observations=[{"name": "lines", "value": 2}],
        contract_refs=["docs/protocol.md §5"],
        artifacts=[Path("results/demo.json")],
    )
    assert result["ok"] is True
    assert result["status"] == "pass"
    assert result["scenario"] == "demo"
    assert result["observations"][0]["value"] == 2
    assert result["artifacts"] == ["results/demo.json"]
    assert result["limitations"]


def test_run_scenario_rejects_unknown_id() -> None:
    with pytest.raises(ValueError, match="allowlisted"):
        run_scenario("not-registered")


def test_run_scenario_normal_svg_produces_real_evidence(tmp_path: Path) -> None:
    result = run_scenario("normal_svg", output_dir=tmp_path)
    assert result["ok"] is True
    assert result["status"] == "pass"
    assert result["observations"][0]["name"] == "gcode_lines"
    assert result["artifacts"]
    assert all(Path(path).exists() for path in result["artifacts"])


def test_run_scenario_normal_svg_embeds_verifiable_artifact() -> None:
    result = run_scenario("normal_svg")
    artifact = result["embedded_artifact"]
    assert artifact["name"] == "normal_svg.gcode"
    assert artifact["mime_type"] == "text/x-gcode"
    assert artifact["text"].replace("\r\n", "\n").startswith("G21\nG90\nF8000\n")
    assert len(artifact["sha256"]) == 64


def test_result_is_json_serializable(tmp_path: Path) -> None:
    json.dumps(run_scenario("normal_svg", output_dir=tmp_path), ensure_ascii=False)


@pytest.mark.parametrize(
    ("scenario_id", "error_fragment"),
    [
        ("line_payload_255", "255 字节超上限 254"),
        ("reject_g91", "G91"),
        ("reject_g92", "G92"),
        ("reject_out_of_bounds", "X191.0 越界"),
    ],
)
def test_rejection_scenarios_prove_their_specific_contract(
    scenario_id: str, error_fragment: str,
) -> None:
    result = run_scenario(scenario_id)
    observations = {item["name"]: item["value"] for item in result["observations"]}
    assert result["ok"] is True
    assert result["status"] == "pass"
    assert observations["expected"] == "rejected"
    assert error_fragment in observations["error"]
    assert result["limitations"] == SIL_LIMITATIONS


def test_line_payload_254_is_accepted() -> None:
    result = run_scenario("line_payload_254")
    observations = {item["name"]: item["value"] for item in result["observations"]}
    assert result["ok"] is True
    assert observations["expected"] == "accepted"


def test_local_stream_e2e_runs_real_pipe_and_grbl_sim() -> None:
    result = run_scenario("local_stream_e2e")
    assert result["ok"] is True
    observations = {item["name"]: item["value"] for item in result["observations"]}
    assert observations["exit_code"] == 0
    assert observations["result"] == "PASS"


def test_local_stream_e2e_timeout_is_structured_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    # 超时现由 Popen.communicate(timeout=…) 抛出（改用 Popen 是为了让取消可
    # 杀子进程，见 sim_server._run_scenario_cancellable）；契约不变：结构化失败。
    def timeout(self, *_args, **_kwargs):
        raise sim_server.subprocess.TimeoutExpired(cmd="e2e_local.py", timeout=1)

    monkeypatch.setattr(sim_server.subprocess.Popen, "communicate", timeout)
    result = run_scenario("local_stream_e2e")
    observations = {item["name"]: item["value"] for item in result["observations"]}
    assert result["ok"] is False
    assert observations["error"] == "本地闭环超时"


def test_e2e_child_job_handle_is_released_on_normal_path() -> None:
    """正常跑完也必须关 job 句柄。

    修前只有取消路径关句柄，实测每次运行泄漏 1 个内核句柄（3 次运行 delta=3）；
    更要紧的是 KILL_ON_JOB_CLOSE 要等句柄全关才终止残留进程，靠 GC 回收时机不可知。
    """
    captured: list[object] = []
    real_popen = sim_server.subprocess.Popen

    class Spy(real_popen):  # type: ignore[misc,valid-type]
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)
            captured.append(self)

    sim_server.subprocess.Popen = Spy  # type: ignore[misc]
    try:
        result = run_scenario("local_stream_e2e")
    finally:
        sim_server.subprocess.Popen = real_popen  # type: ignore[misc]

    assert result["ok"] is True
    assert captured, "未捕获到 e2e 子进程"
    # 句柄已被 _release_job 置空；Windows 下挂接成功过，非 Windows 下从未挂接。
    assert getattr(captured[0], "_hutuji_job", None) is None


def test_local_stream_e2e_timeout_kills_whole_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """超时路径必须整树终止，不能只 kill 直接子进程。

    e2e_local.py 自己 spawn grbl_sim 孙进程（e2e_local.py:108）。child.kill() 是
    TerminateProcess，不跑 e2e 的 finally，grbl_sim 会成孤儿永占 2323，之后所有
    e2e 全挂。修前实测 ORPHAN_ALIVE=True。
    """
    tree_kills: list[object] = []
    direct_kills: list[object] = []
    real_terminate = sim_server._terminate_whole_tree
    real_kill = sim_server.subprocess.Popen.kill

    def spy_terminate(child: object) -> None:
        tree_kills.append(child)
        real_terminate(child)

    def spy_kill(self) -> None:
        # 只记录「绕过整树终止的直杀」；_terminate_whole_tree 的非 Windows 回退
        # 也会走到这里，故用 tree_kills 是否已记录来区分。
        if not tree_kills:
            direct_kills.append(self)
        real_kill(self)

    def timeout(self, *_args: object, **_kwargs: object):
        raise sim_server.subprocess.TimeoutExpired(cmd="e2e_local.py", timeout=1)

    monkeypatch.setattr(sim_server, "_terminate_whole_tree", spy_terminate)
    monkeypatch.setattr(sim_server.subprocess.Popen, "kill", spy_kill)
    monkeypatch.setattr(sim_server.subprocess.Popen, "communicate", timeout)

    result = run_scenario("local_stream_e2e")
    observations = {item["name"]: item["value"] for item in result["observations"]}
    assert result["ok"] is False
    assert observations["error"] == "本地闭环超时"
    assert tree_kills, "超时路径未走整树终止，grbl_sim 孙进程会成孤儿永占 2323"
    assert not direct_kills, "超时路径绕过整树终止直接 kill 子进程"


def test_cancelled_scenario_kills_child_and_releases_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """取消不只取消 await；必须杀子进程，让工作线程立即归还执行器。"""
    processes = []

    def blocking_runner(_output_dir: Path):
        child = sim_server.subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"]
        )
        processes.append(child)
        sink = sim_server._ACTIVE_CHILDREN.get()
        assert sink is not None
        sink.append(child)
        child.wait()
        return build_evidence(
            ok=False,
            scenario="cancel_probe",
            observations=[],
            contract_refs=[],
        )

    monkeypatch.setitem(sim_server.SCENARIOS, "cancel_probe", {"runner": blocking_runner})

    async def exercise() -> None:
        task = asyncio.create_task(sim_server._run_scenario_cancellable("cancel_probe"))
        while not processes:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)

    asyncio.run(exercise())
    assert processes[0].returncode is not None


def test_validate_gcode_tool_returns_specific_failure_as_evidence() -> None:
    result = validate_gcode_tool(sim_server._minimal_gcode("G91\n"))
    assert result["ok"] is False
    assert result["status"] == "fail"
    assert "G91" in result["observations"][0]["value"]


def test_mcp_schema_and_unknown_tools_are_rejected() -> None:
    async def exercise() -> None:
        from mcp.shared.memory import create_connected_server_and_client_session

        async with create_connected_server_and_client_session(create_server()) as session:
            missing = await session.call_tool("run_scenario", {})
            assert missing.isError is True
            assert "required property" in missing.content[0].text

            extra = await session.call_tool("list_scenarios", {"extra": True})
            assert extra.isError is True
            assert "Additional properties" in extra.content[0].text

            unknown = await session.call_tool("not_registered", {})
            assert unknown.isError is True
            assert "unknown hutuji simulation tool" in unknown.content[0].text

            rejected = await session.call_tool(
                "validate_gcode", {"text": sim_server._minimal_gcode("G91\n")},
            )
            assert rejected.isError is True
            assert rejected.structuredContent["ok"] is False
            assert "G91" in rejected.content[0].text

    asyncio.run(exercise())


def test_stdio_mcp_initialize_list_and_call() -> None:
    async def exercise() -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        env.setdefault("PYTHONUTF8", "1")
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "hutuji_mcp.sim_server"],
            cwd=str(Path(__file__).resolve().parents[1]),
            env=env,
        )
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                assert initialized.serverInfo.name == "hutuji-pc-simulation"
                tools = await session.list_tools()
                assert {tool.name for tool in tools.tools} == {
                    "list_scenarios",
                    "run_scenario",
                    "validate_gcode",
                }
                called = await session.call_tool("run_scenario", {"scenario_id": "line_payload_254"})
                assert called.isError is False
                assert called.structuredContent["ok"] is True
                assert called.structuredContent["limitations"]

    asyncio.run(exercise())
