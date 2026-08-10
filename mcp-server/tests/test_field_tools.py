"""仓库根 `scripts/` 下脚本的契约测试。

这些脚本不属于 `hutuji_mcp` 包，按路径 importlib 加载。分三类：

1. 实机工具（`grbl_wifi_setup`）——直接操作真实硬件（写 NVS、重启写字机），
   判定逻辑出错的代价是「半配置状态还被重启」，故其成败判定必须有测试钉死。
2. 门禁（`agent_gate`）——它自己是所有其它验收的闸门，分层覆盖范围一旦缩水
   就是「绿了但没查」，故 profile 与层的对应关系必须有测试钉死。
3. 取证工具（`bringup_fault_capture`）——它产出的摘要表是人判联调结论的依据，
   且它会把写字机置于 WiFi 关态再恢复：标记归属错会凭空生成正向结论，恢复确认
   缺失会让「设备被留在关态」隐形，两者都必须有测试钉死。
"""
from __future__ import annotations

import importlib.util
import queue
import sys
import threading
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_script(name: str):
    """按路径加载 scripts/ 下的脚本模块（仓库无 pyproject，不可 import）。"""
    path = _REPO_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_hutuji_script_{name}", path)
    assert spec is not None and spec.loader is not None, f"无法加载 {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


wifi_setup = _load_script("grbl_wifi_setup")


# 固件真值（Grbl_Esp32/src/Report.cpp:210/213 成功、:237/239 失败；
# :506 的 `>{line}:` 回显与状态同行）。
@pytest.mark.parametrize(
    ("resp", "reason"),
    [
        ("ok", "独立 ok 行"),
        ("ok\r\n", "带 CRLF"),
        (">[ESP110]STA:ok", "verbose 回显 + ok"),
        ("[MSG:some info]\nok", "先 webPrint 信息行再 ok"),
    ],
)
def test_classify_response_accepts_real_success(resp, reason):
    ok, _line = wifi_setup.classify_response(resp)
    assert ok is True, f"应判成功（{reason}）：{resp!r}"


@pytest.mark.parametrize(
    ("resp", "reason"),
    [
        ("error:3", "数字错误码"),
        ("error: Invalid value", "verbose 错误文本"),
        (">[ESP100]MySSID:error:3", "回显 + 错误码"),
        ("", "无应答"),
        ("[MSG:timeout]", "只有信息行、无状态"),
    ],
)
def test_classify_response_rejects_failure(resp, reason):
    ok, _line = wifi_setup.classify_response(resp)
    assert ok is False, f"应判失败（{reason}）：{resp!r}"


# 本组是本次修复的核心判别式：SSID/密码含子串 "ok" 时，旧的
# `"ok" in resp.lower()` 会把失败应答读成成功，脚本继续发 [ESP444]RESTART，
# 击穿 grbl_wifi_setup.py 里「任一配置命令失败即中止、不重启」的承诺。
@pytest.mark.parametrize(
    "value",
    ["BookCafe", "Tokyo5G", "bookworm", "smoketest", "OK-Guest", "lookup"],
)
def test_failure_not_masked_by_ok_substring_in_credentials(value):
    resp = f">[ESP100]{value}:error:3"
    assert "ok" in resp.lower(), "夹具前提：整段确实含 ok 子串（旧实现据此误判）"
    ok, line = wifi_setup.classify_response(resp)
    assert ok is False, f"含 ok 子串的失败应答必须仍判失败：{resp!r}"
    assert "error:3" in line, "展示行必须是 error 行，否则失败原因被藏掉"


def test_error_line_wins_over_later_ok_line():
    """error 优先：任何一行报错就是失败，不能被后续 ok 洗白。"""
    ok, line = wifi_setup.classify_response("error:3\nok")
    assert ok is False
    assert line == "error:3"



gate = _load_script("agent_gate")


def _layer_ids(profile: str) -> list[str]:
    return [layer_id for layer_id, _thunk in gate.profile_layers(profile)]


