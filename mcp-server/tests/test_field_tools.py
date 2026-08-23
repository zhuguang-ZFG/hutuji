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
import json
import os
import queue
import signal
import subprocess
import sys
import tempfile
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


def _load_mcp_script(name: str):
    """按路径加载 mcp-server/scripts/ 下的脚本（e2e_local / cloud_bridge_smoke）。

    它们不属于 hutuji_mcp 包也不在仓库根 scripts/，_load_script 够不到，
    单独按路径加载。"""
    path = _REPO_ROOT / "mcp-server" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_hutuji_mcp_script_{name}", path)
    assert spec is not None and spec.loader is not None, f"无法加载 {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


wifi_setup = _load_script("grbl_wifi_setup")


# 固件真值（Grbl_Esp32/src/Report.cpp:210/213 成功、:237/239 失败）。
# F-TEST-09：`>{line}:` 形态是防御性兼容——实机 ESP 配置命令（[ESP100/101/110]）
# 走 system_execute_line 无命令回显，`>{line}:` 仅由 report_execute_startup_message
# 用于 $N0/$N1 启动行（Report.cpp:505-507，仅被 ProcessSettings.cpp:625/630 调用），
# 且 ESPResponseStream 的 byid 是假参数（(void)byid）。断言无害、保留。
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


