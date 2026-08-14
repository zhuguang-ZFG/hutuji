from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import socket
import sys
import threading
import time
from collections.abc import Callable
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


def test_local_stream_e2e_runs_real_pipe_and_grbl_sim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 固定 2323 会被上次崩溃遗留的孤儿 grbl_sim 占用（R11-TQ-03），注入随机
    # 空闲端口让本用例对 2323 残留免疫；e2e_local 的 _sim_port 读此环境变量。
    monkeypatch.setenv("GRBL_SIM_PORT", str(_free_port()))
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


def test_e2e_timeout_matches_gate_run_e2e_timeout() -> None:
    """R11-SIM-02：sim_server 本地闭环超时与门禁 run_e2e 层是同一时限。

    同一 e2e_local.py 有 sim_server 与门禁两个入口；时限分岔会让同一闭环
    两入口结论不同（合法慢场景在一侧判超时、另一侧放行）。门禁是带 argparse
    副作用的脚本，禁 import，按同仓先例读源码文本钉死（test_contract.py 的
    preflight 形状钉法同构）。"""
    gate_src = (
        Path(__file__).resolve().parents[2] / "scripts" / "agent_gate.py"
    ).read_text(encoding="utf-8")
    # run_e2e 现以 child.communicate(timeout=…) 表达时限（R11-E2E-02 把
    # subprocess.run 重构为 Popen + 整树杀）；钉住 run_e2e 定义后的第一处。
    call = re.search(r"def run_e2e\(\)", gate_src)
    assert call, "agent_gate.py 找不到 run_e2e 定义"
    timeout_m = re.search(r"communicate\(timeout=(\d+)\)", gate_src[call.end():])
    assert timeout_m, "run_e2e 缺 communicate(timeout=…) 调用"
    # 双重钉死：与门禁相等（防两入口分岔），绝对值 300（防两处同步漂移）。
    assert sim_server.LOCAL_E2E_TIMEOUT_SECONDS == int(timeout_m.group(1)) == 300