def test_hub_profile_checks_external_doc_line_refs():
    """AGENTS.md 规定日常跑 hub；外仓 path:line 只在 full 校验等于日常不校验。

    枢纽仓 doc_line_refs 只认本仓文件，文档里绝大多数证据引用指向两个 fork。
    这批行号漂掉时引用即失效，所以 hub 必须承担这层，不能只留给 full。
    """
    assert "external_doc_line_refs" in _layer_ids("hub")
    assert "external_doc_line_refs" in _layer_ids("full")
    assert "external_doc_line_refs" not in _layer_ids("docs"), "docs 层只校验枢纽仓"



def test_line_ref_fingerprint_layer_in_all_profiles():
    """行号内容指纹层兜「行号在界内但内容已变」的半漂移，docs 层也不能缺。

    只在 hub/full 挂这层等于日常 docs 门禁不查半漂移；本仓引用不依赖外仓，
    外仓条目在缺 fork 机器上保留不判陈旧，故三个 profile 都应挂。"""
    for profile in ("docs", "hub", "full"):
        assert "line_ref_fingerprints" in _layer_ids(profile), profile

def test_hub_and_full_differ_on_missing_forks():
    """缺 fork 的机器上：hub 不该变红，full 必须红。"""
    plans = {p: dict(gate.profile_layers(p)) for p in ("hub", "full")}
    assert plans["hub"]["external_doc_line_refs"].keywords == {"require_repos": False}
    assert plans["full"]["external_doc_line_refs"].keywords == {"require_repos": True}


@pytest.mark.parametrize(
    ("require_repos", "expected"),
    [(False, "pass"), (True, "fail")],
)
def test_external_refs_missing_repos_status(monkeypatch, tmp_path, require_repos, expected):
    missing = tmp_path / "nope"
    for env, _default, _subdirs in gate._EXTERNAL_REPOS:
        monkeypatch.setenv(env, str(missing / env))
    layer = gate.check_external_doc_line_refs(require_repos=require_repos)
    assert layer["status"] == expected


def test_external_refs_pass_on_missing_repos_admits_zero_coverage(monkeypatch, tmp_path):
    """缺仓放行时必须自陈「无可校验」，否则 pass 会被读成「查过且干净」。"""
    for env, _default, _subdirs in gate._EXTERNAL_REPOS:
        monkeypatch.setenv(env, str(tmp_path / "nope" / env))
    layer = gate.check_external_doc_line_refs(require_repos=False)
    assert layer["status"] == "pass"
    assert "无可校验" in layer["detail"]



capture = _load_script("bringup_fault_capture")


class _FakeWriteTimeout(Exception):
    """替代 serial.SerialTimeoutException：pyserial 是惰性依赖，且本仓不引新库。"""


class _FakeSerial:
    """最小假串口：read 恒空、write 可选抛超时。用于不碰真 COM 口地驱动采集逻辑。"""

    def __init__(self, port, timeout, write_timeout, write_error):
        self.port = port
        self.timeout = timeout
        self.write_timeout = write_timeout
        self.write_error = write_error
        self.written: list[bytes] = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self, _size):
        time.sleep(0.01)  # 模拟 read 超时返回，避免采集循环空转打满 CPU
        return b""

    def write(self, payload):
        self.written.append(payload)
        if self.write_error:
            raise _FakeWriteTimeout(f"write timeout after {self.write_timeout}s")

    def flush(self):
        pass


class _FakeSerialModule:
    """冒充 pyserial 模块（只需 Serial 一个属性），并留下打开过的假串口供断言。"""

    def __init__(self, write_error):
        self.write_error = write_error
        self.opened: list[_FakeSerial] = []

    def Serial(self, port, baud, timeout=None, write_timeout=None):
        """名字须与 pyserial 一致，故不按本仓函数命名风格改小写。"""
        ser = _FakeSerial(port, timeout, write_timeout, self.write_error)
        self.opened.append(ser)
        return ser


def _log(source: str, text: str) -> str:
    """按 `_serial_worker` / `main` 的落行格式造日志行（时间戳格式同 `_timestamp()`）。"""
    return f"12:34:56.789 [{source}] {text}"


