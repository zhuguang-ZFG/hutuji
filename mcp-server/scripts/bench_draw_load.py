#!/usr/bin/env python3
"""绘图队列假负载压测（不调用付费生图 / vpype / LLM）。

默认 memory:// 单进程 + 替身 _run_draw_job（asyncio.sleep），测入队/worker/等待
全链。Redis 模式需本机 Redis 与 HUTUJI_DRAW_QUEUE_URL。

示例（10 画/秒 × 30 秒，8 worker，假 job 250ms）：

  .venv/Scripts/python scripts/bench_draw_load.py --rps 10 --duration 30 --workers 8

模拟 AI 链 3s 延迟、需 ~30 在途：

  .venv/Scripts/python scripts/bench_draw_load.py --rps 10 --duration 30 --workers 32 --job-ms 3000

退出码：0 = 达到 --min-success-rate；1 = 未达标或参数错误。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hutuji_mcp import draw_queue, server  # noqa: E402
from hutuji_mcp.config import Settings, load_settings  # noqa: E402
from hutuji_mcp.draw_worker import _process_one  # noqa: E402


@dataclass
class BenchStats:
    enqueued: int = 0
    rejected: int = 0
    completed: int = 0
    failed: int = 0
    timed_out: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    errors: dict[str, int] = field(default_factory=dict)
    producer_elapsed_s: float = 0.0
    total_elapsed_s: float = 0.0

    def note_error(self, name: str) -> None:
        self.errors[name] = self.errors.get(name, 0) + 1


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return ordered[idx]


def _apply_bench_settings(args: argparse.Namespace) -> Settings:
    import hutuji_mcp.config as config

    base = load_settings()
    queue_url = args.redis_url if args.backend == "redis" else "memory://"
    patched = Settings(
        **{
            **base.__dict__,
            "draw_queue_url": queue_url,
            "draw_queue_max_depth": args.queue_depth,
            "draw_enqueue_per_second": args.enqueue_per_second,
            "draw_per_device_minute": args.per_device_minute,
            "public_base_url": "https://bench.hutuji.local",
        }
    )
    config.settings = patched
    server.settings = patched
    draw_queue.settings = patched  # type: ignore[attr-defined]
    draw_queue.reset_backend_for_tests()
    return patched


def _install_fake_draw_job(job_ms: float) -> None:
    async def fake_run_draw_job(
        description: str, public_base_url: str, *, job_id: str | None = None,
        chain_push: bool = True, machine: str = "",
    ) -> str:
        await asyncio.sleep(job_ms / 1000.0)
        token = secrets.token_urlsafe(16)[:22]
        name = f"draw_20260830_120000_{token}.gcode"
        return json.dumps(
            {
                "url": f"{public_base_url}/files/{name}",
                "preview_url": f"{public_base_url}/files/{name.replace('.gcode', '.png')}",
                "lines": 42,
                "bytes": 1024,
                "pen_lifts": 3,
                "ttl_seconds": 600,
            },
            ensure_ascii=False,
        )

    server._run_draw_job = fake_run_draw_job  # type: ignore[method-assign]


async def _worker_loop(stop: asyncio.Event, stats: BenchStats) -> None:
    while not stop.is_set():
        try:
            processed = await _process_one()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            stats.note_error(type(exc).__name__)
            await asyncio.sleep(0.05)


async def _wait_job(
    job_id: str,
    started: float,
    stats: BenchStats,
    timeout_s: float,
) -> None:
    try:
        await draw_queue.wait_result(job_id, timeout=timeout_s)
        stats.completed += 1
        stats.latencies_ms.append((time.monotonic() - started) * 1000.0)
    except asyncio.TimeoutError:
        stats.timed_out += 1
    except Exception as exc:
        stats.failed += 1
        stats.note_error(type(exc).__name__)


async def _producer(
    args: argparse.Namespace,
    stats: BenchStats,
    stop: asyncio.Event,
) -> None:
    interval = 1.0 / args.rps
    deadline = time.monotonic() + args.duration
    seq = 0
    waiters: list[asyncio.Task[None]] = []
    producer_t0 = time.monotonic()
    while time.monotonic() < deadline and not stop.is_set():
        tick = time.monotonic()
        desc = f"压测负载{seq:06d}"
        device_id = ""
        if args.device_id:
            device_id = f"{args.device_id}-{seq % max(1, args.device_shards)}"
        try:
            job_id = await draw_queue.attach_or_enqueue(
                desc,
                "https://bench.hutuji.local",
                device_id=device_id,
            )
            stats.enqueued += 1
            waiters.append(
                asyncio.create_task(
                    _wait_job(job_id, tick, stats, args.wait_timeout),
                    name=f"wait-{job_id}",
                )
            )
        except draw_queue.DrawQueueBusyError:
            stats.rejected += 1
        except Exception as exc:
            stats.rejected += 1
            stats.note_error(type(exc).__name__)
        seq += 1
        sleep_s = interval - (time.monotonic() - tick)
        if sleep_s > 0:
            await asyncio.sleep(sleep_s)
    stats.producer_elapsed_s = time.monotonic() - producer_t0
    if waiters:
        await asyncio.gather(*waiters, return_exceptions=True)


async def _run_bench(args: argparse.Namespace) -> BenchStats:
    _apply_bench_settings(args)
    _install_fake_draw_job(args.job_ms)

    stats = BenchStats()
    stop = asyncio.Event()
    workers = [asyncio.create_task(_worker_loop(stop, stats), name=f"worker-{i}") for i in range(args.workers)]
    t0 = time.monotonic()
    try:
        await _producer(args, stats, stop)
    finally:
        stop.set()
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
    stats.total_elapsed_s = time.monotonic() - t0
    return stats


def _print_report(args: argparse.Namespace, stats: BenchStats) -> bool:
    producer_s = stats.producer_elapsed_s or args.duration
    total_s = stats.total_elapsed_s or producer_s
    drain_s = max(0.0, total_s - producer_s)
    success_rate = (stats.completed / stats.enqueued) if stats.enqueued else 0.0
    print("== hutuji draw load bench ==")
    print(
        f"backend={args.backend} rps={args.rps} duration={args.duration}s "
        f"workers={args.workers} job_ms={args.job_ms} queue_depth={args.queue_depth}"
    )
    print(
        f"enqueued={stats.enqueued} rejected={stats.rejected} "
        f"completed={stats.completed} failed={stats.failed} timed_out={stats.timed_out}"
    )
    print(
        f"producer_s={producer_s:.1f} drain_s={drain_s:.1f} total_s={total_s:.1f} "
        f"enqueue_rps={stats.enqueued / producer_s:.2f} "
        f"complete_rps={stats.completed / total_s:.2f}"
    )
    if stats.latencies_ms:
        print(
            "latency_ms "
            f"p50={_percentile(stats.latencies_ms, 50):.0f} "
            f"p95={_percentile(stats.latencies_ms, 95):.0f} "
            f"p99={_percentile(stats.latencies_ms, 99):.0f} "
            f"mean={statistics.fmean(stats.latencies_ms):.0f}"
        )
    if stats.errors:
        print("errors " + " ".join(f"{k}={v}" for k, v in sorted(stats.errors.items())))
    ok = stats.enqueued > 0 and success_rate >= args.min_success_rate
    print(f"success_rate={success_rate:.1%} threshold={args.min_success_rate:.0%} -> {'PASS' if ok else 'FAIL'}")
    if stats.rejected and stats.enqueued < args.rps * args.duration * 0.5:
        print(
            "hint: 入队拒绝过多——检查 queue_depth / enqueue_per_second；"
            "或增大 --workers / 减小 --job-ms"
        )
    if drain_s > args.duration * 2 and args.job_ms >= 1000:
        print(
            "hint: drain 阶段很长说明 worker 吞吐不足；"
            f"3s 假 job 建议 workers >= {int(args.rps * args.job_ms / 1000) + 2}"
        )
    return ok


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="hutuji 绘图队列假负载压测")
    parser.add_argument("--rps", type=float, default=10.0, help="目标入队速率（画/秒）")
    parser.add_argument("--duration", type=float, default=30.0, help="压测时长（秒）")
    parser.add_argument("--workers", type=int, default=8, help="并发 draw-worker 协程数")
    parser.add_argument("--job-ms", type=float, default=250.0, help="替身 job 耗时（毫秒）")
    parser.add_argument("--backend", choices=("memory", "redis"), default="memory")
    parser.add_argument(
        "--redis-url",
        default=os.environ.get("HUTUJI_DRAW_QUEUE_URL", "redis://127.0.0.1:6379/0"),
    )
    parser.add_argument("--queue-depth", type=int, default=200)
    parser.add_argument("--enqueue-per-second", type=int, default=100, help="全局限流入队上限")
    parser.add_argument("--per-device-minute", type=int, default=60)
    parser.add_argument("--device-id", default="", help="非空则附带 device_id 测单设备限流")
    parser.add_argument("--device-shards", type=int, default=10, help="device_id 分片数")
    parser.add_argument("--wait-timeout", type=float, default=120.0, help="单 job 等待超时（秒）")
    parser.add_argument("--min-success-rate", type=float, default=0.95)
    args = parser.parse_args(argv)
    if args.rps <= 0 or args.duration <= 0 or args.workers < 1:
        parser.error("rps/duration 须 > 0，workers 须 >= 1")
    return args


def main(argv: list[str] | None = None) -> int:
    import logging

    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("hutuji_mcp.draw_worker").setLevel(logging.ERROR)
    args = _parse_args(argv)
    if args.backend == "redis":
        try:
            import redis

            client = redis.from_url(args.redis_url)
            client.ping()
        except Exception as exc:
            print(f"Redis 不可用 ({args.redis_url}): {type(exc).__name__}", file=sys.stderr)
            return 1
    stats = asyncio.run(_run_bench(args))
    return 0 if _print_report(args, stats) else 1


if __name__ == "__main__":
    raise SystemExit(main())
