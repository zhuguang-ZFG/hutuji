"""Redis 绘图任务队列：MCP 入队、draw-worker 消费、跨实例 single-flight。

未配置 HUTUJI_DRAW_QUEUE_URL 时本模块不被 server 调用；测试可用 memory://。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import secrets
import time
import weakref
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from .config import settings
from .paper_config import PaperConfig

logger = logging.getLogger(__name__)

# inflight 允许略长于 job：残留窗口由 _decide_attach 的 replace 分支兜底
# （status 缺失即删残留重入队）；反过来 inflight 先过期会让仍在 processing 的
# 同题任务被重复入队——那是付费生成，代价高于一次 replace。
JOB_TTL_SECONDS = 600
INFLIGHT_TTL_SECONDS = 620
QUEUE_KEY = "hutuji:draw:queue"
PENDING_KEY = "hutuji:draw:pending"
PENDING_RECOVERY_SECONDS = 30
INFLIGHT_PREFIX = "hutuji:draw:inflight:"
JOB_PREFIX = "hutuji:draw:job:"
ENQUEUE_RATE_PREFIX = "hutuji:draw:enqueue:"
DEVICE_RATE_PREFIX = "hutuji:draw:device:"

# 去重、全部配额、任务登记和队列发布不可拆开；客户端取消或丢响应后仍能搭车。
_ENQUEUE_SCRIPT = """
local existing = redis.call('GET', KEYS[1])
if existing then
    local status = redis.call('HGET', ARGV[15] .. existing, 'status')
    if status == 'pending' or status == 'processing' or status == 'done' then
        return {'attached', existing}
    end
end
if redis.call('EXISTS', KEYS[2]) == 1 then return {'collision', ''} end
local depth = math.max(redis.call('LLEN', KEYS[3]), redis.call('ZCARD', KEYS[4]))
if depth >= tonumber(ARGV[9]) then return {'busy', ''} end
local function count(key)
    local value = redis.call('GET', key) or '0'
    if not string.match(value, '^%d+$') then error('invalid quota counter') end
    return tonumber(value)
end
local global_count = count(KEYS[5])
local minute_count, day_count = 0, 0
if ARGV[6] ~= '' then
    minute_count = count(KEYS[6])
    day_count = count(KEYS[7])
end
if global_count >= tonumber(ARGV[10]) or minute_count >= tonumber(ARGV[11]) then
    return {'busy', ''}
end
if day_count >= tonumber(ARGV[12]) then return {'daily', ''} end
redis.call('INCR', KEYS[5])
redis.call('EXPIRE', KEYS[5], 2)
if ARGV[6] ~= '' then
    redis.call('INCR', KEYS[6])
    redis.call('EXPIRE', KEYS[6], 120)
    redis.call('INCR', KEYS[7])
    redis.call('EXPIRE', KEYS[7], 172800)
end
redis.call('HSET', KEYS[2], 'status', 'pending', 'description', ARGV[4],
    'public_base_url', ARGV[5], 'device_id', ARGV[6], 'machine', ARGV[7],
    'payload', ARGV[2], 'payload_sha256', ARGV[3], 'inflight_key', KEYS[1])
redis.call('EXPIRE', KEYS[2], ARGV[13])
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[14])
redis.call('ZADD', KEYS[4], ARGV[8], ARGV[1])
redis.call('LPUSH', KEYS[3], ARGV[2])
return {'created', ARGV[1]}
"""

# BRPOP 与领取之间退出时，pending 台账仍持有原文。仅补尚未执行的记录，
# processing 不自动重放，避免把一次已开始的付费生成当作可安全重复的操作。
_RECOVER_PENDING_SCRIPT = """
redis.call('LLEN', KEYS[1])
local jobs = redis.call('ZRANGEBYSCORE', KEYS[2], '-inf', ARGV[2] - ARGV[3], 'LIMIT', 0, 64)
local recovered = 0
for i = #jobs, 1, -1 do
    local job_id = jobs[i]
    local key = ARGV[1] .. job_id
    local status = redis.call('HGET', key, 'status')
    local payload = redis.call('HGET', key, 'payload')
    if status == 'pending' and payload then
        redis.call('LREM', KEYS[1], 0, payload)
        redis.call('RPUSH', KEYS[1], payload)
        redis.call('ZADD', KEYS[2], ARGV[2], job_id)
        recovered = recovered + 1
    else
        redis.call('ZREM', KEYS[2], job_id)
    end