def _free_port() -> int:
    """找空闲端口注入 GRBL_SIM_PORT，避免固定 2323 被孤儿残留占用时非确定误红。

    同 test_contract.py 的 test_e2e_rejects_occupied_simulator_port 用 setenv 注入的
    先例；端口在 bind 后短暂释放，竞争窗口与「自选随机端口」同级。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_e2e_child_job_handle_is_released_on_normal_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """正常跑完也必须关 job 句柄。

    修前只有取消路径关句柄，实测每次运行泄漏 1 个内核句柄（3 次运行 delta=3）；
    更要紧的是 KILL_ON_JOB_CLOSE 要等句柄全关才终止残留进程，靠 GC 回收时机不可知。
    """
    monkeypatch.setenv("GRBL_SIM_PORT", str(_free_port()))
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
    monkeypatch.setenv("GRBL_SIM_PORT", str(_free_port()))

    result = run_scenario("local_stream_e2e")
    observations = {item["name"]: item["value"] for item in result["observations"]}
    assert result["ok"] is False
    assert observations["error"] == "本地闭环超时"
    assert tree_kills, "超时路径未走整树终止，grbl_sim 孙进程会成孤儿永占 2323"
    assert not direct_kills, "超时路径绕过整树终止直接 kill 子进程"


def _run_with_reaped_children(coro_factory, processes, *, timeout: float = 5.0) -> None:
    """跑一段 async 用例，无论结果如何都杀掉测试自己 spawn 的真子进程。

    这些用例故意起 `time.sleep(60)` 验证回收路径。若被测取消逻辑回归，旧写法
    `asyncio.run(exercise())` 会从「判红」退化成「永久挂起」——无人值守时表现为
    整轮超时而非某项失败，且 sleep(60) 子进程留在机器上。故整段套 wait_for，
    并在 finally 无条件收尸。
    """

    async def _bounded() -> None:
        await asyncio.wait_for(coro_factory(), timeout=timeout)

    try:
        asyncio.run(_bounded())
    finally:
        for child in processes[:]:
            if child.poll() is None:
                with contextlib.suppress(ProcessLookupError, OSError):
                    child.kill()


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
        # 不能裸自旋：runner 若因回归始终不 spawn，`while not processes` 会把用例
        # 挂死在这一行而不是判红。
        deadline = asyncio.get_running_loop().time() + 3.0
        while not processes:
            assert asyncio.get_running_loop().time() < deadline, "子进程未被 spawn"
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)

    _run_with_reaped_children(exercise, processes)
    # 空列表时 `processes[0]` 会抛 IndexError 盖住真实失败原因，先显式断言。
    assert processes, "未捕获到 spawn 的子进程"
    assert processes[0].returncode is not None


def test_validate_gcode_tool_returns_specific_failure_as_evidence() -> None:
    result = validate_gcode_tool(sim_server._minimal_gcode("G91\n"))
    assert result["ok"] is False
    assert result["status"] == "fail"
    assert "G91" in result["observations"][0]["value"]


def test_validate_gcode_call_keeps_event_loop_responsive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R11-SIM-01：validate_gcode 工具的 CPU 绑定校验不得阻塞事件循环。

    512KiB 载荷的解析在事件循环内同步跑会占死全部并发 handler（对照 run_scenario
    走 to_thread）。用「阻塞若干毫秒」的假校验器 + 同循环空转协程测「校验期间事件
    循环是否仍推进」：同步实现下空转零推进（RED），to_thread 实现下主循环照常
    推进（GREEN）。校验器在另一线程阻塞，主线程据此采样，避免测试自身也卡死。
    """
    from mcp.shared.memory import create_connected_server_and_client_session

    entered = threading.Event()
    release = threading.Event()
    real = sim_server.validate_gcode_tool

    def blocking_validator(text: str) -> dict[str, object]:
        entered.set()
        assert release.wait(10.0), "阻塞校验器未被放行"
        return real(text)

    monkeypatch.setattr(sim_server, "validate_gcode_tool", blocking_validator)

    ticks = {"n": 0}
    outcome: dict[str, object] = {}

    async def ticker() -> None:
        while True:
            ticks["n"] += 1
            await asyncio.sleep(0)

    async def exercise() -> None:
        async with create_connected_server_and_client_session(create_server()) as session:
            tick = asyncio.create_task(ticker())
            try:
                call = asyncio.create_task(
                    session.call_tool(
                        "validate_gcode",
                        {"text": sim_server._minimal_gcode("G1 X10 Y10 F8000\n")},
                    ),
                )
                await asyncio.to_thread(entered.wait, 10.0)
                # 让事件循环空转一段时间：同步实现下这段时间主循环被校验器占死，
                # 空转协程零推进；to_thread 实现下主循环持续推进。
                await asyncio.sleep(0.2)
                outcome["result"] = await asyncio.wait_for(call, timeout=10.0)
                outcome["done"] = True
            finally:
                release.set()
                tick.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await tick

    thread = threading.Thread(target=lambda: asyncio.run(exercise()), daemon=True)
    thread.start()
    try:
        assert entered.wait(10.0), "假校验器未进入阻塞点"
        ticks_before = ticks["n"]
        time.sleep(0.2)
        ticks_after = ticks["n"]
    finally:
        release.set()
        thread.join(timeout=15.0)
    assert not thread.is_alive(), "exercise 未在时限内结束"
    assert outcome.get("done"), "validate_gcode 调用未返回"
    result = outcome["result"]
    assert result.structuredContent["ok"] is True  # type: ignore[union-attr]
    assert ticks_after > ticks_before, (
        "校验期间事件循环未推进——validate_gcode 同步阻塞了事件循环"
    )


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

    # 内存会话本不该慢，但一旦 create_connected_server_and_client_session 或某个
    # call_tool 因回归卡住，裸 asyncio.run 会永久挂起而非判红。
    asyncio.run(asyncio.wait_for(exercise(), timeout=15))


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


