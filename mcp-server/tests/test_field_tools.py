"""仓库根 `scripts/` 下脚本的契约测试。

这些脚本不属于 `hutuji_mcp` 包，按路径 importlib 加载。分两类：

1. 实机工具（`grbl_wifi_setup`）——直接操作真实硬件（写 NVS、重启写字机），
   判定逻辑出错的代价是「半配置状态还被重启」，故其成败判定必须有测试钉死。
2. 门禁（`agent_gate`）——它自己是所有其它验收的闸门，分层覆盖范围一旦缩水
   就是「绿了但没查」，故 profile 与层的对应关系必须有测试钉死。
"""
from __future__ import annotations

import importlib.util
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