end
return recovered
"""

_CLAIM_SCRIPT = """
local status = redis.call('HGET', KEYS[1], 'status')
if redis.call('HGET', KEYS[1], 'payload_sha256') ~= ARGV[2] then return 0 end
if status == 'processing' and redis.call('HGET', KEYS[1], 'claim_token') == ARGV[3] then
    return 1
end
if status ~= 'pending' then return 0 end
local inflight = redis.call('HGET', KEYS[1], 'inflight_key')
local owns_inflight = inflight and redis.call('GET', inflight) == ARGV[1]
redis.call('ZCARD', KEYS[2])
redis.call('HSET', KEYS[1], 'status', 'processing', 'claim_token', ARGV[3])
redis.call('EXPIRE', KEYS[1], ARGV[4])
redis.call('ZREM', KEYS[2], ARGV[1])
if owns_inflight then
    redis.call('EXPIRE', inflight, ARGV[5])
end
return 1
"""

_FINISH_SCRIPT = """
if ARGV[1] == '' or redis.call('HGET', KEYS[1], 'claim_token') ~= ARGV[1] then return 0 end
local status = redis.call('HGET', KEYS[1], 'status')
if status == ARGV[2] and redis.call('HGET', KEYS[1], 'result') == ARGV[3]
    and redis.call('HGET', KEYS[1], 'error_type') == ARGV[4]
    and redis.call('HGET', KEYS[1], 'error_message') == ARGV[5] then return 1 end
if status ~= 'processing' then return 0 end
redis.call('HSET', KEYS[1], 'status', ARGV[2], 'result', ARGV[3],
    'error_type', ARGV[4], 'error_message', ARGV[5])
redis.call('HDEL', KEYS[1], 'payload')
redis.call('EXPIRE', KEYS[1], ARGV[6])
return 1
"""

# 比较与删除在 Redis 内一次完成，避免旧 worker 删掉并发重试已登记的新任务。
_COMPARE_DELETE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""
_REJECT_PAYLOAD_SCRIPT = """
if redis.call('HGET', KEYS[1], 'status') == 'pending'
    and redis.call('HGET', KEYS[1], 'payload_sha256') == ARGV[1] then
    redis.call('HSET', KEYS[1], 'status', 'failed', 'error_type', 'ValueError',
        'error_message', ARGV[2])
    redis.call('ZREM', KEYS[2], ARGV[3])
    return 1