def _summary_rows(lines: list[str]) -> dict[tuple[str, str], str]:
    """把 `_summarize()` 的表格解析成 {(来源, 标记): 是否出现}，让断言不依赖行序。"""
    rows: dict[tuple[str, str], str] = {}
    for row in capture._summarize(lines):
        if not row.startswith("| ") or "是否出现" in row:
            continue
        _, source, marker, seen, _ = (cell.strip() for cell in row.split("|"))
        rows[(source, marker.strip("`"))] = seen
    return rows


def _drain(out: queue.Queue[str]) -> list[str]:
    """排空 `_serial_worker` 的输出队列。"""
    lines: list[str] = []
    while not out.empty():
        lines.append(out.get_nowait())
    return lines


# 标记扫描组：旧实现把所有行合并成一段做 substring 匹配，来源标签形同废纸。实测单行
# `[S3] 已发 M30，[PaperStatus] ...` 就让报告断言 Grbl 收到了 M30 与 [PaperStatus]，
# 而 Grbl 串口一个字节都没收到——那张表是人判联调结论的依据，凭空报正即伪证。
def test_grbl_markers_quoted_in_s3_log_stay_negative_for_grbl():
    rows = _summary_rows([_log("S3", "写字机连接断开，已发 M30，[PaperStatus] 由 S3 复述")])
    assert rows[("Grbl", "M30")] == "否", "Grbl 侧无数据，不能因 S3 复述判正"
    assert rows[("Grbl", "[PaperStatus]")] == "否", "同上：跨来源匹配就是伪证"
    assert rows[("S3", "写字机连接断开")] == "是", "S3 侧仍按自己的行判定，不受修复影响"


def test_capture_note_never_counts_as_device_evidence():
    """`--note` 落成 `[capture] note=...`，是人手写的自由文本，不是任何串口实收。"""
    note = "note=复现 写字机连接断开 后 Grbl 应报 M30 与 [PaperStatus]"
    assert set(_summary_rows([_log("capture", note)]).values()) == {"否"}


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("sent to COM3: [ESP115]OFF", "断连注入回显"),
        ("sent to COM3: [ESP115]ON", "恢复注入回显"),
        ("sent to COM3: M30", "注入集若扩到含标记的命令，回显仍不是设备实收"),
    ],
)
def test_inject_echo_never_counts_as_device_evidence(text, reason):
    assert set(_summary_rows([_log("inject", text)]).values()) == {"否"}, reason


def test_each_source_credited_from_its_own_lines():
    """正常路径不许回归：各来源按自己的行判正。"""
    lines = [_log("Grbl", "M30"), _log("Grbl", "[MSG:WiFi Off]"), _log("S3", "绘图会话重连")]
    rows = _summary_rows(lines)
    assert rows[("Grbl", "M30")] == "是"
    assert rows[("Grbl", "[MSG:WiFi Off]")] == "是"
    assert rows[("S3", "绘图会话重连")] == "是"
    assert rows[("S3", "断连废纸处理失败")] == "否", "没出现的标记仍须判否"


def test_summary_table_shape_unchanged():
    """摘要表是人读的取证格式：表头、行序（先 S3 后 Grbl）、是/否 文案都不许改版。"""
    summary = capture._summarize([])
    assert summary[:4] == ["## 标记扫描", "", "| 来源 | 标记 | 是否出现 |", "|------|------|----------|"]
    assert summary[4:] == [f"| S3 | `{m}` | 否 |" for m in capture.S3_MARKERS] + [
        f"| Grbl | `{m}` | 否 |" for m in capture.GRBL_MARKERS
    ]


# WiFi 恢复组：`[inject] sent to {port}: {cmd}` 只在 ser.write 与 ser.flush() 都返回后才落，
# 故它是「命令已送出」的唯一确认。旧实现把 [ESP115]ON 排进队列就 stop.set() + join(1.5s)
# 走人，且 Serial 无 write_timeout：实测让含 ON 的 write 阻塞 3s，main 3.52s 返回，报告只有
# OFF 行、无 ERROR 行、退出码 0——「写字机被留在 WiFi 关态」这个真实事故完全隐形。
@pytest.mark.parametrize(
    ("source", "text", "reason"),
    [
        ("inject", "sent to COM3: [ESP115]OFF", "只有 OFF 送出，ON 无确认"),
        ("capture", "note=我手动发过 [ESP115]ON", "人工备注不是发送确认"),
        ("Grbl", "sent to COM3: [ESP115]ON", "设备侧文本不是本工具的发送确认"),
    ],
)
def test_restore_failure_line_emitted_without_inject_ack(source, text, reason):
    assert capture._restore_failure_line(True, [_log(source, text)]) is not None, reason


