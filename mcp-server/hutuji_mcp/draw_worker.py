"""独立 draw-worker：从 Redis 队列消费并执行 _run_draw_job。

启动：python -m hutuji_mcp.draw_worker
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

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
            machine=job.machine,
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
        await backend.clear_inflight(
            job.description, job.job_id, chain_push=job.chain_push, machine=job.machine
        )
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

    # 干净停机跳过 Py_FinalizeEx：解释器终结化（GC 遍历/模块拆解）要读写整个堆，
    # 而跑过付费描摹的 worker 堆 HWM ~300MB，空闲数小时后基本整体被换出到
    # /www/swap 与 /swapfile（ROTA=1 云盘）——终结化须把这些页逐页换回。
    # 2026-09-02 22:01 生产实证：8/8 worker 消费循环 4s 内退出，进程却又挂
    # 267–298s（state=D、maj_flt 持续上升、VmSwap 同步回落）才消失，全部 exit 0；
    # 同 swap 状态改发 SIGKILL 则 0.1s 消失（内核直接丢 swap 槽，不读盘）。
    # 隔离复现同构：174MB 换出的单进程 TERM 比无换出慢 ~7s，8 进程并发
    # 1.2GB 换出时 +47s，py-spy 显示主线程处于无 Python 帧的 C 层活跃态。
    # 这里没有必须在终结化里做的事：日志逐行已 flush（下方再 shutdown 兜底），
    # Redis/PG 连接由内核回收，job_ledger/usage 线程本就是 daemon 不等其收尾。
    # 异常路径（含 SystemExit 未配置队列）不走到这里，保持解释器正常退出语义。
    logging.shutdown()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
