"""Redis 绘图任务队列：MCP 入队、draw-worker 消费、跨实例 single-flight。

未配置 HUTUJI_DRAW_QUEUE_URL 时本模块不被 server 调用；测试可用 memory://。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import time
import weakref
from dataclasses import dataclass
from typing import Any, Protocol

from .config import settings

logger = logging.getLogger(__name__)

# inflight 允许略长于 job：残留窗口由 _decide_attach 的 replace 分支兜底
# （status 缺失即删残留重入队）；反过来 inflight 先过期会让仍在 processing 的
# 同题任务被重复入队——那是付费生成，代价高于一次 replace。
JOB_TTL_SECONDS = 600
INFLIGHT_TTL_SECONDS = 620
QUEUE_KEY = "hutuji:draw:queue"
INFLIGHT_PREFIX = "hutuji:draw:inflight:"
JOB_PREFIX = "hutuji:draw:job:"
ENQUEUE_RATE_PREFIX = "hutuji:draw:enqueue:"
DEVICE_RATE_PREFIX = "hutuji:draw:device:"


class DrawQueueBusyError(RuntimeError):
    """队列已满或全局入队速率超限。"""


@dataclass(frozen=True)
class DrawJobPayload:
    job_id: str
    description: str
    public_base_url: str
    # chain_push=False 表示「完成时不链式推送演示设备」（draw-portal 网页生图：
    # 推送由买家在页面上显式触发，不该串到运营 demo 机的预览）。
    # 2026-08-31 新增字段；缺省 True 保持语音路径与旧 payload 行为不变。
    chain_push: bool = True


def _description_hash(description: str) -> str:
    return hashlib.sha256(description.encode("utf-8")).hexdigest()


def _inflight_material(description: str, chain_push: bool) -> str:
    """inflight 去重键的原料：chain_push 不同的同名任务不得互相搭车。

    语音（chain_push=True）搭上网页（False）的任务，完成时不会推设备预览，
    语音用户干等；反向则网页任务串推到演示设备。分开计键两边各自独立。
    """
    return description if chain_push else description + "\x00nopush"


def queue_enabled() -> bool:
    return bool(getattr(settings, "draw_queue_url", "").strip())


def _job_key(job_id: str) -> str:
    return f"{JOB_PREFIX}{job_id}"


def _inflight_key(description: str, chain_push: bool = True) -> str:
    return f"{INFLIGHT_PREFIX}{_description_hash(_inflight_material(description, chain_push))}"

def _decide_attach(existing_job_id: str | None, status: str | None) -> str:
    """同题 inflight 的处置决策（纯函数，两后端共用，便于参数化回归）。

    返回：
    - "attach"：搭车复用进行中的任务；
    - "create"：无 inflight，直接新建；
    - "replace"：inflight 指向 failed 或 job_key 已消失的死任务（TTL 窗口、
      worker 崩溃在 mark_failed 与 clear_inflight 之间）——删除残留后重新入队，
      不得把调用方钉到死任务上（那会让重试立即收到过期失败或「未知任务」）。
    """
    if existing_job_id is None:
        return "create"
    if status in ("pending", "processing", "done"):
        return "attach"
    return "replace"


class DrawQueueBackend(Protocol):
    async def attach_or_enqueue(
        self, description: str, public_base_url: str, *, device_id: str = "", chain_push: bool = True
    ) -> str: ...

    async def wait_result(self, job_id: str, *, timeout: float) -> str: ...

    async def dequeue(self, *, timeout: float) -> DrawJobPayload | None: ...

    async def mark_processing(self, job_id: str) -> None: ...

    async def mark_done(self, job_id: str, result: str) -> None: ...

    async def mark_failed(self, job_id: str, exc: BaseException) -> None: ...

    async def clear_inflight(self, description: str, job_id: str, *, chain_push: bool = True) -> None: ...


class MemoryDrawQueueBackend:
    """单进程测试后端；不支持跨进程 worker。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._queue: asyncio.Queue[DrawJobPayload | None] = asyncio.Queue()
        self._jobs: dict[str, dict[str, str]] = {}
        self._inflight: dict[str, str] = {}
        self._enqueue_times: list[float] = []
        self._device_minute: dict[str, int] = {}

    async def attach_or_enqueue(
        self, description: str, public_base_url: str, *, device_id: str = "", chain_push: bool = True
    ) -> str:
        async with self._lock:
            inflight_key = _description_hash(_inflight_material(description, chain_push))
            existing = self._inflight.get(inflight_key)
            if existing is not None:
                job = self._jobs.get(existing)
                # replace 与 create 都落到下方新建分支：inflight 赋值即覆盖残留
                if _decide_attach(existing, job.get("status") if job else None) == "attach":
                    return existing
            await self._check_enqueue_limits_locked(device_id)
            job_id = secrets.token_urlsafe(16)
            self._jobs[job_id] = {
                "status": "pending",
                "description": description,
                "public_base_url": public_base_url,
                "device_id": device_id,
            }
            self._inflight[inflight_key] = job_id
            payload = DrawJobPayload(job_id, description, public_base_url, chain_push)
        await self._queue.put(payload)
        return job_id

    async def _check_enqueue_limits_locked(self, device_id: str) -> None:
        max_depth = settings.draw_queue_max_depth
        if self._queue.qsize() >= max_depth:
            raise DrawQueueBusyError("绘图服务正忙，请稍候再试")
        now = time.monotonic()
        self._enqueue_times = [t for t in self._enqueue_times if now - t < 1.0]
        if len(self._enqueue_times) >= settings.draw_enqueue_per_second:
            raise DrawQueueBusyError("绘图服务正忙，请稍候再试")
        self._enqueue_times.append(now)
        if device_id:
            minute = int(time.time() // 60)
            bucket = f"{device_id}:{minute}"
            count = self._device_minute.get(bucket, 0) + 1
            if count > settings.draw_per_device_minute:
                raise DrawQueueBusyError("绘图服务正忙，请稍候再试")
            self._device_minute[bucket] = count

    async def wait_result(self, job_id: str, *, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            async with self._lock:
                job = self._jobs.get(job_id)
                if job is None:
                    raise RuntimeError(f"未知任务 {job_id}")
                status = job.get("status")
                if status == "done":
                    return job["result"]
                if status == "failed":
                    raise _exception_from_job(job)
            await asyncio.sleep(0.05)
        raise asyncio.TimeoutError

    async def dequeue(self, *, timeout: float) -> DrawJobPayload | None:
        try:
            item = await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        return item

    async def mark_processing(self, job_id: str) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job["status"] = "processing"

    async def mark_done(self, job_id: str, result: str) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job["status"] = "done"
                job["result"] = result

    async def mark_failed(self, job_id: str, exc: BaseException) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job["status"] = "failed"
                job["error_type"] = type(exc).__name__
                job["error_message"] = str(exc)

    async def clear_inflight(self, description: str, job_id: str, *, chain_push: bool = True) -> None:
        async with self._lock:
            inflight_key = _description_hash(_inflight_material(description, chain_push))
            if self._inflight.get(inflight_key) == job_id:
                del self._inflight[inflight_key]


class RedisDrawQueueBackend:
    def __init__(self, url: str) -> None:
        self._url = url
        self._clients: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, Any
        ] = weakref.WeakKeyDictionary()

    def _client(self):
        import redis.asyncio as redis

        loop = asyncio.get_running_loop()
        client = self._clients.get(loop)
        if client is None:
            client = redis.from_url(self._url, decode_responses=True)
            self._clients[loop] = client
        return client

    async def attach_or_enqueue(
        self, description: str, public_base_url: str, *, device_id: str = "", chain_push: bool = True
    ) -> str:
        client = self._client()
        inflight = _inflight_key(description, chain_push)

        existing = await client.get(inflight)
        status = await client.hget(_job_key(existing), "status") if existing else None
        decision = _decide_attach(existing, status)
        if decision == "attach":
            return existing

        # 限深/限流只在首次判定后计一次；replace 重试不重复扣配额。
        # 已知不对称：首轮判 create/replace 后扣了配额，NX 冲突重判仍可能 attach
        # 到并发 winner——即一次搭车也占一格配额（memory 后端搭车不占）。仅在
        # 真并发竞争下发生，额度是软限制，移动检查点反而要在占坑后回滚，不值。
        depth = await client.llen(QUEUE_KEY)
        if depth >= settings.draw_queue_max_depth:
            raise DrawQueueBusyError("绘图服务正忙，请稍候再试")

        now_sec = int(time.time())
        rate_key = f"{ENQUEUE_RATE_PREFIX}{now_sec}"
        count = await client.incr(rate_key)
        if count == 1:
            await client.expire(rate_key, 2)
        if count > settings.draw_enqueue_per_second:
            raise DrawQueueBusyError("绘图服务正忙，请稍候再试")

        if device_id:
            minute = now_sec // 60
            device_key = f"{DEVICE_RATE_PREFIX}{device_id}:{minute}"
            device_count = await client.incr(device_key)
            if device_count == 1:
                await client.expire(device_key, 120)
            if device_count > settings.draw_per_device_minute:
                raise DrawQueueBusyError("绘图服务正忙，请稍候再试")

        for _attempt in range(3):
            if decision == "replace":
                # 仅当 inflight 仍指向同一死任务才删，避免误删并发 winner 的新 inflight
                if await client.get(inflight) == existing:
                    await client.delete(inflight)
            job_id = secrets.token_urlsafe(16)
            job_key = _job_key(job_id)
            pipe = client.pipeline()
            pipe.hset(
                job_key,
                mapping={
                    "status": "pending",
                    "description": description,
                    "public_base_url": public_base_url,
                    "device_id": device_id,
                },
            )
            pipe.expire(job_key, JOB_TTL_SECONDS)
            pipe.set(inflight, job_id, nx=True, ex=INFLIGHT_TTL_SECONDS)
            results = await pipe.execute()
            if results[-1]:
                payload = json.dumps(
                    {
                        "job_id": job_id,
                        "description": description,
                        "public_base_url": public_base_url,
                        "chain_push": chain_push,
                    },
                    ensure_ascii=False,
                )
                await client.lpush(QUEUE_KEY, payload)
                return job_id
            # NX 失败：并发 winner 已占位（本轮 hset 的孤儿 job_key 随 TTL 自清），
            # 重新判定；winner 仍是死任务则下一轮删残留再试
            existing = await client.get(inflight)
            status = await client.hget(_job_key(existing), "status") if existing else None
            decision = _decide_attach(existing, status)
            if decision == "attach":
                return existing
        raise DrawQueueBusyError("绘图服务正忙，请稍候再试")

    async def wait_result(self, job_id: str, *, timeout: float) -> str:
        client = self._client()
        job_key = _job_key(job_id)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            data = await client.hgetall(job_key)
            if not data:
                raise RuntimeError(f"未知任务 {job_id}")
            status = data.get("status")
            if status == "done":
                result = data.get("result")
                if result is None:
                    raise RuntimeError(f"任务 {job_id} 缺少 result")
                return result
            if status == "failed":
                raise _exception_from_job(data)
            await asyncio.sleep(0.2)
        raise asyncio.TimeoutError

    async def dequeue(self, *, timeout: float) -> DrawJobPayload | None:
        client = self._client()
        item = await client.brpop(QUEUE_KEY, timeout=max(1, int(timeout)))
        if item is None:
            return None
        _, raw = item
        data = json.loads(raw)
        return DrawJobPayload(
            job_id=str(data["job_id"]),
            description=str(data["description"]),
            public_base_url=str(data["public_base_url"]),
            # 旧 payload（本字段 2026-08-31 才加入）缺省 True，保持语音推送行为。
            chain_push=bool(data.get("chain_push", True)),
        )

    async def mark_processing(self, job_id: str) -> None:
        client = self._client()
        job_key = _job_key(job_id)
        await client.hset(job_key, "status", "processing")
        await client.expire(job_key, JOB_TTL_SECONDS)

    async def mark_done(self, job_id: str, result: str) -> None:
        client = self._client()
        job_key = _job_key(job_id)
        pipe = client.pipeline()
        pipe.hset(job_key, mapping={"status": "done", "result": result})
        pipe.expire(job_key, JOB_TTL_SECONDS)
        await pipe.execute()

    async def mark_failed(self, job_id: str, exc: BaseException) -> None:
        client = self._client()
        job_key = _job_key(job_id)
        pipe = client.pipeline()
        pipe.hset(
            job_key,
            mapping={
                "status": "failed",
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            },
        )
        pipe.expire(job_key, JOB_TTL_SECONDS)
        await pipe.execute()

    async def clear_inflight(self, description: str, job_id: str, *, chain_push: bool = True) -> None:
        client = self._client()
        inflight = _inflight_key(description, chain_push)
        current = await client.get(inflight)
        if current == job_id:
            await client.delete(inflight)


def _exception_from_job(job: dict[str, str]) -> BaseException:
    from .bitmap_svg import BitmapError
    from .gcode_gen import GcodeError
    from .server import DrawLimitError, DrawTimeoutError, PaperRiskExhaustedError

    mapping: dict[str, type[BaseException]] = {
        "DrawLimitError": DrawLimitError,
        "DrawTimeoutError": DrawTimeoutError,
        "BitmapError": BitmapError,
        "GcodeError": GcodeError,
        "PaperRiskExhaustedError": PaperRiskExhaustedError,
        "DrawQueueBusyError": DrawQueueBusyError,
    }
    error_type = job.get("error_type", "RuntimeError")
    message = job.get("error_message", "绘图任务失败")
    cls = mapping.get(error_type, RuntimeError)
    return cls(message)


_backend: DrawQueueBackend | None = None


def get_backend() -> DrawQueueBackend:
    global _backend
    if _backend is not None:
        return _backend
    url = getattr(settings, "draw_queue_url", "").strip()
    if url == "memory://":
        _backend = MemoryDrawQueueBackend()
    elif url:
        _backend = RedisDrawQueueBackend(url)
    else:
        raise RuntimeError("draw_queue 未配置")
    return _backend


def reset_backend_for_tests() -> None:
    global _backend
    _backend = None


async def attach_or_enqueue(
    description: str, public_base_url: str, *, device_id: str = "", chain_push: bool = True
) -> str:
    job_id = await get_backend().attach_or_enqueue(
        description, public_base_url, device_id=device_id, chain_push=chain_push
    )
    from . import job_ledger

    job_ledger.schedule_enqueue(
        job_id=job_id,
        description=description,
        public_base_url=public_base_url,
        device_id=device_id,
    )
    return job_id


async def wait_result(job_id: str, *, timeout: float) -> str:
    return await get_backend().wait_result(job_id, timeout=timeout)