end
return 0
"""


class DrawQueueBusyError(RuntimeError):
    """队列已满或全局入队速率超限。"""


class DrawDailyLimitError(RuntimeError):
    """单设备当日绘画次数达上限（UTC+8 自然日窗口）。"""


@dataclass(frozen=True)
class DrawJobPayload:
    job_id: str
    description: str
    public_base_url: str
    # chain_push=False 表示「完成时不链式推送演示设备」（draw-portal 网页生图：
    # 推送由买家在页面上显式触发，不该串到运营 demo 机的预览）。
    # 2026-08-31 新增字段；缺省 True 保持语音路径与旧 payload 行为不变。
    chain_push: bool = True
    # 显式机型（2026-09-11）：portal /internal/draw 传 "nopaper"/"paper"，
    # 语音路径 "" = worker 侧 status 探测。缺省空串兼容旧 payload。
    machine: str = ""
    paper: PaperConfig | None = None
    device_id: str = ""
    owner_stamp: str = ""
    payload_sha256: str = field(default="", compare=False, repr=False)

    @classmethod
    def from_dict(cls, data: object) -> DrawJobPayload:
        if not isinstance(data, dict):
            raise ValueError("任务记录必须是对象")
        fields = ("job_id", "description", "public_base_url", "machine", "device_id", "owner_stamp")
        for name in fields:
            value = data.get(name, "")
            if not isinstance(value, str) or len(value) > 2048:
                raise ValueError("任务字段类型或长度无效")
        if any(not data.get(name) for name in fields[:3]):
            raise ValueError("任务缺少必需字段")
        if not re.fullmatch(r"[A-Za-z0-9_-]{22}", data["job_id"]):
            raise ValueError("任务编号无效")
        chain_push = data.get("chain_push", True)
        machine = data.get("machine", "")
        if type(chain_push) is not bool or machine not in ("", "paper", "nopaper"):
            raise ValueError("任务推送或机型字段无效")
        paper = PaperConfig.from_dict(data["paper"]) if data.get("paper") is not None else None
        device_id, owner_stamp = data.get("device_id", ""), data.get("owner_stamp", "")
        if owner_stamp or (paper is not None and chain_push):
            from .device_paper import DrawTarget

            # 新语音任务必须携带完整归属；坏快照不能降级为旧演示机推送。
            DrawTarget.from_dict({"device_id": device_id, "owner_stamp": owner_stamp,
                                  "paper": data.get("paper"), "machine": machine})
        if paper is not None and not machine:
            raise ValueError("纸张任务缺少机型")
        return cls(data["job_id"], data["description"], data["public_base_url"], chain_push,
                   machine, paper, device_id, owner_stamp)


def _encode_payload(job: DrawJobPayload) -> str:
    return json.dumps({
        "job_id": job.job_id, "description": job.description, "public_base_url": job.public_base_url,
        "chain_push": job.chain_push, "machine": job.machine,
        "paper": job.paper.to_dict() if job.paper is not None else None,
        "device_id": job.device_id, "owner_stamp": job.owner_stamp,
    }, ensure_ascii=False)


def _description_hash(description: str) -> str:
    return hashlib.sha256(description.encode("utf-8")).hexdigest()


def _inflight_material(description: str, chain_push: bool, machine: str = "", *,
                       paper: PaperConfig | None = None, device_id: str = "", owner_stamp: str = "") -> str:
    """inflight 去重键的原料：chain_push/机型不同的同名任务不得互相搭车。

    语音（chain_push=True）搭上网页（False）的任务，完成时不会推设备预览，
    语音用户干等；反向则网页任务串推到演示设备。分开计键两边各自独立。
    机型同理（2026-09-11）：nopaper 搭上 MK38 档同题任务会拿到转置错向产物。
    machine 为空不追加（保持与旧键逐字节一致，语音路径键不漂移）。
    """
    material = description if chain_push else description + "\x00nopush"
    material = material if not machine else material + "\x00" + machine
    if paper is not None or device_id or owner_stamp:
        material += "\x00" + json.dumps([device_id, owner_stamp, paper.marker if paper else ""], separators=(",", ":"))
    return material


def day_stamp_cst() -> str:
    """UTC+8 自然日戳 yyyymmdd：日限窗口键，两后端与 server 进程内闸共用。

    固定 +8 而非本地时区：产品只面向国内课堂，部署机时区不该改变窗口边界，
    也免去 Windows 上 tzdata 依赖。
    """
    return time.strftime("%Y%m%d", time.gmtime(time.time() + 8 * 3600))


def queue_enabled() -> bool:
    return bool(getattr(settings, "draw_queue_url", "").strip())


def _job_key(job_id: str) -> str:
    return f"{JOB_PREFIX}{job_id}"


def _inflight_key(description: str, chain_push: bool = True, machine: str = "", *,
                  paper: PaperConfig | None = None, device_id: str = "", owner_stamp: str = "") -> str:
    return f"{INFLIGHT_PREFIX}{_description_hash(_inflight_material(description, chain_push, machine, paper=paper, device_id=device_id, owner_stamp=owner_stamp))}"

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
        self, description: str, public_base_url: str, *, device_id: str = "", chain_push: bool = True,
        machine: str = "", paper: PaperConfig | None = None, owner_stamp: str = "",
    ) -> str: ...

    async def wait_result(self, job_id: str, *, timeout: float) -> str: ...

    async def dequeue(self, *, timeout: float) -> DrawJobPayload | None: ...

    async def mark_processing(self, job_id: str, *, payload_sha256: str) -> str | None: ...

    async def mark_done(self, job_id: str, result: str, *, claim_token: str = "") -> bool: ...

    async def mark_failed(self, job_id: str, exc: BaseException, *, claim_token: str = "") -> bool: ...

    async def clear_inflight(
        self, description: str, job_id: str, *, chain_push: bool = True, machine: str = "",
        paper: PaperConfig | None = None, device_id: str = "", owner_stamp: str = ""
    ) -> None: ...


class MemoryDrawQueueBackend:
    """单进程测试后端；不支持跨进程 worker。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._queue: asyncio.Queue[DrawJobPayload | None] = asyncio.Queue()
        self._jobs: dict[str, dict[str, str]] = {}
        self._inflight: dict[str, str] = {}
        self._enqueue_times: list[float] = []
        self._device_minute: dict[str, int] = {}
        self._device_day: dict[str, int] = {}
    async def attach_or_enqueue(
        self, description: str, public_base_url: str, *, device_id: str = "", chain_push: bool = True,
        machine: str = "", paper: PaperConfig | None = None, owner_stamp: str = "",
    ) -> str:
        async with self._lock:
            inflight_key = _description_hash(_inflight_material(description, chain_push, machine, paper=paper, device_id=device_id, owner_stamp=owner_stamp))
            existing = self._inflight.get(inflight_key)
            if existing is not None:
                job = self._jobs.get(existing)
                # replace 与 create 都落到下方新建分支：inflight 赋值即覆盖残留
                if _decide_attach(existing, job.get("status") if job else None) == "attach":
                    return existing
            await self._check_enqueue_limits_locked(device_id)
            job_id = secrets.token_urlsafe(16)
            payload = DrawJobPayload(job_id, description, public_base_url, chain_push, machine, paper, device_id, owner_stamp)
            digest = hashlib.sha256(_encode_payload(payload).encode("utf-8")).hexdigest()
            payload = replace(payload, payload_sha256=digest)
            self._jobs[job_id] = {
                "status": "pending",
                "description": description,
                "public_base_url": public_base_url,
                "device_id": device_id,
                "machine": machine,
                "payload_sha256": digest,
            }
            self._inflight[inflight_key] = job_id
            self._queue.put_nowait(payload)
        return job_id

    async def _check_enqueue_limits_locked(self, device_id: str) -> None:
        max_depth = settings.draw_queue_max_depth
        if self._queue.qsize() >= max_depth:
            raise DrawQueueBusyError("绘图服务正忙，请稍候再试")
        now = time.monotonic()
        self._enqueue_times = [t for t in self._enqueue_times if now - t < 1.0]
        if len(self._enqueue_times) >= settings.draw_enqueue_per_second:
            raise DrawQueueBusyError("绘图服务正忙，请稍候再试")
        if device_id:
            minute = int(time.time() // 60)
            bucket = f"{device_id}:{minute}"
            count = self._device_minute.get(bucket, 0) + 1
            if count > settings.draw_per_device_minute:
                raise DrawQueueBusyError("绘图服务正忙，请稍候再试")
            # 日限同款「先查后计」：超限的请求不占额（与 portal 失败不占额同口径，
            # 但计数点取入队时刻——防滥用闸，非精确计费）
            day_bucket = f"{device_id}:{day_stamp_cst()}"
            day_count = self._device_day.get(day_bucket, 0) + 1
            if day_count > settings.draw_per_device_day:
                raise DrawDailyLimitError("今日绘画次数已达上限，明天再来")
            self._device_minute[bucket] = count
            self._device_day[day_bucket] = day_count
        self._enqueue_times.append(now)

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

    async def mark_processing(self, job_id: str, *, payload_sha256: str) -> str | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.get("status") != "pending" or job.get("payload_sha256") != payload_sha256:
                return None
            claim = secrets.token_urlsafe(16)
            job.update(status="processing", claim_token=claim)
            return claim

    async def mark_done(self, job_id: str, result: str, *, claim_token: str = "") -> bool:
        async with self._lock:
            job = self._jobs.get(job_id)
            if not claim_token or job is None or job.get("claim_token") != claim_token:
                return False
            if job.get("status") == "done" and job.get("result") == result:
                return True
            if job.get("status") != "processing":
                return False
            job.update(status="done", result=result)
            return True

    async def mark_failed(self, job_id: str, exc: BaseException, *, claim_token: str = "") -> bool:
        async with self._lock:
            job = self._jobs.get(job_id)
            if not claim_token or job is None or job.get("claim_token") != claim_token:
                return False
            if (job.get("status") == "failed" and job.get("error_type") == type(exc).__name__
                    and job.get("error_message") == str(exc)):
                return True
            if job.get("status") != "processing":
                return False
            job.update(status="failed", error_type=type(exc).__name__, error_message=str(exc))
            return True

    async def clear_inflight(
        self, description: str, job_id: str, *, chain_push: bool = True, machine: str = "",
        paper: PaperConfig | None = None, device_id: str = "", owner_stamp: str = ""
    ) -> None:
        async with self._lock:
            inflight_key = _description_hash(_inflight_material(description, chain_push, machine, paper=paper, device_id=device_id, owner_stamp=owner_stamp))
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
        self, description: str, public_base_url: str, *, device_id: str = "", chain_push: bool = True,
        machine: str = "", paper: PaperConfig | None = None, owner_stamp: str = "",
    ) -> str:
        client = self._client()
        inflight = _inflight_key(description, chain_push, machine, paper=paper, device_id=device_id, owner_stamp=owner_stamp)
        now_sec = int(time.time())
        rate_key = f"{ENQUEUE_RATE_PREFIX}{now_sec}"
        device_key = f"{DEVICE_RATE_PREFIX}{device_id}:{now_sec // 60}"
        day_key = f"{DEVICE_RATE_PREFIX}day:{device_id}:{day_stamp_cst()}"
        for _attempt in range(3):
            job_id = secrets.token_urlsafe(16)
            payload = _encode_payload(DrawJobPayload(job_id, description, public_base_url,
                chain_push, machine, paper, device_id, owner_stamp))
            outcome, resolved = await client.eval(
                _ENQUEUE_SCRIPT, 7, inflight, _job_key(job_id), QUEUE_KEY, PENDING_KEY,
                rate_key, device_key, day_key,
                job_id, payload, hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                description, public_base_url, device_id, machine, now_sec,
                settings.draw_queue_max_depth, settings.draw_enqueue_per_second,
                settings.draw_per_device_minute, settings.draw_per_device_day,
                JOB_TTL_SECONDS, INFLIGHT_TTL_SECONDS, JOB_PREFIX,
            )
            if outcome in ("created", "attached"):
                return resolved
            if outcome == "daily":
                raise DrawDailyLimitError("今日绘画次数已达上限，明天再来")
            if outcome != "collision":
                break
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
        await client.eval(_RECOVER_PENDING_SCRIPT, 2, QUEUE_KEY, PENDING_KEY,
                          JOB_PREFIX, int(time.time()), PENDING_RECOVERY_SECONDS)
        item = await client.brpop(QUEUE_KEY, timeout=max(1, int(timeout)))
        if item is None:
            return None
        _, raw = item
        data = None
        try:
            if len(raw.encode("utf-8")) > 32 * 1024:
                raise ValueError("任务记录超过预算")
            data = json.loads(raw)
            return replace(DrawJobPayload.from_dict(data),
                           payload_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest())
        except (ValueError, TypeError, RecursionError):
            # 只结清与入队原文 hash 完全相同的 pending 记录；坏 ID/被篡改的
            # payload 不能借这条清理路径改写另一件正常任务或已完成的结果。
            job_id = data.get("job_id") if isinstance(data, dict) else None
            if isinstance(job_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{22}", job_id):
                await client.eval(_REJECT_PAYLOAD_SCRIPT, 2, _job_key(job_id), PENDING_KEY,
                    hashlib.sha256(raw.encode("utf-8")).hexdigest(), "生成任务记录无效，请重试", job_id)
            logger.warning("draw-worker 跳过无效任务记录")
            return None

    async def mark_processing(self, job_id: str, *, payload_sha256: str) -> str | None:
        from redis.exceptions import ConnectionError, TimeoutError

        if not payload_sha256:
            return None
        client = self._client()
        claim = secrets.token_urlsafe(16)
        args = (_CLAIM_SCRIPT, 2, _job_key(job_id), PENDING_KEY, job_id, payload_sha256,
                claim, JOB_TTL_SECONDS, INFLIGHT_TTL_SECONDS)
        try:
            claimed = await client.eval(*args)
        except (ConnectionError, TimeoutError):
            # 同一领取凭证可重试一次；响应丢失不等于没有执行，不能换凭证再争抢。
            claimed = await client.eval(*args)
        return claim if claimed else None

    async def mark_done(self, job_id: str, result: str, *, claim_token: str = "") -> bool:
        return await self._finish(job_id, claim_token, "done", result, "", "")

    async def mark_failed(self, job_id: str, exc: BaseException, *, claim_token: str = "") -> bool:
        return await self._finish(job_id, claim_token, "failed", "", type(exc).__name__, str(exc))

    async def _finish(self, job_id: str, claim_token: str, status: str, result: str,
                      error_type: str, error_message: str) -> bool:
        from redis.exceptions import ConnectionError, TimeoutError

        client = self._client()
        args = (_FINISH_SCRIPT, 1, _job_key(job_id), claim_token, status, result,
                error_type, error_message, JOB_TTL_SECONDS)
        try:
            settled = await client.eval(*args)
        except (ConnectionError, TimeoutError):
            # 终态保留领取凭证至 TTL，只接受同一结果重试；丢响应不能触发重新生成。
            settled = await client.eval(*args)
        return bool(settled)

    async def clear_inflight(
        self, description: str, job_id: str, *, chain_push: bool = True, machine: str = "",
        paper: PaperConfig | None = None, device_id: str = "", owner_stamp: str = ""
    ) -> None:
        client = self._client()
        inflight = _inflight_key(description, chain_push, machine, paper=paper, device_id=device_id, owner_stamp=owner_stamp)
        await client.eval(_COMPARE_DELETE_SCRIPT, 1, inflight, job_id)


def _exception_from_job(job: dict[str, str]) -> BaseException:
    from .bitmap_svg import BitmapBusyError, BitmapError
    from .gcode_gen import GcodeError
    from .server import DrawLimitError, DrawTimeoutError, PaperRiskExhaustedError

    mapping: dict[str, type[BaseException]] = {
        "DrawLimitError": DrawLimitError,
        "DrawTimeoutError": DrawTimeoutError,
        "BitmapError": BitmapError,
        # 2026-09-12 忙批：worker 端 BitmapBusyError 须按原名重建，waiter 侧
        # /internal/draw 才能把全忙瞬态映射成 429「通道忙」而不是 502。
        "BitmapBusyError": BitmapBusyError,
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
    description: str, public_base_url: str, *, device_id: str = "", chain_push: bool = True,
    machine: str = "", paper: PaperConfig | None = None, owner_stamp: str = "",
) -> str:
    job_id = await get_backend().attach_or_enqueue(
        description, public_base_url, device_id=device_id, chain_push=chain_push,
        machine=machine, paper=paper, owner_stamp=owner_stamp,
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
