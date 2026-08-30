"""独立 draw-worker：从 Redis 队列消费并执行 _run_draw_job。

启动：python -m hutuji_mcp.draw_worker
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal

from .config import settings
from .draw_queue import get_backend, queue_enabled
from .redact import _install_secret_redaction

logger = logging.getLogger(__name__)

_BRPOP_TIMEOUT_SECONDS = 5.0


def _worker_instance() -> str:
    return (os.environ.get("HUTUJI_DRAW_WORKER_INSTANCE") or "").strip()


async def _process_one() -> bool:
    from . import job_ledger
    from .server import _run_draw_job

    backend = get_backend()
    job = await backend.dequeue(timeout=_BRPOP_TIMEOUT_SECONDS)
    if job is None:
        return False
    await backend.mark_processing(job.job_id)
    job_ledger.schedule_processing(
        job_id=job.job_id,
        worker_instance=_worker_instance(),
    )
    token = job_ledger.bind_job_id(job.job_id)
    try:
        result = await _run_draw_job(
            job.description,
            job.public_base_url,
            job_id=job.job_id,
            chain_push=job.chain_push,
        )
        await backend.mark_done(job.job_id, result)
        job_ledger.schedule_done(job_id=job.job_id, result_json=result)
        logger.info("draw-worker 完成 job_id=%s", job.job_id)
    except BaseException as exc:
        await backend.mark_failed(job.job_id, exc)
        job_ledger.schedule_failed(job_id=job.job_id, exc=exc)
        logger.warning(
            "draw-worker 失败 job_id=%s type=%s",
            job.job_id,
            type(exc).__name__,
        )
    finally:
        job_ledger.reset_job_id(token)
        await backend.clear_inflight(job.description, job.job_id, chain_push=job.chain_push)
    return True


async def run_forever(stop: asyncio.Event | None = None) -> None:
    from . import queue_metrics

    if not queue_enabled():
        raise SystemExit("HUTUJI_DRAW_QUEUE_URL 未配置，draw-worker 无法启动")
    logger.info("draw-worker 已启动，消费 %s", settings.draw_queue_url.split("@")[-1])
    if stop is None:
        stop = asyncio.Event()
    metrics_task = asyncio.create_task(
        queue_metrics.metrics_loop(stop, role="draw-worker")
    )
    try:
        # 停止事件只在迭代边界观察：处理中的 job 跑完再退出，
        # 避免 systemd SIGTERM 把付费生成拦腰砍断（画途中 TERM 浪费一次生成）
        while not stop.is_set():
            try:
                await _process_one()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("draw-worker 循环异常，5s 后重试")
                await asyncio.sleep(5.0)
        logger.info("draw-worker 收到停止事件，退出消费循环")
    finally:
        stop.set()
        metrics_task.cancel()
        try:
            await metrics_task
        except asyncio.CancelledError:
            pass


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    _install_secret_redaction()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    stop = asyncio.Event()
    # 停止语义是「置事件、跑完当前 job、迭代边界退出」；
    # 旧实现 handler 直调 loop.stop 会让 run_until_complete 抛 RuntimeError、
    # run_forever 协程悬挂销毁，systemd 记 exit=1（2026-08-30 22:07 实机噪音）
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    try:
        loop.run_until_complete(run_forever(stop))
    finally:
        loop.close()


if __name__ == "__main__":
    main()
