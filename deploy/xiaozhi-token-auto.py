#!/usr/bin/env python3
"""小智控制台 JWT 自动登录——看门狗检出 401/403 时触发，替代人工取 token。

链路（每轮）：GET /auth/captcha（SVG 文本验证码，cookie 键控）
→ resvg 光栅化 → ddddocr 识别 → 形状闸 → POST /auth/login
{username,password,captcha_code} → 取 data.token → 调
refresh-xiaozhi-token.sh 完成验证/写回/重启/探针。

设计边界：
- 凭据只从 /etc/hutuji/xiaozhi-login.env 读（600），永不入库、永不上日志；
- token 不落日志、不打印，只作为 refresh 脚本 argv 传递（与人工路径一致）；
- import 期零第三方依赖（ddddocr/resvg 懒加载），单测可注入假 OCR/传输；
- 事件驱动低频触发（JWT 标称 90 天，作废是事件性的），默认 5 轮重试，
  轮间 2s；全败退 1，由看门狗报警兜底回人工。

退出码：0 成功（或 --selftest 形状通过）；1 全部尝试失败/写回失败；
2 用法或凭据缺失。
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

# svg-captcha 类默认 4 位字母数字；4–6 余量防服务端调长。形状不符说明 OCR
# 输出已坏（如出现 "]"/空格），不值得提交，直接换码重试。
_CAPTCHA_ANSWER_RE = re.compile(r"^[A-Za-z0-9]{4,6}$")
# 三段 JWT（base64url）；与 refresh-xiaozhi-token.sh 的形状闸同口径。
_JWT_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")

DEFAULT_API_BASE = "https://xiaozhi.me/api"
DEFAULT_ENV_FILE = "/etc/hutuji/xiaozhi-login.env"
DEFAULT_REFRESH_SCRIPT = "/opt/hutuji/deploy/refresh-xiaozhi-token.sh"
_HTTP_TIMEOUT = 15
_RETRY_SLEEP_SEC = 2.0


def parse_env_file(path: str) -> dict[str, str]:
    """解析 KEY=VALUE env 文件（忽略注释/空行/引号），缺文件返回空 dict。"""
    out: dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                out[key.strip()] = value.strip().strip("\"'")
    except OSError:
        pass
    return out


def extract_token(payload: object) -> str | None:
    """从登录应答取 data.token 并做 JWT 形状闸；不合格返回 None。"""
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    token = data.get("token")
    if not isinstance(token, str) or not _JWT_RE.match(token):
        return None
    return token


def _new_opener() -> urllib.request.OpenerDirector:
    """每轮新会话：带 CookieJar（captcha cookie 键控），显式禁代理。"""
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar),
        urllib.request.ProxyHandler({}),
    )


def _fetch_captcha(opener: urllib.request.OpenerDirector, api_base: str) -> bytes:
    with opener.open(f"{api_base}/auth/captcha", timeout=_HTTP_TIMEOUT) as resp:
        body = resp.read()
    if b"<svg" not in body[:200]:
        raise RuntimeError("captcha 应答不是 SVG（验证码类型或已变更）")
    return body


def _rasterize(svg: bytes) -> bytes:
    import resvg_py  # 懒加载：单测注入替身，import 期无第三方依赖

    return bytes(resvg_py.svg_to_bytes(svg_string=svg.decode("utf-8")))


def _ocr(png: bytes) -> str:
    import ddddocr

    if not hasattr(_ocr, "_engine"):
        engine = ddddocr.DdddOcr(show_ad=False)
        # 2026-08-31 阿里云 selftest 实证：不限字符集会把英文字符误识为
        # 中文（'子xKc'）；svg-captcha 只用字母数字，限定字符集消除该类失败。
        engine.set_ranges(
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        )
        _ocr._engine = engine  # type: ignore[attr-defined]
    return _ocr._engine.classification(png)  # type: ignore[attr-defined]


def _login(
    opener: urllib.request.OpenerDirector,
    api_base: str,
    username: str,
    password: str,
    captcha_code: str,
) -> dict:
    body = json.dumps(
        {"username": username, "password": password, "captcha_code": captcha_code}
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{api_base}/auth/login",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with opener.open(req, timeout=_HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def auto_login(
    *,
    username: str,
    password: str,
    api_base: str = DEFAULT_API_BASE,
    attempts: int = 5,
    ocr=_ocr,
    rasterize=_rasterize,
    opener_factory=_new_opener,
    log=lambda msg: None,
    sleep=time.sleep,
) -> str:
    """OCR 重试环：任一环节失败（含验证码错/网络/形状闸）换新码重来。

    验证码错与口令错在应答里难以可靠区分，统一重试——触发是事件性的，
    5 轮成本可忽略；口令真错则 5 轮全败退 1，看门狗报警兜底。
    """
    last_err = "unknown"
    for i in range(1, attempts + 1):
        try:
            opener = opener_factory()
            svg = _fetch_captcha(opener, api_base)
            answer = ocr(rasterize(svg))
            if not _CAPTCHA_ANSWER_RE.match(answer):
                last_err = f"OCR 输出形状不符 len={len(answer)}"
                log(f"第 {i}/{attempts} 轮：{last_err}，换码重试")
                sleep(_RETRY_SLEEP_SEC)
                continue
            token = extract_token(_login(opener, api_base, username, password, answer))
            if token is None:
                last_err = "登录应答无有效 token"
                log(f"第 {i}/{attempts} 轮：{last_err}")
                sleep(_RETRY_SLEEP_SEC)
                continue
            log(f"第 {i}/{attempts} 轮登录成功")
            return token
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError, RuntimeError, ValueError) as e:
            last_err = type(e).__name__
        except Exception as e:  # OCR/光栅化库内部异常也按可重试处理
            last_err = type(e).__name__
        log(f"第 {i}/{attempts} 轮失败：{last_err}")
        sleep(_RETRY_SLEEP_SEC)
    raise RuntimeError(f"自动登录 {attempts} 轮全败（末次：{last_err}）")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="小智控制台 JWT 自动登录续期")
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="只取一张验证码做 OCR 自检（打印识别结果），不登录、不需要凭据",
    )
    parser.add_argument(
        "--env-file",
        default=os.environ.get("XIAOZHI_LOGIN_ENV", DEFAULT_ENV_FILE),
        help="凭据 env 文件路径（默认 /etc/hutuji/xiaozhi-login.env）",
    )
    args = parser.parse_args(argv)

    env = parse_env_file(args.env_file)
    api_base = env.get("XIAOZHI_API_BASE", DEFAULT_API_BASE).rstrip("/")
    log = lambda msg: print(f"xiaozhi-token-auto: {msg}", file=sys.stderr)

    if args.selftest:
        try:
            opener = _new_opener()
            answer = _ocr(_rasterize(_fetch_captcha(opener, api_base)))
        except Exception as e:
            print(f"selftest 失败：{type(e).__name__}", file=sys.stderr)
            return 1
        ok = bool(_CAPTCHA_ANSWER_RE.match(answer))
        print(f"selftest OCR={answer!r} 形状{'通过' if ok else '不符'}")
        return 0 if ok else 1

    username = env.get("XIAOZHI_LOGIN_USERNAME", "")
    password = env.get("XIAOZHI_LOGIN_PASSWORD", "")
    if not username or not password:
        print(
            f"缺 XIAOZHI_LOGIN_USERNAME/_PASSWORD（env 文件 {args.env_file}）",
            file=sys.stderr,
        )
        return 2

    try:
        token = auto_login(
            username=username, password=password, api_base=api_base, log=log
        )
    except RuntimeError as e:
        print(f"xiaozhi-token-auto: {e}", file=sys.stderr)
        return 1

    refresh = os.environ.get("HUTUJI_REFRESH_SCRIPT", DEFAULT_REFRESH_SCRIPT)
    result = subprocess.run([refresh, token], check=False, timeout=90)
    if result.returncode != 0:
        print(
            f"xiaozhi-token-auto: 写回脚本退出 {result.returncode}", file=sys.stderr
        )
        return 1
    print("xiaozhi-token-auto: token 已自动更换并写回", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