# ---------- 第九轮修复：grbl_sim §5 契约边界 / error 语义 / 独占与空闲超时 ----------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("G0 X0 Y0", "error:20"),          # 回原点触发自动换纸（§5 最高危文件缺陷）
        ("G0 X0Y0Z0", "error:20"),
        ("G0 X0.001Y0.001F8000", "error:20"),
        ("G92 Z0", "error:20"),
        ("M3", "error:20"),
        ("M5", "error:20"),
        ("M30", "error:20"),               # M30 须由 S3 旁路单发，不进文件
        ("G28", "error:20"),
        ("G30", "error:20"),
        ("G4 P0.2", "error:20"),
        ("G53 G1 X0", "error:20"),
        ("G20", "error:20"),
        ("G1 X191 Y10 F8000", "error:20"),  # 越出 §5 契约值 190
        ("G1 X-1 Y10 F8000", "error:20"),   # 负 X：本机无限位开关，MCP 限幅唯一防线
        ("G1 X10 Y278 F8000", "error:20"),
        ("G1G90 Z7.0F10000", "error:20"),   # Z 只允许 0/5
        ("G1 X1 Y1 F0", "error:20"),
        ("G1 X1 Y1 F-1", "error:20"),
        ("G1 X1 Y1 F10001", "error:20"),
        ("G1 X1e400 Y1 F8000", "error:20"),  # 非有限数值
        ("G1 X1.5.5 Y1 F8000", "error:20"),  # 畸形坐标
        ("G1 X10 Y10 Q5 F8000", "error:20"),  # 未知字母
    ],
)
def test_grbl_sim_contract_boundary_rejects_forbidden_lines(line: str, expected: str) -> None:
    """R9-SIM-01：§5/§3 禁语与越界必须在设备边界回 error，使 e2e 能在
    validate_gcode 回归时立即失败（协议回归网，非逐字节真机仿真）。"""
    from hutuji_mcp import grbl_sim

    refusal = grbl_sim._contract_violation(line)
    assert refusal == expected, f"{line!r} 应回 {expected}，实际 {refusal!r}"


@pytest.mark.parametrize(
    "line",
    [
        "G21",
        "G90",
        "F8000",
        "G1G90 Z0.0F10000",
        "G1G90 Z5.0F10000",
        "G0 X5.5Y3.2F8000",
        "G1 X10.0Y10.0F8000",
        "G0 X190Y277F8000",       # 契约边界值允许
        "G1 X0Y277F8000",
        "G2 X10 Y10 I5 J0 F8000",  # 圆弧白名单内；包围盒不建模（validate_gcode 覆盖）
        "G1 X10 Y10 F8000 ;move",  # 行内注释剥离
        ";only comment",
        "",
    ],
)
def test_grbl_sim_contract_boundary_accepts_valid_lines(line: str) -> None:
    """合法生产 profile 形状必须放行（否则 e2e 假 FAIL）。"""
    from hutuji_mcp import grbl_sim

    assert grbl_sim._contract_violation(line) is None, f"{line!r} 不应被拒"


def test_grbl_sim_payload_length_boundary_is_254_bytes() -> None:
    """R9-SIM-01：payload 254 字节放行、255 字节回 error:11（§3 off-by-one）。"""
    from hutuji_mcp import grbl_sim

    # 用 `;` 注释垫长：payload 按设备收到的整行字节计（§3），注释字节也算
    prefix = "G1 X1 Y1 ;"
    assert grbl_sim._contract_violation(prefix + "0" * (254 - len(prefix))) is None
    assert grbl_sim._contract_violation(prefix + "0" * (255 - len(prefix))) == "error:11"


def test_grbl_sim_non_ascii_replies_error11() -> None:
    """F-SIM-02：≥0x80 多字节序列（如中文）仍按 §5/§3 契约回 error:11——
    真机把它们当实时字符提取（Serial.cpp:240）不产生 error:11，仿真差异有意
    （回归网钉文件契约）。单字节实时字符 0x85 走实时旁路静默（F-SIM-01，
    见 test_grbl_sim_realtime_characters_are_silent 对照）。"""
    from hutuji_mcp import grbl_sim

    assert grbl_sim._contract_violation("G1 X10 Y10 中文 F8000") == "error:11"


