"""deploy/xiaozhi-token-auto.py 契约测试。

脚本是运维面自动登录器，行为契约：OCR 形状闸先于登录提交、失败换码重试、
全败抛错交看门狗报警、token 只走 argv 不上日志。HTTP/OCR/光栅化全部注入，
不真实触网。
"""

import importlib.util
import io
import json
import urllib.error
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_auto():
    path = _REPO_ROOT / "deploy" / "xiaozhi-token-auto.py"
    spec = importlib.util.spec_from_file_location("_hutuji_token_auto", path)
    assert spec is not None and spec.loader is not None, f"无法加载 {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


auto = _load_auto()

_GOOD_TOKEN = "aaa.bbb.ccc"


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://x/auth/login", code, "err", {}, io.BytesIO(b"{}"))


def _login_ok(payload_token: str = _GOOD_TOKEN):
    return lambda *a, **k: {"data": {"token": payload_token, "user": {}}}


def test_parse_env_file_comments_quotes_and_missing(tmp_path):
    env = tmp_path / "login.env"
    env.write_text(
        '# 注释\nXIAOZHI_LOGIN_USERNAME="user1"\n'
        "XIAOZHI_LOGIN_PASSWORD='p@ss w'\n\nBADLINE\n",
        encoding="utf-8",
    )
    got = auto.parse_env_file(str(env))
    assert got["XIAOZHI_LOGIN_USERNAME"] == "user1"
    assert got["XIAOZHI_LOGIN_PASSWORD"] == "p@ss w"
    assert "BADLINE" not in got
    assert auto.parse_env_file(str(tmp_path / "nope.env")) == {}


@pytest.mark.parametrize(
    "payload",
    [
        {"data": {"token": _GOOD_TOKEN}},
        {"data": {"token": "a_b.c-d.e_f", "user": {}}},
    ],
)
def test_extract_token_happy(payload):
    assert auto.extract_token(payload) is not None


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"data": {}},
        {"data": {"token": "not-a-jwt"}},
        {"data": {"token": 123}},
        {"data": "x"},
        "raw-string",
        None,
    ],
)
def test_extract_token_rejects(payload):
    assert auto.extract_token(payload) is None


@pytest.mark.parametrize(
    "answer, ok",
    [
        ("r6b5", True),
        ("ABCD12", True),
        ("]TmG", False),
        ("ab cd", False),
        ("", False),
        ("abcdefg", False),
    ],
)
def test_captcha_answer_shape(answer, ok):
    assert bool(auto._CAPTCHA_ANSWER_RE.match(answer)) is ok


def _run_auto(monkeypatch, *, answers, login_behaviors, attempts=5):
    """注入式跑 auto_login；返回 (结果或异常, 登录调用次数)。"""
    fetch_calls = {"n": 0}
    login_calls = {"n": 0}

    def fake_fetch(opener, api_base):
        fetch_calls["n"] += 1
        return b"<svg></svg>"

    answer_iter = iter(answers)

    def fake_ocr(png):
        return next(answer_iter)

    behavior_iter = iter(login_behaviors)

    def fake_login(*a, **k):
        login_calls["n"] += 1
        behavior = next(behavior_iter)
        if isinstance(behavior, Exception):
            raise behavior
        return behavior

    monkeypatch.setattr(auto, "_fetch_captcha", fake_fetch)
    monkeypatch.setattr(auto, "_login", fake_login)
    return (
        fetch_calls,
        login_calls,
        lambda: auto.auto_login(
            username="u",
            password="p",
            attempts=attempts,
            ocr=fake_ocr,
            rasterize=lambda svg: b"png",
            opener_factory=lambda: object(),
            sleep=lambda s: None,
        ),
    )


def test_auto_login_first_try_success(monkeypatch):
    _, login_calls, run = _run_auto(
        monkeypatch, answers=["r6b5"], login_behaviors=[{"data": {"token": _GOOD_TOKEN}}]
    )
    assert run() == _GOOD_TOKEN
    assert login_calls["n"] == 1


def test_auto_login_shape_gate_skips_login(monkeypatch):
    """OCR 形状不符（如 "]TmG"）不得提交登录，直接换码重试。"""
    fetch_calls, login_calls, run = _run_auto(
        monkeypatch,
        answers=["]TmG", "r6b5"],
        login_behaviors=[{"data": {"token": _GOOD_TOKEN}}],
    )
    assert run() == _GOOD_TOKEN
    assert fetch_calls["n"] == 2
    assert login_calls["n"] == 1


def test_auto_login_retries_on_http_error(monkeypatch):
    _, login_calls, run = _run_auto(
        monkeypatch,
        answers=["aaaa", "bbbb"],
        login_behaviors=[_http_error(400), {"data": {"token": _GOOD_TOKEN}}],
    )
    assert run() == _GOOD_TOKEN
    assert login_calls["n"] == 2


def test_auto_login_exhausts_attempts(monkeypatch):
    fetch_calls, _, run = _run_auto(
        monkeypatch,
        answers=["aaaa", "bbbb"],
        login_behaviors=[_http_error(400), _http_error(401)],
        attempts=2,
    )
    with pytest.raises(RuntimeError, match="2 轮全败"):
        run()
    assert fetch_calls["n"] == 2


def test_auto_login_bad_token_shape_retries(monkeypatch):
    _, login_calls, run = _run_auto(
        monkeypatch,
        answers=["aaaa", "bbbb"],
        login_behaviors=[{"data": {"token": "bad"}}, {"data": {"token": _GOOD_TOKEN}}],
    )
    assert run() == _GOOD_TOKEN
    assert login_calls["n"] == 2


def test_main_missing_credentials_exit_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv", ["xiaozhi-token-auto.py", "--env-file", str(tmp_path / "none.env")]
    )
    assert auto.main(["--env-file", str(tmp_path / "none.env")]) == 2
    assert "缺 XIAOZHI_LOGIN" in capsys.readouterr().err


def test_main_success_passes_token_via_argv_only(tmp_path, monkeypatch, capsys):
    env = tmp_path / "login.env"
    env.write_text(
        "XIAOZHI_LOGIN_USERNAME=u\nXIAOZHI_LOGIN_PASSWORD=p\n", encoding="utf-8"
    )
    seen = {}

    def fake_run(cmd, check, timeout):
        seen["cmd"] = cmd
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(auto, "auto_login", lambda **k: _GOOD_TOKEN)
    monkeypatch.setattr(auto.subprocess, "run", fake_run)
    rc = auto.main(["--env-file", str(env)])
    assert rc == 0
    assert seen["cmd"][1] == _GOOD_TOKEN  # argv 传递（与人工 refresh 路径一致）
    out = capsys.readouterr()
    assert _GOOD_TOKEN not in out.out and _GOOD_TOKEN not in out.err


def test_main_refresh_failure_exit_1(tmp_path, monkeypatch):
    env = tmp_path / "login.env"
    env.write_text(
        "XIAOZHI_LOGIN_USERNAME=u\nXIAOZHI_LOGIN_PASSWORD=p\n", encoding="utf-8"
    )
    monkeypatch.setattr(auto, "auto_login", lambda **k: _GOOD_TOKEN)
    monkeypatch.setattr(
        auto.subprocess,
        "run",
        lambda *a, **k: type("R", (), {"returncode": 1})(),
    )
    assert auto.main(["--env-file", str(env)]) == 1


def test_main_login_failure_exit_1(tmp_path, monkeypatch):
    env = tmp_path / "login.env"
    env.write_text(
        "XIAOZHI_LOGIN_USERNAME=u\nXIAOZHI_LOGIN_PASSWORD=p\n", encoding="utf-8"
    )

    def boom(**k):
        raise RuntimeError("自动登录 5 轮全败（末次：HTTP 400）")

    monkeypatch.setattr(auto, "auto_login", boom)
    assert auto.main(["--env-file", str(env)]) == 1


def test_main_selftest_no_creds_needed(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(auto, "_fetch_captcha", lambda opener, api: b"<svg></svg>")
    monkeypatch.setattr(auto, "_rasterize", lambda svg: b"png")
    monkeypatch.setattr(auto, "_ocr", lambda png: "r6b5")
    rc = auto.main(["--selftest", "--env-file", str(tmp_path / "none.env")])
    assert rc == 0
    assert "r6b5" in capsys.readouterr().out