def _write_fix_baseline(tmp_path: Path, refs: dict[str, dict[str, str]]) -> None:
    baseline = tmp_path / "docs" / "agent-line-refs-baseline.json"
    baseline.parent.mkdir(parents=True, exist_ok=True)
    baseline.write_text(
        json.dumps(
            {"version": 1, "git_head": "test", "updated": "test", "refs": refs},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _run_fix_line_refs(tmp_path: Path, monkeypatch) -> int:
    """把门禁脚本指向 tmp_path 并跑 --fix-line-refs 的测试封装。"""
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "_EXTERNAL_REPOS", ())
    monkeypatch.setattr(gate, "_git_head", lambda: "testhead")
    return gate.fix_line_refs()


def test_fix_line_refs_relocations_pure_move(tmp_path, monkeypatch):
    """内容没变只是挪了行号：自动改写文档并重登基线。"""
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    target = tmp_path / "target.py"
    target.write_text("line1\nfoo\nline3\nline4\nline5\n", encoding="utf-8")
    doc = tmp_path / "docs" / "test.md"
    doc.write_text("See target.py:2 for evidence.\n", encoding="utf-8")
    _write_fix_baseline(
        tmp_path,
        {
            "docs/test.md -> target.py:2": {
                "target": "target.py",
                "sha8": gate._sha8(["foo"]),
                "source": "foo",
            }
        },
    )
    # 把 foo 从第 2 行挪到第 5 行
    target.write_text("line1\nline2\nline3\nline4\nfoo\nline6\n", encoding="utf-8")
    rc = _run_fix_line_refs(tmp_path, monkeypatch)
    assert rc == 0
    doc_text = doc.read_text(encoding="utf-8")
    assert "target.py:5" in doc_text
    assert "target.py:2" not in doc_text
    baseline = json.loads(
        (tmp_path / "docs" / "agent-line-refs-baseline.json").read_text(encoding="utf-8")
    )
    assert "docs/test.md -> target.py:5" in baseline["refs"]
    assert baseline["refs"]["docs/test.md -> target.py:5"]["sha8"] == gate._sha8(["foo"])


def test_fix_line_refs_content_change_requires_manual(tmp_path, monkeypatch):
    """内容变了不是纯移动：保持判红并退 1，不得改文档或基线。"""
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    target = tmp_path / "target.py"
    target.write_text("line1\nbar\nline3\n", encoding="utf-8")
    doc = tmp_path / "docs" / "test.md"
    original_doc = "See target.py:2.\n"
    doc.write_text(original_doc, encoding="utf-8")
    _write_fix_baseline(
        tmp_path,
        {
            "docs/test.md -> target.py:2": {
                "target": "target.py",
                "sha8": gate._sha8(["foo"]),
                "source": "foo",
            }
        },
    )
    rc = _run_fix_line_refs(tmp_path, monkeypatch)
    assert rc == 1
    assert doc.read_text(encoding="utf-8") == original_doc
    baseline = json.loads(
        (tmp_path / "docs" / "agent-line-refs-baseline.json").read_text(encoding="utf-8")
    )
    assert "docs/test.md -> target.py:2" in baseline["refs"]


def test_fix_line_refs_multiple_matches_requires_manual(tmp_path, monkeypatch):
    """原文在目标文件中出现多处：无法唯一重定位，交人工。"""
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    target = tmp_path / "target.py"
    # 第 1 行不再是 foo，而 foo 在后面出现两次
    target.write_text("bar\nfoo\nline3\nfoo\nline5\n", encoding="utf-8")
    doc = tmp_path / "docs" / "test.md"
    original_doc = "See target.py:1.\n"
    doc.write_text(original_doc, encoding="utf-8")
    _write_fix_baseline(
        tmp_path,
        {
            "docs/test.md -> target.py:1": {
                "target": "target.py",
                "sha8": gate._sha8(["foo"]),
                "source": "foo",
            }
        },
    )
    rc = _run_fix_line_refs(tmp_path, monkeypatch)
    assert rc == 1
    assert doc.read_text(encoding="utf-8") == original_doc


def test_fix_line_refs_no_drift_returns_zero_and_unchanged(tmp_path, monkeypatch):
    """基线与当前一致时：无漂移，退 0，不改任何文件。"""
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    target = tmp_path / "target.py"
    target.write_text("line1\nfoo\nline3\n", encoding="utf-8")
    doc = tmp_path / "docs" / "test.md"
    original_doc = "See target.py:2.\n"
    doc.write_text(original_doc, encoding="utf-8")
    original_baseline = {
        "docs/test.md -> target.py:2": {
            "target": "target.py",
            "sha8": gate._sha8(["foo"]),
            "source": "foo",
        }
    }
    _write_fix_baseline(tmp_path, original_baseline)
    rc = _run_fix_line_refs(tmp_path, monkeypatch)
    assert rc == 0
    assert doc.read_text(encoding="utf-8") == original_doc
    baseline = json.loads(
        (tmp_path / "docs" / "agent-line-refs-baseline.json").read_text(encoding="utf-8")
    )
    assert baseline["refs"] == original_baseline


def test_fix_line_refs_old_baseline_without_source_requires_manual(tmp_path, monkeypatch):
    """兼容旧基线：条目无 source 字段时不能直接自动重定位，交人工。"""
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    target = tmp_path / "target.py"
    target.write_text("line1\nline2\nline3\nfoo\nline5\n", encoding="utf-8")
    doc = tmp_path / "docs" / "test.md"
    original_doc = "See target.py:2.\n"
    doc.write_text(original_doc, encoding="utf-8")
    _write_fix_baseline(
        tmp_path,
        {
            "docs/test.md -> target.py:2": {
                "target": "target.py",
                "sha8": gate._sha8(["bar"]),
                # 故意缺少 source
            }
        },
    )
    rc = _run_fix_line_refs(tmp_path, monkeypatch)
    assert rc == 1
    assert doc.read_text(encoding="utf-8") == original_doc



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


def test_error90_belongs_to_s3_not_grbl():
    """TOOL-MARK-01：error:90 只出现在 Telnet/S3 日志（hutuji 侧缺纸/换纸相关），
    Grbl 串口只见 [PaperStatus]。它曾是 GRBL_MARKERS，报告表格 Grbl 列恒「否」——
    人判联调结论时会把「否」误读成取证缺陷。归到 S3 侧后：S3 日志出现即判正，
    Grbl 列不再出现该标记（没有可误读的「否」）。"""
    rows = _summary_rows([_log("S3", "hutuji: error:90 纸张用完或未放好")])
    assert rows[("S3", "error:90")] == "是", "S3/Telnet 日志出现 error:90 必须判正"
    assert ("Grbl", "error:90") not in rows, "error:90 已移出 GRBL_MARKERS，Grbl 列不得再出现误导性的「否」"


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


def test_exit_code_ignores_capture_note_echoing_error_markers() -> None:
    """R9-CAP-01：`--note` 是操作员自由文本（落成 `[capture] note=...`），
    复述历史报错文案（如「上次 ERROR opening/reading COM3 之后…」）不得误报
    退出 1；`[capture]` 里唯一照常计入的标记是「恢复未确认」合成失败行。"""
    note = _log("capture", "note=复述：上次 ERROR opening/reading COM3 之后重启过")
    assert capture._exit_code([note]) == 0
    note2 = _log("capture", "note=复述：ERROR writing to COM3 当时把命令写丢了")
    assert capture._exit_code([note2]) == 0
    # 设备来源照旧判红；恢复未确认的合成失败行照旧判红（既有契约不回归）
    assert capture._exit_code([_log("Grbl", "ERROR opening/reading COM3: boom")]) == 1
    failure = capture._restore_failure_line(
        True, [_log("inject", "sent to COM3: [ESP115]OFF")]
    )
    assert capture._exit_code([failure]) == 1


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
    """最小假串口：每次 write 触发下一条预置应答进入缓冲区，read 按 pyserial 语义
    只吐当前可用字节（可少于请求数，fragment_size 可强制分片）。记录打开参数供断言。"""

    def __init__(self, port, timeout, write_timeout, responses, fragment_size=None):
        self.port = port
        self.timeout = timeout
        self.write_timeout = write_timeout
        self._responses = list(responses)
        self._buffer = b""
        # 模拟 TCP/USB 分片：单次 read 最多吐这么多字节（None = 一次全给）。
        self._fragment_size = fragment_size
        self.written: list[bytes] = []
        self.closed = False

    @property
    def in_waiting(self) -> int:
        return len(self._buffer)

    def read(self, size):
        n = min(size, self._fragment_size or size, len(self._buffer))
        chunk = self._buffer[:n]
        self._buffer = self._buffer[n:]
        return chunk

    def write(self, payload):
        self.written.append(payload)
        # 写命令 → 设备应答 的因果关系：每条命令触发下一条预置应答入缓冲。
        if self._responses:
            self._buffer += self._responses.pop(0)

    def close(self):
        self.closed = True


class _FakeWifiSerialModule:
    """冒充 pyserial 模块；SerialException 只供 except 子句求值，映射到 OSError 即可。"""

    SerialException = OSError

    def __init__(self, responses, fragment_size=None):
        self._responses = responses
        self._fragment_size = fragment_size
        self.opened: list[_FakeWifiSerial] = []

    def Serial(self, port, baud, timeout=None, write_timeout=None):
        """名字须与 pyserial 一致，故不按本仓函数命名风格改小写。"""
        ser = _FakeWifiSerial(port, timeout, write_timeout, self._responses, self._fragment_size)
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


def test_wifi_setup_read_assembles_fragmented_response(monkeypatch, capsys):
    """F-TOOL-06：应答跨 TCP/USB 分片到达时（单次 read 只能取到部分字节，如
    'o'+'k' 或 'erro'+'r:3'），必须循环拼出完整行再判定——旧实现单次 read 截断
    会把合法应答判失败、中止配置。这里 fragment_size=1 强制每条应答逐字节到达，
    且断言四步命令全部发出（分片截断会在 [ESP110] 就中止，发不到 RESTART）。"""
    fake = _FakeWifiSerialModule(
        [
            b"ok",  # [ESP100] SSID
            b"ok",  # [ESP101] 密码
            b"ok",  # [ESP110] STA
            b"[MSG:Connected IP: 192.168.1.13]",  # 重启后的联网确认
            b"WiFi Mode: STA",  # [ESP420] 状态
        ],
        fragment_size=1,
    )
    monkeypatch.setitem(sys.modules, "serial", fake)
    monkeypatch.setattr(wifi_setup.time, "sleep", lambda *_: None)
    monkeypatch.setenv("GRBL_WIFI_PASSWORD", "pw")
    monkeypatch.setattr(
        sys,
        "argv",
        ["grbl_wifi_setup.py", "--port", "COM_FAKE", "--ssid", "Test", "--yes"],
    )

    assert wifi_setup.main() == 0, "逐字节分片到达也必须拼出完整应答行，不能截断误判"
    written = b"".join(fake.opened[0].written)
    assert b"[ESP100]Test\r\n" in written, "SSID 命令必须发出"
    assert b"[ESP101]pw\r\n" in written, "密码命令必须发出"
    assert b"[ESP110]STA\r\n" in written, "STA 命令必须发出"
    assert b"[ESP444]RESTART\r\n" in written, "四步命令都发完才叫没被分片截断中止"


def test_wifi_setup_connected_judged_after_esp420_status(monkeypatch, capsys):
    """F-TOOL-07：STA 恰在重启后读取之后、[ESP420] 之前连上时，connected 必须以
    [ESP420] 状态行的设备自报为准——旧实现按前两次读取判「未连上」却打出 Connected
    状态行，exit 1 与展示自相矛盾。F-TOOL-08 收紧后：连上判据与展示都只认 `IP:`（
    固件 WebSettings.cpp:460 无条件先印 `Connected to: `，只有 :491 的 `IP: ` 在
    isConnected() 分支内）。"""
    fake = _FakeWifiSerialModule(
        [
            b"ok",  # [ESP100] SSID
            b"ok",  # [ESP101] 密码
            b"ok",  # [ESP110] STA
            b"[MSG:Wifi connecting...]",  # 重启后读取时 STA 还没连上
            b"WiFi Mode: STA\nConnected to: Test\nSignal: 80%\nIP: 192.168.1.13",  # [ESP420] 已连上
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

    assert wifi_setup.main() == 0, "ESP420 自报已连上必须判成功，不得 exit 1"
    out = capsys.readouterr().out
    assert "IP: 192.168.1.13" in out, "设备自报的 IP 状态行必须展示"
    assert "Connected to:" not in out, "F-TOOL-08：无条件前缀行不再展示（未连上时同样出现）"


def test_wifi_setup_bare_connected_to_is_not_connection(monkeypatch, capsys):
    """F-TOOL-08 回归：固件在 STA 模式下**无条件**先印 `Connected to: `（WebSettings.cpp:460，
    未连上时拼接成 'Connected to: Disabled Mode: …'），裸 `Connected to: ` 子串命中即
    判连上是「STA 未连接却 exit 0」的假阳性。收紧后只认 `IP:`，本形状必须 fail-closed。"""
    fake = _FakeWifiSerialModule(
        [
            b"ok",  # [ESP100] SSID
            b"ok",  # [ESP101] 密码
            b"ok",  # [ESP110] STA
            b"[MSG:Wifi connecting...]",  # 重启后读取时 STA 还没连上
            b"WiFi Mode: STA\nConnected to: Disabled Mode: AA:BB:CC:DD:EE:FF",  # [ESP420] 未连上
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

    assert wifi_setup.main() == 1, "只有无条件前缀 Connected to: 没有 IP: 时必须判失败"
    err = capsys.readouterr().err
    assert "WiFi 未确认连上" in err, "失败原因必须单行点明"
    out = capsys.readouterr().out
    assert "Connected to:" not in out, "未连上时的无条件前缀行不得展示成已连上"


def test_wifi_setup_not_connected_still_fails_closed(monkeypatch, capsys):
    """F-TOOL-07 负向：boot 与 [ESP420] 状态都没有 Connected/IP 时仍 fail-closed
    exit 1——联网确认判定只换位置，不许放宽。"""
    fake = _FakeWifiSerialModule(
        [
            b"ok",  # [ESP100] SSID
            b"ok",  # [ESP101] 密码
            b"ok",  # [ESP110] STA
            b"[MSG:Wifi connecting...]",  # 重启后读取时还没连上
            b"WiFi Mode: STA\nSignal: 0%",  # [ESP420] 状态也没有 Connected/IP
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

    assert wifi_setup.main() == 1, "任何一处都没有 Connected/IP 时必须判失败"
    err = capsys.readouterr().err
    assert "WiFi 未确认连上" in err, "失败原因必须单行点明"


# ---- F-XZ-08：小智控制台上传应答缺 data 字段 ----

console = _load_script("xiaozhi_console")


def test_update_kb_missing_upload_data_fails_single_line(monkeypatch, tmp_path, capsys):
    """F-XZ-08：上传应答 success=true 却缺 data.file_name/file_md5 时必须按
    「上传应答缺少 file_name/file_md5」单行报错 exit 1，不能裸 KeyError traceback
    （应答形状不符是常态，不是异常）。"""
    monkeypatch.setattr(console, "KNOWLEDGE_BASE_PATH", tmp_path / "knowledge-base.md")
    (tmp_path / "knowledge-base.md").write_text("内容", encoding="utf-8")
    monkeypatch.setattr(console, "api", lambda method, path, body=None: {"data": {"list": []}})
    monkeypatch.setattr(console, "api_upload", lambda path, file_path: {"success": True, "data": {}})

    with pytest.raises(SystemExit) as exc:
        console.cmd_update_kb("42")
    assert exc.value.code == 1, "应答形状不符按失败收场，退出码 1"
    err = capsys.readouterr().err
    assert "缺少 file_name/file_md5" in err, "必须单行点明缺的是哪两个字段"
    assert "Traceback" not in err, "不许裸 traceback"


# ---- TOOL-UX-03：退出码统一（usage/参数错=2，运行错=1）----

@pytest.mark.parametrize(
    "argv",
    [
        [],  # 无命令 = 用法错误（旧实现 exit 0）
        ["show"],  # 缺子命令参数
        ["update-role"],
        ["update-kb"],
        ["recall", "42"],  # 缺 query
        ["device-call", "hutuji.status", "{}", "extra"],  # 参数个数错
        ["sync"],
        ["unknown-cmd"],  # 未知命令（旧实现 exit 1）
    ],
)
def test_console_usage_errors_exit_2(monkeypatch, argv, capsys):
    """TOOL-UX-03：用法/参数错误统一退出码 2——旧实现混用 0/1/2（无命令 exit 0、
    缺参数与未知命令 exit 1、仅 device-call 参数错 exit 2），脚本化调用方无法区分
    「用错了」与「跑挂了」。"""
    monkeypatch.setattr(sys, "argv", ["xiaozhi_console.py", *argv])
    with pytest.raises(SystemExit) as exc:
        console.main()
    assert exc.value.code == 2, argv


def test_console_update_kb_non_integer_id_is_usage_error(monkeypatch, capsys):
    """TOOL-UX-03：kb_id 非整数是参数错误 → 退出码 2（旧实现 exit 1）。"""
    monkeypatch.setattr(sys, "argv", ["xiaozhi_console.py", "update-kb", "abc"])
    with pytest.raises(SystemExit) as exc:
        console.main()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "kb_id 必须是整数" in err


def test_console_recall_non_integer_id_is_usage_error(monkeypatch, capsys):
    """TOOL-UX-03：recall 的 kb_id 非整数同样归参数错误 → 退出码 2。"""
    monkeypatch.setattr(sys, "argv", ["xiaozhi_console.py", "recall", "abc", "画一只猫"])
    with pytest.raises(SystemExit) as exc:
        console.main()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "kb_id 必须是整数" in err


def test_update_kb_delete_failure_summarizes_parallel_versions(monkeypatch, tmp_path, capsys):
    """TOOL-UX-03：update-kb 的 DELETE 循环收集到失败后必须汇总提示「新旧并存需人工
    清理」再非零退出——新文档已建档、旧文档没删干净时远端是新旧并存状态，半清理
    静默成功会让操作员误以为替换已完成。"""
    monkeypatch.setattr(console, "KNOWLEDGE_BASE_PATH", tmp_path / "knowledge-base.md")
    (tmp_path / "knowledge-base.md").write_text("内容", encoding="utf-8")

    def fake_api(method, path, body=None):
        if method == "GET":
            return {"data": {"list": [{"id": 7, "title": "旧文档"}]}}
        if method == "DELETE":
            return {"success": False}
        return {"success": True}

    monkeypatch.setattr(console, "api", fake_api)
    monkeypatch.setattr(
        console,
        "api_upload",
        lambda path, file_path: {
            "success": True,
            "data": {"file_name": "kb.md", "file_md5": "d41d8cd98f00b204e9800998ecf8427e"},
        },
    )

    with pytest.raises(SystemExit) as exc:
        console.cmd_update_kb("42")
    assert exc.value.code == 1, "删除失败属运行期错误 → 退出码 1（不是用法错误 2）"
    err = capsys.readouterr().err
    assert "新旧并存" in err and "需人工清理" in err, f"必须点明新旧并存需人工清理：{err}"
    assert "旧文档" in err, "汇总要列出删失败的具体文档，操作员才知道清哪些"


def test_console_logical_failure_message_is_redacted(monkeypatch, tmp_path, capsys):
    """HTTP 200 + success=false 的自由文本也可能回显凭据，必须与 HTTPError 同口径脱敏。"""
    role = tmp_path / "role-prompt.md"
    role.write_text("# 角色\n内容", encoding="utf-8")
    monkeypatch.setattr(console, "ROLE_PROMPT_PATH", role)
    monkeypatch.setattr(
        console,
        "api",
        lambda *args, **kwargs: {
            "success": False,
            "message": "echo Bearer top.secret.token and sk-examplelongsecret",
        },
    )

    with pytest.raises(SystemExit) as exc:
        console.cmd_update_role("42")
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "top.secret.token" not in err
    assert "sk-examplelongsecret" not in err
    assert "<redacted>" in err


def test_update_kb_paginates_all_old_documents(monkeypatch, tmp_path, capsys):
    """旧文档超过 50 篇时必须拉完全部分页，不能清首页后误报替换成功。"""
    kb = tmp_path / "knowledge-base.md"
    kb.write_text("内容", encoding="utf-8")
    monkeypatch.setattr(console, "KNOWLEDGE_BASE_PATH", kb)
    deleted: list[int] = []

    def fake_api(method, path, body=None):
        if method == "GET":
            if "page=1" in path:
                return {"data": {"list": [{"id": i, "title": f"旧-{i}"} for i in range(50)]}}
            if "page=2" in path:
                return {"data": {"list": [{"id": 50, "title": "旧-50"}]}}
            raise AssertionError(path)
        if method == "DELETE":
            deleted.append(int(path.rsplit("documentId=", 1)[1]))
            return {"success": True}
        return {"success": True}

    monkeypatch.setattr(console, "api", fake_api)
    monkeypatch.setattr(
        console,
        "api_upload",
        lambda path, file_path: {
            "success": True,
            "data": {"file_name": "kb.md", "file_md5": "d41d8cd98f00b204e9800998ecf8427e"},
        },
    )

    console.cmd_update_kb("42")
    assert deleted == list(range(51))
    assert "现有文档 51 篇" in capsys.readouterr().out

# ============================================================================
# R11-E2E-01/02/03 + R11-BR-01/02：闭环工具链契约测试。
# e2e_local / cloud_bridge_smoke 在 mcp-server/scripts/ 下，走 _load_mcp_script。
# ============================================================================

e2e = _load_mcp_script("e2e_local")
smoke = _load_mcp_script("cloud_bridge_smoke")


class _NS:
    """极简属性容器：给 e2e_local 的 svg_to_gcode / stream_gcode 打桩返回值。"""

    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeSimPopen:
    """e2e_local / bridge 注入用的假子进程：默认保持运行态，_stop 时可被终止。"""

    def __init__(self, running: bool = True):
        self._running = running
        self.returncode: int | None = None if running else 1

    def poll(self) -> int | None:
        return None if self._running else self.returncode

    def terminate(self) -> None:
        self._running = False
        self.returncode = 1

    def kill(self) -> None:
        self._running = False
        self.returncode = 1

    def wait(self, timeout: float | None = None) -> int:
        self._running = False
        return self.returncode

    def communicate(self) -> tuple[str, str]:
        return ("", "")


class _FakeE2EChild:
    """run_e2e 注入用的假 e2e_local 子进程：首次 communicate 抛 TimeoutExpired。"""

    def __init__(self, raise_on_first: bool = True) -> None:
        self.calls: list[tuple[str, object]] = []
        self.returncode = 1
        self.killed = False
        self._raise_on_first = raise_on_first

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        self.calls.append(("communicate", timeout))
        if self._raise_on_first:
            self._raise_on_first = False
            raise subprocess.TimeoutExpired(cmd="e2e_local.py", timeout=timeout or 0)
        return ("", "")

    def kill(self) -> None:
        self.killed = True
        self.calls.append(("kill", None))

    def wait(self, timeout: float | None = None) -> int:
        self.calls.append(("wait", timeout))
        return 1


class _OkE2EChild(_FakeE2EChild):
    """run_e2e 注入用的正常子进程：stdout 带 PASS，exit 0。"""

    def __init__(self) -> None:
        super().__init__(raise_on_first=False)
        self.returncode = 0

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        self.calls.append(("communicate", timeout))
        return ("PASS\n", "")


# ---- R11-E2E-02：门禁 run_e2e 超时整树杀（job object / killpg） ----

def test_run_e2e_timeout_terminates_whole_tree(monkeypatch):
    """R11-E2E-02：门禁 run_e2e 超时必须整树终止，不能只杀直接子进程。

    旧的 subprocess.run(timeout=300) 超时只杀 e2e_local 本身，其 spawn 的
    grbl_sim 孙进程成孤儿永占 2323，之后每次门禁 e2e 层都因端口占用 FAIL（已实证）。
    """
    child = _FakeE2EChild()
    terminated: list[object] = []
    monkeypatch.setattr(gate, "_mcp_python", lambda: Path("py"))
    monkeypatch.setattr(gate.subprocess, "Popen", lambda *a, **k: child)
    monkeypatch.setattr(gate, "_kill_process_tree_on_close", lambda c: None)
    monkeypatch.setattr(gate, "_terminate_whole_tree", lambda c: terminated.append(c))
    monkeypatch.setattr(gate, "_release_job", lambda c: None)

    layer = gate.run_e2e()

    assert layer["status"] == "fail"
    assert "300s" in layer["detail"], layer["detail"]
    assert terminated == [child], "超时路径必须调用整树终止（TerminateJobObject / killpg）"
    assert child.calls[0] == ("communicate", 300), (
        "超时时限钉死 300s（与 sim_server.LOCAL_E2E_TIMEOUT_SECONDS 统一）"
    )


def test_run_e2e_spawns_isolated_process_group(monkeypatch):
    """R11-E2E-02：spawn 必须为整树回收铺路——POSIX 走 start_new_session（setsid
    使 e2e_local 成组长、grbl_sim 同组可 killpg），Windows 由 job object 兜底；
    正常路径仍判 pass、超时时限仍是 300s。"""
    child = _OkE2EChild()
    captured: dict[str, object] = {}

    def _popen(*args, **kwargs):
        captured.update(kwargs)
        return child

    monkeypatch.setattr(gate, "_mcp_python", lambda: Path("py"))
    monkeypatch.setattr(gate.subprocess, "Popen", _popen)
    monkeypatch.setattr(gate, "_kill_process_tree_on_close", lambda c: None)
    monkeypatch.setattr(gate, "_release_job", lambda c: None)

    layer = gate.run_e2e()

    assert layer["status"] == "pass", layer["detail"]
    assert captured["start_new_session"] is (os.name != "nt")
    assert child.calls[0] == ("communicate", 300)


def test_run_e2e_spawn_failure_is_layer_fail(monkeypatch):
    """R11-E2E-02：spawn 失败（venv 被删 / 架构不符 / ENOEXEC）必须是本层判红，
    不能穿透到 main()——与 run_pytest 的 GATE-P2-04 同口径。"""

    def _boom(*_a, **_k):
        raise OSError("venv 解释器不可执行")

    monkeypatch.setattr(gate, "_mcp_python", lambda: Path("py"))
    monkeypatch.setattr(gate.subprocess, "Popen", _boom)

    layer = gate.run_e2e()

    assert layer["status"] == "fail"
    assert "无法启动" in layer["detail"]


def test_gate_posix_tree_kill_uses_process_group(monkeypatch):
    """R11-E2E-02：POSIX 整树杀 = killpg 杀 e2e_local 的进程组（start_new_session
    保证 grbl_sim 同组，组杀一并回收）。"""
    calls: list[tuple[int, int]] = []
    # Windows 的 os 模块没有 killpg、signal 模块没有 SIGKILL（均 POSIX 专属），
    # monkeypatch 默认拒绝新增属性，raising=False 允许注入；getpgid 同理。
    # 被测函数 _killpg 只在 os.name != "nt" 分支被调用，这里直接驱动它验证语义。
    monkeypatch.setattr(gate.os, "killpg", lambda pgid, sig: calls.append((pgid, sig)), raising=False)
    monkeypatch.setattr(gate.os, "getpgid", lambda pid: pid + 1, raising=False)
    monkeypatch.setattr(gate.signal, "SIGKILL", 9, raising=False)
    gate._killpg(4242)
    assert calls == [(4243, 9)], "killpg 必须打进程组 4243 且信号是 SIGKILL（POSIX 恒为 9）"


def test_gate_terminate_whole_tree_windows_fallback_kills_child():
    """R11-E2E-02：Windows 上 job 挂接失败（无 _hutuji_job）时整树杀降级为
    child.kill()——仍必须终止直接子进程，不能静默放过。"""
    if os.name != "nt":
        return  # POSIX 走 _killpg，见 test_gate_posix_tree_kill_uses_process_group
    child = _FakeE2EChild()
    gate._terminate_whole_tree(child)
    assert child.killed


# ---- R11-E2E-01：grbl_sim stderr 不再悬在无人排空的 PIPE 上 ----

def test_e2e_local_sim_stderr_redirected_to_file_not_pipe(monkeypatch):
    """R11-E2E-01：grbl_sim 的 stderr 必须落文件而非 PIPE。

    PIPE 背压死锁链：grbl_sim 对每条 G-code 行 logger.info（grbl_sim.py:185），
    夹具增至数百行即写满 Windows 4KB 匿名管道 → grbl_sim 阻塞不应答 → ok 超时 →
    真 PASS 误 FAIL。文件无背压，写多少都行。
    """
    captured: dict[str, object] = {}

    def _popen(*args, **kwargs):
        captured["stderr"] = kwargs["stderr"]
        return _FakeSimPopen(running=True)

    monkeypatch.setattr(e2e, "_sim_port", lambda: 29998)
    monkeypatch.setattr(e2e, "_port_in_use", lambda host, port: False)
    monkeypatch.setattr(e2e.subprocess, "Popen", _popen)
    monkeypatch.setattr(e2e, "_wait_ready", lambda *a, **k: None)
    monkeypatch.setattr(
        e2e, "svg_to_gcode",
        lambda _svg, _path: _NS(lines=10, bytes=120),
    )
    monkeypatch.setattr(
        e2e, "stream_gcode",
        lambda _path, _host, _port: _NS(
            success=True, ok_lines=10, total_lines=10,
            failed_line=0, error_response="", elapsed_s=1.0,
        ),
    )

    assert e2e.main() == 0
    stderr = captured["stderr"]
    assert stderr is not subprocess.PIPE, "stderr 不得再是悬空 PIPE（背压死锁源）"
    assert hasattr(stderr, "fileno"), "stderr 必须是真实文件，早退诊断才能 seek+read"


def test_e2e_local_early_exit_diagnostics_readable():
    """R11-E2E-01：grbl_sim 早退时 _wait_ready 必须能从 stderr 落盘文件取出诊断文本
    （旧实现靠 communicate()，改文件重定向后这条路径不许丢）。"""

    class _DeadSim:
        returncode = 7

        def poll(self):
            return self.returncode

        def communicate(self):
            raise AssertionError("stderr 已落文件，不得再走 communicate()")

    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace") as log:
        log.write("Traceback (most recent call last):\nboom\n")
        log.flush()
        with pytest.raises(RuntimeError, match="grbl_sim 子进程提前退出 rc=7") as exc:
            e2e._wait_ready(_DeadSim(), "127.0.0.1", 29998, stderr_log=log)
        assert "boom" in str(exc.value), "早退诊断必须包含 stderr 落盘内容"


# ---- R11-E2E-03：裸 OSError 归一为 FAIL: 文案 ----

def test_e2e_local_bare_oserror_is_fail_message(monkeypatch, capsys):
    """R11-E2E-03：裸 OSError（WinError 10038 等，非 ConnectionError 子类）也必须
    走统一 FAIL: 文案 + 退出码 1，不许打原始 traceback。"""

    def _boom(_svg, _path):
        raise OSError(10038, "WSAENOTSOCK")

    monkeypatch.setattr(e2e, "_sim_port", lambda: 29998)
    monkeypatch.setattr(e2e, "_port_in_use", lambda host, port: False)
    monkeypatch.setattr(e2e.subprocess, "Popen", lambda *a, **k: _FakeSimPopen(running=True))
    monkeypatch.setattr(e2e, "_wait_ready", lambda *a, **k: None)
    monkeypatch.setattr(e2e, "svg_to_gcode", _boom)

    code = e2e.main()

    assert code == 1
    err = capsys.readouterr().err
    assert "FAIL:" in err, f"裸 OSError 必须归一为 FAIL: 文案：{err!r}"
    assert "WSAENOTSOCK" in err, "异常原因要留在诊断里"


# ---- R11-BR-01：bridge 就绪轮询替代固定 sleep(12) ----

def test_bridge_smoke_ready_marker_polls_early():
    """R11-BR-01：就绪 marker 已在日志流时立即返回，不得等满固定 sleep。"""
    q: queue.Queue[str] = queue.Queue()
    q.put("小智 MCP WSS 已连接")
    fake = _FakeSimPopen(running=True)

    t0 = time.monotonic()
    seen = smoke._wait_bridge_ready(fake, q, timeout_s=12.0, poll_s=0.05)
    elapsed = time.monotonic() - t0

    assert any("小智 MCP WSS 已连接" in line for line in seen)
    assert elapsed < 5, f"marker 已在队列却等满超时：{elapsed:.2f}s"


def test_bridge_smoke_ready_timeout_bounded():
    """R11-BR-01：marker 迟迟不出现时轮询有上限，不会无限等。"""
    q: queue.Queue[str] = queue.Queue()
    fake = _FakeSimPopen(running=True)
    t0 = time.monotonic()
    seen = smoke._wait_bridge_ready(fake, q, timeout_s=0.5, poll_s=0.05)
    elapsed = time.monotonic() - t0
    assert elapsed < 5
    assert not any("小智 MCP WSS 已连接" in line for line in seen)


def test_bridge_smoke_ready_breaks_on_bridge_exit():
    """R11-BR-01：bridge 提前退出时轮询立即收场（返回已收集日志），不等到超时。"""
    q: queue.Queue[str] = queue.Queue()
    q.put("log line before exit")
    fake = _FakeSimPopen(running=False)  # poll() -> 1（已退出）
    t0 = time.monotonic()
    seen = smoke._wait_bridge_ready(fake, q, timeout_s=12.0, poll_s=0.05)
    elapsed = time.monotonic() - t0
    assert elapsed < 5
    assert seen == ["log line before exit"]


# ---- R11-BR-02：MCP_ENDPOINT wss 形态预校验（不回显取值） ----

@pytest.mark.parametrize(
    ("endpoint", "reason"),
    [
        ("http://127.0.0.1:8300/mcp", "http 而非 wss"),
        ("ws://cloud.example.com:8300/mcp", "ws 而非 wss"),
        ("wss://", "缺主机名"),
        ("", "未设置"),
    ],
)
def test_bridge_smoke_main_rejects_invalid_endpoint(monkeypatch, capsys, endpoint, reason):
    """R11-BR-02：非 wss / 畸形 MCP_ENDPOINT 必须在启动任何子进程前被拒，退出码 2、
    诊断指向环境变量本身且**不回显取值**（可能含 token，脱敏是硬约定）。"""
    monkeypatch.setenv("MCP_ENDPOINT", endpoint)
    monkeypatch.setattr(smoke, "_port_in_use", lambda host, port: False)

    def _forbid_spawn(*_a, **_k):
        raise AssertionError(f"非法端点不得起子进程（{reason}）")

    monkeypatch.setattr(smoke.subprocess, "Popen", _forbid_spawn)

    code = smoke.main()

    assert code == 2, reason
    err = capsys.readouterr().err
    assert "FAIL:" in err, reason
    if endpoint:
        assert endpoint not in err, f"诊断不得回显 MCP_ENDPOINT 取值（{reason}）"


def test_bridge_smoke_validate_endpoint_accepts_wss(monkeypatch):
    """R11-BR-02：合法 wss 端点通过预校验（返回 None，不报错）。"""
    monkeypatch.setenv("MCP_ENDPOINT", "wss://cloud.example.com:8300/mcp?token=SECRET")
    assert smoke._validate_endpoint() is None


cloud_health = _load_script("cloud_health_pull")


def test_cloud_health_redact_masks_capability_token():
    """能力文件名 token 段整段打码；token 是 urlsafe base64、本身可含下划线，
    必须按定长前缀拆（draw_+8 位日期+_+6 位时间+_ = 21 字符），不能按最后一个
    下划线切。短名/普通文本不动。"""
    name = "draw_20260823_123456_AbCdEf-GhIjKl_MnOpQr12"  # token 恰 22 位且含 _ 与 -
    out = cloud_health.redact(f"GET /files/{name} 200\n短名 draw_abc 不动\n")
    assert name not in out
    assert "draw_20260823_123456_***" in out
    assert "draw_abc" in out


def test_cloud_health_redact_masks_token_ending_with_dash():
    """回归：token 第 22 位是 `-`（非单词字符）时尾部 \\b 不存在，整段曾漏脱敏；
    尾部改用负前瞻后，后随空格/引号/行尾三种形态都必须打码。"""
    name = "draw_20260823_123456_AbCdEf-GhIjKl_MnOpQr1-"  # token 恰 22 位且末位为 -
    for tail in (" 200", '"', ""):
        out = cloud_health.redact(f"GET /files/{name}{tail}\n")
        assert name not in out, repr(tail)
        assert "draw_20260823_123456_***" in out, repr(tail)


def test_cloud_health_summarize_counts_key_events():
    """关键事件计数：traceback/session terminated/探活/429 分行统计；无命中给明示。"""
    sample = (
        "Aug 23 01:00:00 host python[1]: 云端连接已建立\n"
        "Aug 23 01:10:00 host python[1]: 空闲超过 60s，主动探活本机 MCP 会话\n"
        "Aug 23 01:10:01 host python[1]: mcp-proxy 探活通过\n"
        "Aug 23 02:00:00 host python[1]: Traceback (most recent call last):\n"
        "Aug 23 02:00:01 host python[1]: Session terminated\n"
        'Aug 23 03:00:00 host nginx[2]: "POST /mcp" 429 19\n'
    )
    summary = cloud_health.summarize(sample)
    assert "traceback: 1" in summary
    assert "session_terminated: 1" in summary
    assert "idle_probe: 1" in summary
    assert "idle_probe_ok: 1" in summary
    assert "http_429: 1" in summary
    assert "wss_connect: 1" in summary
    assert "无关键事件命中" not in summary
    assert "无关键事件命中" in cloud_health.summarize("平平无奇的一行\n")


def test_cloud_health_main_ssh_failure_exit_2(monkeypatch, tmp_path, capsys):
    """ssh/传输失败塌成退出码 2 且不落盘（诊断不携带 stderr 全文外的信息）。"""
    def _boom(*_a, **_k):
        raise RuntimeError("ssh/journalctl 失败（exit 255）：mock")

    monkeypatch.setattr(cloud_health, "pull", _boom)
    monkeypatch.setattr(sys, "argv", ["cloud_health_pull.py", "-o", str(tmp_path)])
    assert cloud_health.main() == 2
    assert "拉取失败" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []
