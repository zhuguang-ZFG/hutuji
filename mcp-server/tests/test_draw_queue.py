"""draw_queue + draw-worker 契约测试（memory:// 后端）。"""
from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

from hutuji_mcp import draw_queue
from hutuji_mcp.config import Settings, load_settings


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


@pytest.fixture
def memory_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    base = load_settings()
    patched = Settings(
        **{
            **base.__dict__,
            "draw_queue_url": "memory://",
            "draw_queue_max_depth": 10,
            "draw_enqueue_per_second": 100,
        }
    )
    monkeypatch.setattr("hutuji_mcp.config.settings", patched)
    monkeypatch.setattr("hutuji_mcp.draw_queue.settings", patched)
    draw_queue.reset_backend_for_tests()


def test_queue_single_flight_and_worker(memory_queue, monkeypatch):
    calls: list[tuple[str, bool]] = []

    async def fake_run(
        description: str,
        public_base_url: str,
        *,
        job_id: str | None = None,
        chain_push: bool = True,
        machine: str = "",
    ) -> str:
        calls.append((description, chain_push))
        return json.dumps({"url": f"{public_base_url}/files/draw_test.gcode"}, ensure_ascii=False)

    monkeypatch.setattr("hutuji_mcp.server._run_draw_job", fake_run)

    async def run_case() -> None:
        job_a = await draw_queue.attach_or_enqueue("一只猫", "https://hutuji.example")
        job_b = await draw_queue.attach_or_enqueue("一只猫", "https://hutuji.example")
        assert job_a == job_b

        from hutuji_mcp.draw_worker import _process_one

        assert await _process_one() is True
        result = await draw_queue.wait_result(job_a, timeout=5.0)
        payload = json.loads(result)
        assert payload["url"].endswith("draw_test.gcode")
        assert calls == [("一只猫", True)]

    asyncio_run(run_case())


def test_queue_busy_when_full(memory_queue):
    async def run_case() -> None:
        for i in range(10):
            await draw_queue.attach_or_enqueue(f"主题{i}", "https://hutuji.example")

        with pytest.raises(draw_queue.DrawQueueBusyError):
            await draw_queue.attach_or_enqueue("溢出", "https://hutuji.example")

    asyncio_run(run_case())


@pytest.mark.parametrize(
    ("existing", "status", "expected"),
    [
        (None, None, "create"),
        (None, "pending", "create"),  # 无 inflight 时 status 无关紧要
        ("j1", "pending", "attach"),
        ("j1", "processing", "attach"),
        ("j1", "done", "attach"),
        ("j1", "failed", "replace"),
        ("j1", None, "replace"),  # job_key 已过期/消失（JOB<INFLIGHT TTL 窗口）
        ("j1", "mystery", "replace"),  # 未知状态一律换新，不钉死调用方
    ],
)
def test_decide_attach_matrix(existing, status, expected):
    assert draw_queue._decide_attach(existing, status) == expected














def test_real_redis_old_cleanup_cannot_remove_new_inflight(live_redis):
    url, client = live_redis
    backend = draw_queue.RedisDrawQueueBackend(url)
    key = draw_queue._inflight_key("相同主题")
    client.set(key, "new-job")

    async def run():
        await backend.clear_inflight("相同主题", "old-job")
        assert await backend._client().get(key) == "new-job"
        await backend.clear_inflight("相同主题", "new-job")
        assert await backend._client().get(key) is None
        await backend._client().aclose()
    asyncio_run(run())


def test_real_redis_invalid_payload_settles_and_allows_retry(live_redis, memory_queue):
    url, client = live_redis
    backend = draw_queue.RedisDrawQueueBackend(url)

    async def run():
        job_id = await backend.attach_or_enqueue("猫", "https://example.test")
        value = json.loads(client.rpop(draw_queue.QUEUE_KEY))
        value["chain_push"] = "false"
        raw = json.dumps(value)
        # 模拟旧生产者写下类型错误但可归属的原始记录。
        client.hset(draw_queue._job_key(job_id), "payload_sha256", hashlib.sha256(raw.encode()).hexdigest())
        client.lpush(draw_queue.QUEUE_KEY, raw)
        assert await backend.dequeue(timeout=1) is None
        with pytest.raises(RuntimeError, match="任务记录无效"):
            await backend.wait_result(job_id, timeout=1)
        replacement = await backend.attach_or_enqueue("猫", "https://example.test")
        assert replacement != job_id
        assert (await backend.dequeue(timeout=1)).job_id == replacement
        await backend._client().aclose()
    asyncio_run(run())


def test_real_redis_forged_bad_payload_cannot_fail_normal_job(live_redis, memory_queue):
    url, client = live_redis
    backend = draw_queue.RedisDrawQueueBackend(url)

    async def run():
        job_id = await backend.attach_or_enqueue("猫", "https://example.test")
        client.rpush(draw_queue.QUEUE_KEY, json.dumps({"job_id": job_id, "description": None}))
        assert await backend.dequeue(timeout=1) is None
        assert client.hget(draw_queue._job_key(job_id), "status") == "pending"
        assert (await backend.dequeue(timeout=1)).job_id == job_id
        await backend._client().aclose()
    asyncio_run(run())


def test_real_redis_concurrent_retries_charge_one_slot(live_redis, memory_queue, monkeypatch):
    import asyncio
    from dataclasses import replace

    monkeypatch.setattr(draw_queue, "settings", replace(draw_queue.settings,
        draw_queue_max_depth=1, draw_per_device_minute=1, draw_per_device_day=1))
    url, client = live_redis
    backend = draw_queue.RedisDrawQueueBackend(url)

    async def run():
        replies = await asyncio.gather(*(backend.attach_or_enqueue(
            "同一幅猫", "https://example.test", device_id="same-device") for _ in range(32)),
            return_exceptions=True)
        assert all(isinstance(item, str) for item in replies), replies
        assert len(set(replies)) == 1
        assert client.llen(draw_queue.QUEUE_KEY) == 1
        assert len(list(client.scan_iter(draw_queue.JOB_PREFIX + "*"))) == 1
        day = f"{draw_queue.DEVICE_RATE_PREFIX}day:same-device:{draw_queue.day_stamp_cst()}"
        assert client.get(day) == "1"
        await backend._client().aclose()
    asyncio_run(run())


def test_real_redis_queue_depth_is_atomic_under_contention(live_redis, memory_queue, monkeypatch):
    import asyncio
    from dataclasses import replace

    monkeypatch.setattr(draw_queue, "settings", replace(draw_queue.settings, draw_queue_max_depth=3))
    url, client = live_redis
    backend = draw_queue.RedisDrawQueueBackend(url)

    async def run():
        replies = await asyncio.gather(*(backend.attach_or_enqueue(
            f"主题{i}", "https://example.test", device_id="one-device") for i in range(20)),
            return_exceptions=True)
        assert sum(isinstance(item, str) for item in replies) == 3
        assert all(isinstance(item, (str, draw_queue.DrawQueueBusyError)) for item in replies)
        assert client.llen(draw_queue.QUEUE_KEY) == 3
        day = f"{draw_queue.DEVICE_RATE_PREFIX}day:one-device:{draw_queue.day_stamp_cst()}"
        assert client.get(day) == "3"
        await backend._client().aclose()
    asyncio_run(run())


def test_real_redis_lost_enqueue_reply_can_attach_to_a_durable_task(live_redis, memory_queue, monkeypatch):
    import redis

    url, client = live_redis
    backend = draw_queue.RedisDrawQueueBackend(url)

    async def run():
        connection = backend._client()
        original_pipeline, original_eval = connection.pipeline, connection.eval
        lost = False

        async def lose_eval_reply(*args, **kwargs):
            nonlocal lost
            reply = await original_eval(*args, **kwargs)
            if not lost:
                lost = True
                raise redis.ConnectionError("模拟事务完成但响应丢失")
            return reply

        def lose_pipeline_reply(*args, **kwargs):
            pipe = original_pipeline(*args, **kwargs)
            execute = pipe.execute

            async def lose(*args, **kwargs):
                nonlocal lost
                reply = await execute(*args, **kwargs)
                if not lost:
                    lost = True
                    raise redis.ConnectionError("模拟事务完成但响应丢失")
                return reply
            pipe.execute = lose
            return pipe

        monkeypatch.setattr(connection, "eval", lose_eval_reply)
        monkeypatch.setattr(connection, "pipeline", lose_pipeline_reply)
        with pytest.raises(redis.ConnectionError):
            await backend.attach_or_enqueue("事务响应丢失", "https://example.test")
        retried = await backend.attach_or_enqueue("事务响应丢失", "https://example.test")
        assert client.llen(draw_queue.QUEUE_KEY) == 1
        assert (await backend.dequeue(timeout=1)).job_id == retried
        await connection.aclose()
    asyncio_run(run())


@pytest.mark.parametrize("forged", [False, True])
def test_real_redis_duplicate_or_modified_payload_never_runs_twice(live_redis, memory_queue, monkeypatch, forged):
    import asyncio
    from hutuji_mcp import draw_worker

    url, client = live_redis
    backend = draw_queue.RedisDrawQueueBackend(url)
    monkeypatch.setattr(draw_queue, "_backend", backend)
    calls = []

    async def generate(description, *args, **kwargs):
        calls.append(description)
        await asyncio.sleep(0.02)
        return '{"url":"https://example.test/files/result.gcode"}'

    monkeypatch.setattr("hutuji_mcp.server._run_draw_job", generate)

    async def run():
        job_id = await backend.attach_or_enqueue("原始主题", "https://example.test")
        raw = client.lindex(draw_queue.QUEUE_KEY, 0)
        if forged:
            changed = json.loads(raw)
            changed["description"] = "被替换的主题"
            raw = json.dumps(changed, ensure_ascii=False)
        client.rpush(draw_queue.QUEUE_KEY, raw)
        results = await asyncio.gather(draw_worker._process_one(), draw_worker._process_one())
        assert sorted(results) == [False, True]
        assert calls == ["原始主题"]
        assert json.loads(await backend.wait_result(job_id, timeout=1))["url"].endswith("result.gcode")
        await backend._client().aclose()
    asyncio_run(run())


def test_real_redis_worker_lost_before_claim_is_recovered(live_redis, memory_queue, monkeypatch):
    url, _ = live_redis
    backend = draw_queue.RedisDrawQueueBackend(url)
    now = time.time()

    async def run():
        job_id = await backend.attach_or_enqueue("领走后进程退出", "https://example.test")
        assert (await backend.dequeue(timeout=1)).job_id == job_id
        # 模拟进程在领取记录后、实际执行前退出；其他 worker 可找回同一个任务。
        monkeypatch.setattr(draw_queue.time, "time", lambda: now + 60)
        recovered = await backend.dequeue(timeout=1)
        assert recovered is not None and recovered.job_id == job_id
        await backend._client().aclose()
    asyncio_run(run())


def test_real_redis_late_completion_cannot_recreate_expired_job(live_redis, memory_queue):
    url, client = live_redis
    backend = draw_queue.RedisDrawQueueBackend(url)

    async def run():
        job_id = await backend.attach_or_enqueue("过期任务", "https://example.test")
        client.delete(draw_queue._job_key(job_id))
        await backend.mark_done(job_id, "late result")
        await backend.mark_failed(job_id, ValueError("late failure"))
        assert not client.exists(draw_queue._job_key(job_id))
        await backend._client().aclose()
    asyncio_run(run())


def test_real_redis_lost_claim_reply_preserves_owner_and_result(live_redis, memory_queue, monkeypatch):
    import redis

    url, _ = live_redis
    backend = draw_queue.RedisDrawQueueBackend(url)

    async def run():
        job_id = await backend.attach_or_enqueue("领取响应丢失", "https://example.test")
        payload = await backend.dequeue(timeout=1)
        connection = backend._client()
        original = connection.eval
        lost = False

        async def lose_reply(*args, **kwargs):
            nonlocal lost
            result = await original(*args, **kwargs)
            if args[0] == draw_queue._CLAIM_SCRIPT and not lost:
                lost = True
                raise redis.ConnectionError("模拟领取响应丢失")
            return result

        monkeypatch.setattr(connection, "eval", lose_reply)
        claim = await backend.mark_processing(job_id, payload_sha256=payload.payload_sha256)
        assert lost and claim
        assert await backend.mark_processing(job_id, payload_sha256=payload.payload_sha256) is None
        assert not await backend.mark_done(job_id, "wrong", claim_token="other-worker")
        assert await backend.mark_done(job_id, "correct", claim_token=claim)
        assert not await backend.mark_failed(job_id, ValueError("late"), claim_token=claim)
        assert await backend.wait_result(job_id, timeout=1) == "correct"
        await connection.aclose()
    asyncio_run(run())


@pytest.mark.parametrize("after_write", [False, True])
@pytest.mark.parametrize("failed", [False, True])
def test_real_redis_finish_retries_same_outcome(live_redis, memory_queue, monkeypatch, after_write, failed):
    """断连与丢响应均只重试结算；凭证、结果或错误不同均不能修改终态。"""
    import redis

    url, client = live_redis
    backend = draw_queue.RedisDrawQueueBackend(url)

    async def run():
        job_id = await backend.attach_or_enqueue("结算丢包", "https://example.test")
        payload = await backend.dequeue(timeout=1)
        claim = await backend.mark_processing(job_id, payload_sha256=payload.payload_sha256)
        connection = backend._client()
        original = connection.eval
        attempts = 0

        async def interrupted(*args, **kwargs):
            nonlocal attempts
            if args[0] == draw_queue._FINISH_SCRIPT:
                attempts += 1
                if attempts == 1:
                    if after_write:
                        await original(*args, **kwargs)
                    raise redis.ConnectionError("模拟结算连接中断")
            return await original(*args, **kwargs)

        monkeypatch.setattr(connection, "eval", interrupted)
        async def finish(token=claim):
            if failed:
                return await backend.mark_failed(job_id, ValueError("原始失败"), claim_token=token)
            return await backend.mark_done(job_id, "原始结果", claim_token=token)

        assert await finish()
        assert attempts == 2
        before = client.hgetall(draw_queue._job_key(job_id))
        assert before["status"] == ("failed" if failed else "done")
        assert await finish()
        assert not await finish("其他 worker")
        assert not await finish("")
        assert not await backend.mark_done(job_id, "另一结果", claim_token=claim)
        assert not await backend.mark_failed(job_id, ValueError("另一失败"), claim_token=claim)
        assert client.hgetall(draw_queue._job_key(job_id)) == before
        await connection.aclose()
    asyncio_run(run())


def test_worker_completion_outage_never_becomes_generation_failure(live_redis, memory_queue, monkeypatch):
    """已经生成的结果不能因 Redis 回写失败开放重画，也不能污染失败台账。"""
    import redis
    from hutuji_mcp import draw_worker, job_ledger

    url, client = live_redis
    backend = draw_queue.RedisDrawQueueBackend(url)
    monkeypatch.setattr(draw_queue, "_backend", backend)
    generated, failures = [], []

    async def generate(*args, **kwargs):
        generated.append(args[0])
        return "已付费生成的结果"

    monkeypatch.setattr("hutuji_mcp.server._run_draw_job", generate)
    monkeypatch.setattr(job_ledger, "schedule_failed", lambda **kw: failures.append(kw))

    async def run():
        job_id = await backend.attach_or_enqueue("完成后断连", "https://example.test")
        connection = backend._client()
        original = connection.eval
        attempts = 0

        async def unavailable(*args, **kwargs):
            nonlocal attempts
            if args[0] == draw_queue._FINISH_SCRIPT and args[4] == "done":
                attempts += 1
                raise redis.ConnectionError("模拟持续写入故障")
            return await original(*args, **kwargs)

        monkeypatch.setattr(connection, "eval", unavailable)
        with pytest.raises(redis.ConnectionError):
            await draw_worker._process_one()
        assert attempts == 2
        assert generated == ["完成后断连"] and failures == []
        assert client.hget(draw_queue._job_key(job_id), "status") == "processing"
        assert await backend.attach_or_enqueue("完成后断连", "https://example.test") == job_id
        assert client.llen(draw_queue.QUEUE_KEY) == 0
        await connection.aclose()
    asyncio_run(run())