def test_restore_failure_line_absent_after_inject_ack():
    lines = [
        _log("inject", "sent to COM3: [ESP115]OFF"),
        _log("inject", "sent to COM3: [ESP115]ON"),
    ]
    assert capture._restore_failure_line(True, lines) is None


def test_restore_not_required_when_nothing_was_queued():
    """没排过恢复命令时不许凭空报失败（纯监听，或还没走到 OFF 就退出）。"""
    assert capture._restore_failure_line(False, [_log("S3", "写字机连接断开")]) is None


def test_restore_failure_line_is_actionable_and_fails_the_run():
    failure = capture._restore_failure_line(True, [_log("inject", "sent to COM3: [ESP115]OFF")])
    assert failure is not None
    assert "[ESP115]ON" in failure, "要点明缺的是哪条命令，操作员才知道补什么"
    assert "WiFi" in failure, "要点明写字机可能仍处 WiFi 关态"
    assert capture._exit_code([failure]) == 1, "恢复未确认属串口侧故障 → 退出码 1"
    assert _summary_rows([failure])[("Grbl", "[MSG:Client Started]")] == "否", (
        "失败行自己引用了 Grbl 标记（叫操作员去确认它），不能因此把 Grbl 侧判正"
    )


def test_exit_code_covers_every_declared_error_marker():
    """退出码判定必须覆盖声明的每一条故障标记：旧实现只认字面量
    `ERROR opening/reading`，写失败与恢复未确认都会以退出码 0 收场。"""
    assert len(capture.SERIAL_ERROR_MARKERS) >= 3, "至少覆盖 打开/读取、写入、恢复未确认"
    for marker in capture.SERIAL_ERROR_MARKERS:
        assert capture._exit_code([_log("Grbl", f"{marker} COM3: boom")]) == 1, marker


def test_exit_code_zero_on_clean_log():
    lines = [_log("Grbl", "[MSG:WiFi Off]"), _log("inject", "sent to COM3: [ESP115]ON")]
    assert capture._exit_code(lines) == 0


def test_join_budget_covers_one_bounded_write():
    """恢复命令是在 finally 里补排的：join 预算必须够一次有界写走完，
    否则线程被丢下时 [ESP115]ON 还没写出去（旧值 1.5s，小于写超时）。"""
    assert capture.JOIN_TIMEOUT_S > capture.SERIAL_WRITE_TIMEOUT_S


def test_worker_reports_write_failure_apart_from_read_failure(monkeypatch):
    """写失败要有独立文案：套用 `ERROR opening/reading` 会把「端口开着但命令没出去」
    读成读故障，而后者的处置是人工补发 WiFi 恢复，完全不同。"""
    fake = _FakeSerialModule(write_error=True)
    monkeypatch.setattr(capture, "_load_serial_module", lambda: fake)
    stop = threading.Event()
    stop.set()  # 立即置停：worker 仍会排空命令队列（脚本对 Ctrl-C 的既有承诺）
    out: queue.Queue[str] = queue.Queue()
    commands: queue.Queue[str] = queue.Queue()
    commands.put("[ESP115]ON")

    capture._serial_worker("Grbl", "COM_FAKE", 115200, stop, out, commands)

    lines = _drain(out)
    assert any("ERROR writing to" in line for line in lines), f"写失败要留痕：{lines}"
    assert not any("ERROR opening/reading" in line for line in lines), "写失败不得报成读故障"
    assert not any("sent to" in line for line in lines), "write 没成功就落确认行会洗白恢复判定"
    assert capture._exit_code(lines) == 1
    assert fake.opened[0].write_timeout == capture.SERIAL_WRITE_TIMEOUT_S, (
        "Serial 必须带 write_timeout：无上限的阻塞写既不成功也不报错"
    )
    assert fake.opened[0].written == [b"[ESP115]ON\r\n"], "命令仍按 ASCII+CRLF 组包后才写"


