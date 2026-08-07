from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from hutuji_mcp.sim_server import (
    SCENARIOS,
    build_evidence,
    list_scenarios,
    run_scenario,
    validate_gcode_tool,
)


def test_list_scenarios_is_fixed_and_structured() -> None:
    scenarios = list_scenarios()
    assert scenarios
    assert [item["id"] for item in scenarios] == sorted(SCENARIOS)
    assert all(set(item) == {"id", "description", "contract_refs", "limitations"} for item in scenarios)


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


def test_result_is_json_serializable(tmp_path: Path) -> None:
    json.dumps(run_scenario("normal_svg", output_dir=tmp_path), ensure_ascii=False)


@pytest.mark.parametrize(
    ("scenario_id", "expected_ok"),
    [
        ("line_payload_254", True),
        ("line_payload_255", True),
        ("reject_g91", True),
        ("reject_g92", True),
        ("reject_out_of_bounds", True),
    ],
)
def test_contract_boundary_scenarios_pass(scenario_id: str, expected_ok: bool) -> None:
    result = run_scenario(scenario_id)
    assert result["ok"] is expected_ok
    assert result["status"] == "pass"
    assert result["limitations"]


def test_local_stream_e2e_runs_real_pipe_and_grbl_sim() -> None:
    result = run_scenario("local_stream_e2e")
    assert result["ok"] is True
    observations = {item["name"]: item["value"] for item in result["observations"]}
    assert observations["exit_code"] == 0
    assert observations["result"] == "PASS"


def test_validate_gcode_tool_returns_failure_as_evidence() -> None:
    result = validate_gcode_tool("G91\n")
    assert result["ok"] is False
    assert result["status"] == "fail"
    assert "error" in result["observations"][0]["name"]


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
