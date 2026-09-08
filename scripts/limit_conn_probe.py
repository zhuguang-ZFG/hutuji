#!/usr/bin/env python3
"""limit_conn 行为面探针（课堂/多端加固验收用）。

默认对公网 `/draw-upload/api/health` 开 N 路慢连接（读体前故意拖延），
期望超过 server/location 闸位后出现 503。**不为绿改产品闸**。

用法（本机）：
  python scripts/limit_conn_probe.py --url https://hutuji.donglicao.com/draw-upload/api/health --n 25

闸位对账（文档真值，探针不改）：
  - nginx server limit_conn = 20（阿里 gray zone 名可能不同，数值同口径）
  - /draw-upload/ location limit_conn = 50
  - DrawLimiter 并发 8 / vpype≤6 / worker×8 / 禁止多 uvicorn worker
"""
from __future__ import annotations

import argparse
import concurrent.futures
import ssl
import time
import urllib.error
import urllib.request


def _one(url: str, hold_s: float, timeout: float) -> tuple[int | str, float]:
    t0 = time.monotonic()
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, method="GET", headers={"Connection": "keep-alive"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            # 故意占住连接一段时间，让 limit_conn 看到在途连接
            time.sleep(hold_s)
            body = resp.read(64)
            code = resp.status
            _ = body
            return code, time.monotonic() - t0
    except urllib.error.HTTPError as exc:
        return exc.code, time.monotonic() - t0
    except Exception as exc:  # noqa: BLE001 — 探针要记网络类失败类型
        return type(exc).__name__, time.monotonic() - t0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--url",
        default="https://hutuji.donglicao.com/draw-upload/api/health",
        help="探测 URL（须走带 limit_conn 的 vhost）",
    )
    p.add_argument("--n", type=int, default=25, help="并发连接数（>20 才易撞 server 闸）")
    p.add_argument("--hold", type=float, default=3.0, help="每连接持有秒数")
    p.add_argument("--timeout", type=float, default=20.0)
    args = p.parse_args()

    print(f"PROBE url={args.url} n={args.n} hold={args.hold}s")
    print(
        "BASELINE expected: server limit_conn=20; /draw-upload/ limit_conn=50; "
        "DrawLimiter=8; workers=8; single uvicorn (no multi-worker)"
    )
    results: list[tuple[int | str, float]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.n) as ex:
        futs = [
            ex.submit(_one, args.url, args.hold, args.timeout) for _ in range(args.n)
        ]
        for fut in concurrent.futures.as_completed(futs):
            results.append(fut.result())

    counts: dict[str, int] = {}
    for code, _elapsed in results:
        key = str(code)
        counts[key] = counts.get(key, 0) + 1
    print("RESULT counts:", dict(sorted(counts.items(), key=lambda x: x[0])))
    has_503 = counts.get("503", 0) > 0
    has_ok = counts.get("200", 0) > 0
    if args.n > 20 and not has_503:
        print(
            "NOTE: 未观察到 503。可能原因：① 走了未套 limit_conn 的节点/路径；"
            "② hold 太短连接已释放；③ 阿里 gray zone 名/数值需对照 conf 手核；"
            "④ 本探针从单出口 IP 打公网，CDN/上游池可能摊开。标记为未验证，勿改闸。"
        )
        return 2
    if has_ok and has_503:
        print("PASS: 同批既有 200 又有 503，符合超限拒的行为面。")
        return 0
    if has_ok and args.n <= 20:
        print("PASS: n≤20 全通或主通，未强制期望 503。")
        return 0
    print("INCONCLUSIVE — 见 counts；结果请回填 docs/release-readiness.md / deployment.md")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