def test_main_fails_loudly_when_wifi_restore_is_unconfirmed(monkeypatch, tmp_path):
    """端到端：注入过 OFF 而 ON 没能送出时，报告要有显式失败行、退出码要非 0。

    只用假 serial 模块驱动，绝不打开真实 COM 口；`--duration 1` 是 argparse 允许的最小值。
    """
    fake = _FakeSerialModule(write_error=True)
    monkeypatch.setattr(capture, "_load_serial_module", lambda: fake)
    out_path = tmp_path / "capture.md"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "bringup_fault_capture.py",
            "--grbl-port",
            "COM_FAKE",
            "--duration",
            "1",
            "--inject-grbl-wifi-cycle-at",
            "0",
            "--wifi-off-seconds",
            "0",
            "--out",
            str(out_path),
        ],
    )

    code = capture.main()

    report = out_path.read_text(encoding="utf-8")
    assert "ERROR wifi-restore-unconfirmed" in report, f"恢复未确认必须写进报告：{report}"
    assert "ERROR writing to" in report, "写失败也要留在报告里"
    assert "ERROR opening/reading" not in report, "端口开成功了，不该报成读故障"
    assert code == 1, "串口侧故障 → 退出码 1（AGENTS.md 钉死的退出码语义）"


class _FakeWifiSerial:
    """最小假串口：按序返回预置应答，记录打开参数供断言。不碰真实 COM 口。"""

    def __init__(self, port, timeout, write_timeout, responses):
        self.port = port
        self.timeout = timeout
        self.write_timeout = write_timeout
        self._responses = list(responses)
        self.written: list[bytes] = []
        self.in_waiting = 0
        self.closed = False

    def read(self, _size):
        return self._responses.pop(0) if self._responses else b""

    def write(self, payload):
        self.written.append(payload)

    def close(self):
        self.closed = True


class _FakeWifiSerialModule:
    """冒充 pyserial 模块；SerialException 只供 except 子句求值，映射到 OSError 即可。"""

    SerialException = OSError

    def __init__(self, responses):
        self._responses = responses
        self.opened: list[_FakeWifiSerial] = []

    def Serial(self, port, baud, timeout=None, write_timeout=None):
        """名字须与 pyserial 一致，故不按本仓函数命名风格改小写。"""
        ser = _FakeWifiSerial(port, timeout, write_timeout, self._responses)
        self.opened.append(ser)
        return ser


def test_wifi_setup_serial_write_is_bounded(monkeypatch):
    """ser.write 不设上限会无限挂住交互配网。bringup_fault_capture 已踩过同类坑并
    钉了 SERIAL_WRITE_TIMEOUT_S；配网脚本必须同口径带 write_timeout。"""
    fake = _FakeWifiSerialModule(
        [
            b"ok",  # [ESP100] SSID
            b"ok",  # [ESP101] 密码
            b"ok",  # [ESP110] STA
            b"[MSG:Connected IP: 192.168.1.13]",  # 重启后的联网确认
            b"WiFi Mode: STA",  # [ESP420] 状态
        ]
    )
    monkeypatch.setitem(sys.modules, "serial", fake)
    monkeypatch.setattr(wifi_setup.time, "sleep", lambda *_: None)
    monkeypatch.setenv("GRBL_WIFI_PASSWORD", "pw")
    monkeypatch.setattr(
        sys,
        "argv",
        ["grbl_wifi_setup.py", "--port", "COM_FAKE", "--ssid", "Test", "--yes"],
    )

    assert wifi_setup.main() == 0
    assert fake.opened[0].write_timeout == wifi_setup.SERIAL_WRITE_TIMEOUT_S, (
        "Serial 必须带 write_timeout：无上限的阻塞写会把配网脚本挂死"
    )
    assert fake.opened[0].closed, "退出路径必须关串口"