def test_real_redis_invalid_queue_type_cannot_partially_charge(live_redis, memory_queue):
    import redis

    url, client = live_redis
    client.set(draw_queue.QUEUE_KEY, "损坏的队列类型")
    backend = draw_queue.RedisDrawQueueBackend(url)

    async def run():
        with pytest.raises(redis.ResponseError):
            await backend.attach_or_enqueue("队列损坏", "https://example.test", device_id="test-device")
        assert list(client.scan_iter(draw_queue.JOB_PREFIX + "*")) == []
        assert list(client.scan_iter(draw_queue.ENQUEUE_RATE_PREFIX + "*")) == []
        assert list(client.scan_iter(draw_queue.DEVICE_RATE_PREFIX + "*")) == []
        await backend._client().aclose()
    asyncio_run(run())


def test_worker_cancellation_settles_without_swallowing_cancel(memory_queue, monkeypatch):
    import asyncio
    from hutuji_mcp import draw_worker

    async def run():
        entered = asyncio.Event()

        async def generate(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr("hutuji_mcp.server._run_draw_job", generate)
        job_id = await draw_queue.attach_or_enqueue("取消 worker", "https://example.test")
        worker = asyncio.create_task(draw_worker._process_one())
        await entered.wait()
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker
        backend = draw_queue.get_backend()
        assert backend._jobs[job_id]["status"] == "failed"
        assert await draw_queue.attach_or_enqueue("取消 worker", "https://example.test") != job_id
    asyncio_run(run())


@pytest.mark.parametrize("stale_status", [None, "failed"])
def test_redis_attach_replaces_dead_inflight(memory_queue, live_redis, stale_status):
    url, client = live_redis
    dead_id = "d" * 22
    inflight = draw_queue._inflight_key("一只猫")
    client.set(inflight, dead_id)
    if stale_status is not None:
        client.hset(draw_queue._job_key(dead_id), "status", stale_status)
    backend = draw_queue.RedisDrawQueueBackend(url)

    async def run_case():
        job_id = await backend.attach_or_enqueue("一只猫", "https://hutuji.example")
        assert job_id != dead_id
        assert client.get(inflight) == job_id
        assert client.hget(draw_queue._job_key(job_id), "status") == "pending"
        assert client.llen(draw_queue.QUEUE_KEY) == 1
        await backend._client().aclose()
    asyncio_run(run_case())



def test_redis_attach_reuses_live_inflight(memory_queue, live_redis):
    url, client = live_redis
    live_id = "l" * 22
    inflight = draw_queue._inflight_key("一只猫")
    client.set(inflight, live_id)
    client.hset(draw_queue._job_key(live_id), "status", "processing")
    backend = draw_queue.RedisDrawQueueBackend(url)

    async def run_case():
        assert await backend.attach_or_enqueue("一只猫", "https://hutuji.example") == live_id
        assert client.llen(draw_queue.QUEUE_KEY) == 0
        await backend._client().aclose()
    asyncio_run(run_case())



@pytest.mark.parametrize("winner_status", ["pending", None])
def test_redis_competing_replacement_preserves_live_winner(memory_queue, live_redis, winner_status):
    import asyncio

    url, client = live_redis
    backend = draw_queue.RedisDrawQueueBackend(url)
    winner_id = "w" * 22
    key = draw_queue._inflight_key("一只猫")
    client.set(key, winner_id)
    if winner_status is not None:
        client.hset(draw_queue._job_key(winner_id), "status", winner_status)

    async def run_case():
        first, second = await asyncio.gather(*(backend.attach_or_enqueue(
            "一只猫", "https://hutuji.example") for _ in range(2)))
        assert first == second == client.get(key)
        assert (first == winner_id) is (winner_status is not None)
        assert client.llen(draw_queue.QUEUE_KEY) == (0 if winner_status is not None else 1)
        await backend._client().aclose()
    asyncio_run(run_case())



def test_worker_run_forever_clean_stop(monkeypatch):
    """stop 事件置位后消费循环干净返回（SIGTERM 不再 loop.stop 裸死 exit=1）。"""
    import asyncio
    from types import SimpleNamespace

    from hutuji_mcp import draw_worker, queue_metrics

    monkeypatch.setattr(draw_worker, "queue_enabled", lambda: True)
    monkeypatch.setattr(
        draw_worker, "settings", SimpleNamespace(draw_queue_url="redis://stub")
    )

    async def _fake_metrics(stop, *, role):
        await stop.wait()

    monkeypatch.setattr(queue_metrics, "metrics_loop", _fake_metrics)

    stop = asyncio.Event()
    calls = 0

    async def _fake_process_one():
        nonlocal calls
        calls += 1
        if calls >= 3:
            stop.set()
        return False

    monkeypatch.setattr(draw_worker, "_process_one", _fake_process_one)
    asyncio_run(draw_worker.run_forever(stop))
    assert calls == 3


def test_worker_stop_mid_job_finishes_current(monkeypatch):
    """停止事件在 job 处理中到达：当前 job 跑完后才在迭代边界退出。"""
    import asyncio
    from types import SimpleNamespace

    from hutuji_mcp import draw_worker, queue_metrics

    monkeypatch.setattr(draw_worker, "queue_enabled", lambda: True)
    monkeypatch.setattr(
        draw_worker, "settings", SimpleNamespace(draw_queue_url="redis://stub")
    )

    async def _fake_metrics(stop, *, role):
        await stop.wait()

    monkeypatch.setattr(queue_metrics, "metrics_loop", _fake_metrics)

    stop = asyncio.Event()
    finished: list[str] = []

    async def _fake_process_one():
        stop.set()  # 信号在 job 处理中到达
        finished.append("job")
        return True

    monkeypatch.setattr(draw_worker, "_process_one", _fake_process_one)
    asyncio_run(draw_worker.run_forever(stop))
    assert finished == ["job"]

def test_worker_main_hard_exits_after_clean_stop(monkeypatch):
    """干净停机路径必须以 os._exit(0) 收尾，跳过 Py_FinalizeEx 全堆遍历。
    钉死 2026-09-02 生产事故根因：8/8 worker 堆被换出后，终结化逐页换回
    拖 267–298s。若 main() 回到自然 return，本测试失败。"""
    from hutuji_mcp import draw_worker

    class _HardExit(Exception):
        pass

    exits: list[int] = []

    def _fake_exit(code: int) -> None:
        exits.append(code)
        raise _HardExit  # os._exit 不返回；用异常模拟终止

    async def _ok_run_forever(stop=None):
        return None

    monkeypatch.setattr(draw_worker.os, "_exit", _fake_exit)
    monkeypatch.setattr(draw_worker.logging, "shutdown", lambda: None)
    monkeypatch.setattr(draw_worker, "run_forever", _ok_run_forever)
    with pytest.raises(_HardExit):
        draw_worker.main()
    assert exits == [0]


def test_worker_main_error_path_never_hard_exits(monkeypatch):
    """run_forever 抛错（含 SystemExit 未配置队列）时必须走解释器正常退出，
    不得被 os._exit(0) 吞掉错误码——配合 test_worker_main_wiring_subprocess
    钉死「缺队列地址 exit 1 + 文案」语义不被硬退出破坏。"""
    from hutuji_mcp import draw_worker

    exits: list[int] = []
    monkeypatch.setattr(draw_worker.os, "_exit", lambda code: exits.append(code))
    monkeypatch.setattr(draw_worker.logging, "shutdown", lambda: None)

    async def _boom(stop=None):
        raise SystemExit("HUTUJI_DRAW_QUEUE_URL 未配置，draw-worker 无法启动")

    monkeypatch.setattr(draw_worker, "run_forever", _boom)
    with pytest.raises(SystemExit):
        draw_worker.main()
    assert exits == []



def test_chain_push_flag_separates_jobs_and_propagates(memory_queue, monkeypatch):
    """同一描述 chain_push=True/False 是两个独立 job（网页生图不得与语音
    单飞共享，否则链推演示设备与否取决于谁先入队），且标志原样传到
    worker 的 _run_draw_job。"""
    calls: list[tuple[str, bool]] = []

    async def fake_run(
        description: str,
        public_base_url: str,
        *,
        job_id: str | None = None,
        chain_push: bool = True,
        machine: str = "",
    ) -> str:
        calls.append((description, chain_push))
        return json.dumps({"url": f"{public_base_url}/files/draw_test.gcode"})

    monkeypatch.setattr("hutuji_mcp.server._run_draw_job", fake_run)

    async def run_case() -> None:
        voice_a = await draw_queue.attach_or_enqueue("一只猫", "https://hutuji.example")
        web = await draw_queue.attach_or_enqueue(
            "一只猫", "https://hutuji.example", chain_push=False
        )
        voice_b = await draw_queue.attach_or_enqueue("一只猫", "https://hutuji.example")
        assert voice_a == voice_b  # 同标志同题搭车
        assert web != voice_a  # 不同标志必须分开

        from hutuji_mcp.draw_worker import _process_one

        assert await _process_one() is True
        assert await _process_one() is True
        assert sorted(calls) == [("一只猫", False), ("一只猫", True)]

        for job_id in (voice_a, web):
            payload = json.loads(await draw_queue.wait_result(job_id, timeout=5.0))
            assert payload["url"].endswith("draw_test.gcode")

    asyncio_run(run_case())


def test_redis_dequeue_chain_push_defaults_true_for_old_payload(memory_queue, live_redis):
    url, client = live_redis
    backend = draw_queue.RedisDrawQueueBackend(url)
    client.lpush(draw_queue.QUEUE_KEY,
        json.dumps({"job_id": "o" * 22, "description": "d", "public_base_url": "u"}),
        json.dumps({"job_id": "n" * 22, "description": "d", "public_base_url": "u", "chain_push": False}))

    async def run_case():
        legacy = await backend.dequeue(timeout=1)
        modern = await backend.dequeue(timeout=1)
        assert legacy is not None and legacy.chain_push is True
        assert modern is not None and modern.chain_push is False
        await backend._client().aclose()
    asyncio_run(run_case())



def test_redis_attach_records_chain_push_and_separates_inflight(memory_queue, live_redis):
    url, client = live_redis
    backend = draw_queue.RedisDrawQueueBackend(url)

    async def run_case():
        voice = await backend.attach_or_enqueue("一只猫", "https://hutuji.example")
        web = await backend.attach_or_enqueue("一只猫", "https://hutuji.example", chain_push=False)
        assert voice != web
        assert client.get(draw_queue._inflight_key("一只猫", True)) == voice
        queued = {json.loads(item)["job_id"]: json.loads(item)
                  for item in client.lrange(draw_queue.QUEUE_KEY, 0, -1)}
        assert queued[voice]["chain_push"] is True
        assert queued[web]["chain_push"] is False
        assert client.get(draw_queue._inflight_key("一只猫", False)) == web
        await backend.clear_inflight("一只猫", web, chain_push=False)
        assert client.get(draw_queue._inflight_key("一只猫", False)) is None
        assert client.get(draw_queue._inflight_key("一只猫", True)) == voice
        await backend._client().aclose()
    asyncio_run(run_case())


def test_draw_queue_url_validation():
    with pytest.raises(ValueError, match="HUTUJI_DRAW_QUEUE_URL"):
        Settings(
            llm_base_url="https://api.example/v1",
            llm_api_key="k",
            llm_model="m",
            output_dir=Path("/tmp/out"),
            public_base_url="https://hutuji.example",
            host="127.0.0.1",
            port=8300,
            output_ttl_seconds=600,
            draw_queue_url="ftp://bad",
        )


def test_bench_draw_load_script_smoke():
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    if not py.is_file():
        py = Path(sys.executable)
    proc = subprocess.run(
        [
            str(py),
            str(ROOT / "scripts" / "bench_draw_load.py"),
            "--rps",
            "5",
            "--duration",
            "2",
            "--workers",
            "4",
            "--job-ms",
            "100",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PASS" in proc.stdout


def test_worker_main_wiring_subprocess():
    """main() 接线端到端冒烟：缺队列地址必须走 SystemExit 文案退出，
    不得出现 NameError/Traceback——钉死 2026-08-30 生产事故
    （main() 误删 loop 创建行，worker 全量 NameError 崩溃回环）。
    单元测试只调 run_forever，从不经过 main()，故须子进程真起。"""
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    env = {k: v for k, v in os.environ.items() if k != "HUTUJI_DRAW_QUEUE_URL"}
    # 门禁将子进程输出设为 UTF-8；父进程也须显式同编码，避免 Windows 按 GBK 解码中文。
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [str(py), "-m", "hutuji_mcp.draw_worker"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
        env=env,
    )
    combined = proc.stdout + proc.stderr
    assert "Traceback" not in combined, combined
    assert "未配置" in combined, combined