async def _drive_sim(
    payloads: list[bytes],
    *,
    paper_delay_s: float = 0.3,
    idle_timeout_s: float = 5.0,
    no_paper: bool = False,
    read_timeout_s: float = 5.0,
    resume: Callable[[list[str]], bytes | None] | None = None,
) -> list[str]:
    """起内存版 handle_client，连真实 stream 对，发 payload、读到 EOF/超时。

    resume：显式同步点钩子——读循环每轮先调用 resume(已收响应)，返回非空
    bytes 就写入再继续读。用于「先等 M30 窗口结束应答、再发下一行」这类
    不依赖真实睡眠时序的确定性编排（见换纸窗口两个用例）。"""
    from hutuji_mcp import grbl_sim

    async def on_connect(reader, writer):
        await grbl_sim.handle_client(
            reader, writer,
            paper_delay_s=paper_delay_s,
            idle_timeout_s=idle_timeout_s,
            no_paper=no_paper,
        )

    server = await asyncio.start_server(on_connect, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        await reader.readline()  # banner 首行（\r\n 空行）
        banner = await reader.readline()
        assert b"Grbl" in banner, banner
        for payload in payloads:
            writer.write(payload)
        await writer.drain()
        responses: list[str] = []
        try:
            while True:
                if resume is not None:
                    extra = resume(responses)
                    if extra:
                        writer.write(extra)
                        await writer.drain()
                line = await asyncio.wait_for(reader.readline(), timeout=read_timeout_s)
                if not line:
                    break
                responses.append(line.decode(errors="replace").strip())
        except asyncio.TimeoutError:
            responses.append("<client-timeout>")
        return responses
    finally:
        writer.close()
        await writer.wait_closed()
        server.close()
        await server.wait_closed()


def test_grbl_sim_question_mark_replies_idle_status() -> None:
    """F-SIM-01：'?' 实时状态查询走旁路立即回 Idle 状态行（真机 Serial.cpp:239-
    263 is_realtime_command；protocol.md §3：应答只回发送 client），且不吃 ok——
    修前 '?' 落进 _contract_violation 被回伪造 ok。"""
    responses = asyncio.run(_drive_sim([b"?\n"], read_timeout_s=0.3))
    assert responses[0] == "<Idle|MPos:0.000,0.000,0.000|Bf:249,1200|FS:0,0>", responses
    assert "ok" not in responses, responses


@pytest.mark.parametrize("char", [b"!", b"~", b"\x18", b"\x85"])
def test_grbl_sim_realtime_characters_are_silent(char: bytes) -> None:
    """F-SIM-01：'!' 急停复位 / '~' 门控暂停 / 0x18 CAN / 0x85 保留实时字节
    静默吸收不产生应答（修前都落契约判定被回伪造 ok）。0x85 是 ≥0x80 单字节
    实时字符，必须在 UTF-8 解码前按字节判定（F-SIM-02 对照：多字节中文仍回
    error:11）。"""
    responses = asyncio.run(_drive_sim([char + b"\n"], read_timeout_s=0.3))
    assert responses == ["<client-timeout>"], responses  # 唯一应答是客户端读超时标记


def test_grbl_sim_realtime_bypasses_contract() -> None:
    """F-SIM-01：实时旁路先于契约判定——'?' 行首即使带越界内容也只回状态行，
    不产生 error:20/error:11。"""
    responses = asyncio.run(_drive_sim([b"?G1 X191 Y10 F8000\n"], read_timeout_s=0.3))
    assert responses[0] == "<Idle|MPos:0.000,0.000,0.000|Bf:249,1200|FS:0,0>", responses
    assert "error:20" not in responses, responses


def test_grbl_sim_m300_is_not_paper_change() -> None:
    """F-SIM-04：M30 判定按 M 字整数值精确匹配（真机 GCode.cpp:486-488
    int_value==30）——startswith("M30") 会把 M300/M301 误判为换纸命令。
    M300 是非法词法，应走契约判定回 error:20，而非触发换纸窗口。"""
    responses = asyncio.run(_drive_sim([b"M300\n"], paper_delay_s=0.3, read_timeout_s=1.0))
    assert "error:20" in responses, responses
    assert "[MSG:[PaperStatus]" not in " ".join(responses), responses
    assert "error:90" not in responses, responses


@pytest.mark.parametrize("line", [b"M030\n", b"  M30 \n", b"M 30\n"])
def test_grbl_sim_m30_with_padding_triggers_paper_change(line: bytes) -> None:
    """F-SIM-04：行内空白与前导零不影响判定——真机入口 collapseGCode
    （GCode.cpp:75-130）剥空白转大写后 'M 30'/'M030' 同为整数值 30，仍触发换纸建模。"""
    responses = asyncio.run(_drive_sim([line]))
    assert "[MSG:[PaperStatus] 0]" in responses, responses
    assert "ok" in responses, responses


def test_grbl_sim_m30_no_paper_replies_error90() -> None:
    """R9-SIM-02 / F-SIM-05：GRBL_SIM_NO_PAPER 建模缺纸——M30 先发
    [PaperStatus] 5（OUT_OF_PAPER，真机 PaperSystem.cpp:804/811 发 CLIENT_ALL）
    再回 error:90（§7 换纸失败，protocol.md §7:243 按该码区分失败原因）。
    修前只回裸 error:90，缺纸与其它失败无法按码区分。"""
    responses = asyncio.run(_drive_sim([b"M30\n"], no_paper=True))
    assert responses[0] == "[MSG:[PaperStatus] 5]", responses
    assert "error:90" in responses, responses
    assert (
        responses.index("[MSG:[PaperStatus] 5]") < responses.index("error:90")
    ), responses


def test_grbl_sim_m30_normal_replies_paper_status_then_ok() -> None:
    responses = asyncio.run(_drive_sim([b"M30\n"]))
    assert "[MSG:[PaperStatus] 0]" in responses, responses
    assert "ok" in responses, responses

def test_grbl_sim_empty_line_replies_ok() -> None:
    """空行回 ok：真机 execute_line() 对 line[0]==0 直接 Error::Ok
    （Protocol.cpp:84-86，§3 CRLF 双 ok 同因）；此前仿真静默丢弃，
    手工 Telnet 敲回车会无任何应答。"""
    responses = asyncio.run(_drive_sim([b"\nG0 X10 Y10 F8000\n\n"], read_timeout_s=0.3))
    assert responses[0] == "ok", responses  # 首个空行
    assert responses[1] == "ok", responses  # G0 行
    assert responses[2] == "ok", responses  # 第二个空行


def test_grbl_sim_motion_buffered_during_paper_window_replies_error8() -> None:
    """R9-SIM-02：换纸窗口内已到达的运动行回 error:8（§7：该行被丢弃，须重发）。

    两行同包发送：M30 处理期间 G1 已缓冲在服务端——「窗口内到达」按构造成立，
    不依赖调度时序；窗口放宽到 2s、客户端读超时放宽到 10s，只为给慢机留足
    读应答余量（R11-TQ-02），判据不变：窗口内运动行必须回 error:8。

    真机对窗口内每条运动行回 error:8，且可经 protocol_
    service_during_blocking 先于 M30 的 ok 发出（Protocol.cpp:166-174,228-256）；
    仿真在 M30 应答后才统一处置缓冲行，应答顺序差异有意。行分类口径已与真机
    逐字对齐（R13-SIM-07）：行首 G 字首位数 0-3 即运动行。"""
    responses = asyncio.run(
        _drive_sim(
            [b"M30\nG1 X10 Y10 F8000\n"],
            paper_delay_s=2.0,
            idle_timeout_s=1.0,
            read_timeout_s=10.0,
        )
    )
    assert "error:8" in responses, responses



def test_grbl_sim_g21_buffered_during_paper_window_replies_error8() -> None:
    """R13-SIM-07：换纸窗口内的 'G21' 真机按运动行回 error:8（行首 G 字首位数
    '2' ∈ 0-3，Protocol.cpp:110-126）；旧仿真按整值 0-3 判定会漏成 ok。"""
    responses = asyncio.run(
        _drive_sim([b"M30\nG21\n"], paper_delay_s=2.0, read_timeout_s=10.0)
    )
    assert "error:8" in responses, responses


def test_grbl_sim_motion_after_paper_window_is_accepted() -> None:
    """窗口结束后到达的运动行正常 ok（对 error:8 建模的对照）。

    R11-TQ-02：旧版只发 M30、从不发运动行，「error:8 not in responses」恒真、
    无判别力。改为显式同步点：先等 M30 的 PaperStatus+ok（窗口已结束）再发 G1，
    不依赖真实睡眠时序——慢机也不会把窗口后到达误判为窗口内。"""
    sent = {"g1": False}

    def resume(responses: list[str]) -> bytes | None:
        if not sent["g1"] and "[MSG:[PaperStatus] 0]" in responses and "ok" in responses:
            sent["g1"] = True
            return b"G1 X10 Y10 F8000\n"
        return None

    responses = asyncio.run(
        _drive_sim([b"M30\n"], paper_delay_s=0.3, idle_timeout_s=1.0, resume=resume)
    )
    assert sent["g1"], "未等到 M30 窗口结束应答，G1 未发出"
    assert "error:8" not in responses, responses
    # M30 自己的 ok + G1 的 ok 各一条：证明 G1 确实被放行而非「压根没发」。
    assert responses.count("ok") == 2, responses


def test_grbl_sim_survives_m30_probe_cancel() -> None:
    """R11-SIM-03：M30 窗口无缓冲行时 probe 取消不得弄死 handler。

    CPython 的 Task.cancel 只排程取消、不立即清 reader._waiter；旧实现
    probe.cancel() 后直接下一轮 readline 会抛 RuntimeError 使整个 handler
    崩溃——M30 之后任何合法客户端续发都会撞 EOF。这里先等 M30 的
    PaperStatus+ok（窗口空、必走 probe-cancel 路径）再用 `$I` 探活：
    证明 handler 存活且能继续正确处理下一行。对 revert（去掉 await probe）
    必须判红。"""
    sent = {"probe": False}

    def resume(responses: list[str]) -> bytes | None:
        if not sent["probe"] and "[MSG:[PaperStatus] 0]" in responses and "ok" in responses:
            sent["probe"] = True
            return b"$I\n"
        return None

    responses = asyncio.run(
        _drive_sim([b"M30\n"], paper_delay_s=0.3, idle_timeout_s=1.0, resume=resume)
    )
    assert sent["probe"], "未等到 M30 完成应答，$I 未发出"
    assert "[VER:1.3a-hutuji-sim" in " ".join(responses), responses
    # M30 的 ok + $I 的 ok 各一条：证明 $I 被正常应答而非「连接已断」。
    assert responses.count("ok") == 2, responses


def test_grbl_sim_idle_timeout_closes_stale_connection() -> None:
    """R9-SIM-04：空闲超时回收（真机 keepalive ~20s；仿真参数化便于测试）。"""
    responses = asyncio.run(_drive_sim([], idle_timeout_s=0.2))
    assert responses == [], responses  # 对端直接 EOF，无任何应答


def test_grbl_sim_rejects_second_client() -> None:
    """R9-SIM-04：MAX_TLNT_CLIENTS=1 建模——第二客户端不发 banner 立即断开。"""
    from hutuji_mcp import grbl_sim

    async def exercise() -> None:
        server = await grbl_sim.serve(0)
        port = server.sockets[0].getsockname()[1]
        try:
            r1, w1 = await asyncio.open_connection("127.0.0.1", port)
            await r1.readline()
            banner1 = await r1.readline()
            assert b"Grbl" in banner1
            r2, w2 = await asyncio.open_connection("127.0.0.1", port)
            # 第二客户端被立即断开：读不到 banner，EOF
            got = await asyncio.wait_for(r2.readline(), timeout=3.0)
            assert got == b"", f"第二客户端应被立即断开，实际收到 {got!r}"
            w1.close()
            w2.close()
            await w1.wait_closed()
            await w2.wait_closed()
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(exercise())


# ---------- 第九轮修复：pipe_sim 行内注释 / error:8 重发 ----------


def test_pipe_sim_strips_inline_comments() -> None:
    """R9-SIM-06：`;` 行内注释必须转发前剥掉（§3）；整行注释剥成空。"""
    from hutuji_mcp import pipe_sim

    assert pipe_sim._clean_gcode_line("G1 X10 Y10 F8000 ;move") == "G1 X10 Y10 F8000"
    assert pipe_sim._clean_gcode_line("  ;x") == ""
    assert pipe_sim._clean_gcode_line("(full line)") == ""
    assert pipe_sim._clean_gcode_line("G1 X1 Y1 (note) F8000") == "G1 X1 Y1  F8000"
    assert pipe_sim._clean_gcode_line("") == ""


class _ScriptedGrbl:
    """按脚本回行的假对端：banner 之后每收一行按队列回一行（可为多行片段）。"""

    def __init__(self, script: list[list[str]]) -> None:
        self.script = script
        self.seen = 0
        self.server = None

    async def handle(self, reader, writer):
        writer.write(b"\r\nGrbl 1.3a ['$' for help]\r\n")
        await writer.drain()
        while True:
            data = await reader.readline()
            if not data:
                break
            if self.seen >= len(self.script):
                writer.write(b"ok\r\n")
            else:
                for resp in self.script[self.seen]:
                    writer.write((resp + "\r\n").encode())
                self.seen += 1
            await writer.drain()
        writer.close()


def _run_pipe_sim_against(script: list[list[str]], *, delay_s: float = 0.01, tmp_path: Path):
    from hutuji_mcp import pipe_sim

    fake = _ScriptedGrbl(script)

    async def exercise():
        server = await asyncio.start_server(fake.handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        gcode = tmp_path / "job.gcode"
        gcode.write_text("G1 X10 Y10 F8000\n", encoding="utf-8")
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                None,
                lambda: pipe_sim.stream_gcode(
                    gcode, "127.0.0.1", port,
                    deferred_retry_delay_s=delay_s,
                ),
            )
        finally:
            server.close()
            await server.wait_closed()

    return asyncio.run(exercise())


def test_pipe_sim_retries_error8_then_succeeds(tmp_path: Path) -> None:
    """R9-SIM-03：error:8 → 2s 后原地重发同一行（§7），成功即整单 ok。"""
    result = _run_pipe_sim_against([["error:8"], ["ok"]], tmp_path=tmp_path)
    assert result.success, (result.failed_line, result.error_response)
    assert result.ok_lines == result.total_lines == 1


def test_pipe_sim_error8_exhausted_fails(tmp_path: Path) -> None:
    """error:8 重试超过上限（8 次）必须失败闭合，不得无限重发。"""
    result = _run_pipe_sim_against([["error:8"]] * 10, tmp_path=tmp_path)
    assert not result.success
    assert result.failed_line == 1
    assert result.error_response == "error:8"


def test_pipe_sim_other_error_stops_immediately(tmp_path: Path) -> None:
    """非 error:8 的 error 仍立即整单终止（原契约不回归）。"""
    result = _run_pipe_sim_against([["error:20"]], tmp_path=tmp_path)
    assert not result.success
    assert result.failed_line == 1
    assert result.error_response == "error:20"


def test_pipe_sim_alarm_is_structured_failure(tmp_path: Path) -> None:
    """F-SIM-10：真机运动期间软限位触发异步 'ALARM:2'（Report.cpp:205-207
    report_alarm_message 发 CLIENT_ALL），此后 ok 永不返回——修前把 ALARM 行
    吞进 info_lines 继续等 ok，挂满 60s 超时变成非结构化失败。ALARM: 前缀行
    必须结构化终止，且失败信息携带该行内容。"""
    result = _run_pipe_sim_against([["ALARM:2"]], tmp_path=tmp_path)
    assert not result.success
    assert result.failed_line == 1
    assert result.error_response == "ALARM:2"


def test_pipe_sim_status_lines_are_tolerated(tmp_path: Path) -> None:
    """F-SIM-10：'<...>' 实时状态行不是失败信号（Report.cpp 状态报告与行应答
    并发，ok 仍会返回），继续容忍吞入 info_lines——与 ALARM: 的差异有意：
    ALARM 后 ok 永不返回，状态行后 ok 照常。"""
    result = _run_pipe_sim_against(
        [["<Idle|MPos:0.000,0.000,0.000|Bf:249,1200|FS:0,0>", "ok"]],
        tmp_path=tmp_path,
    )
    assert result.success, (result.failed_line, result.error_response)
    assert result.ok_lines == result.total_lines == 1
    assert any("<Idle|MPos:0.000" in line for line in result.info_lines), result.info_lines


# ---------- 第九轮修复：并发 e2e 端口互斥 ----------


def test_local_stream_e2e_busy_when_lock_held() -> None:
    """R9-SIM-05：并发调用收敛为确定性 busy 结果，而非撞 2323 的非确定失败。"""
    assert sim_server._E2E_LOCK.acquire(blocking=False)
    try:
        result = run_scenario("local_stream_e2e")
    finally:
        sim_server._E2E_LOCK.release()
    assert result["ok"] is False
    observations = {item["name"]: item["value"] for item in result["observations"]}
    assert "占用" in observations["error"], observations
