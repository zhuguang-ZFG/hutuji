"""单设备每日限流（HUTUJI_DRAW_PER_DEVICE_DAY，UTC+8 自然日窗口，入队时刻计数）。"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from hutuji_mcp import config, draw_queue, server
from hutuji_mcp.config import Settings, load_settings


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


def _queue_settings(monkeypatch: pytest.MonkeyPatch, **overrides) -> Settings:
    """补丁 settings 并复位后端全局；默认放宽其余闸，只留日限起作用。"""
    base = load_settings()
    patched = Settings(
        **{
            **base.__dict__,
            "draw_queue_max_depth": 100,
            "draw_enqueue_per_second": 100,
            "draw_per_device_minute": 60,
            **overrides,
        }
    )
    monkeypatch.setattr("hutuji_mcp.config.settings", patched)
    monkeypatch.setattr("hutuji_mcp.draw_queue.settings", patched)
    draw_queue.reset_backend_for_tests()
    return patched


class _FakePipeline:
    """attach_or_enqueue 所需的最小 pipeline 面（同 test_draw_queue 的桩）。"""

    def __init__(self, client: "_FakeRedis") -> None:
        self._client = client
        self._ops: list[tuple] = []

    def hset(self, key, mapping):
        self._ops.append(("hset", key, mapping))

    def expire(self, key, ttl):
        self._ops.append(("expire", key, ttl))

    def set(self, key, value, nx=False, ex=None):
        self._ops.append(("set", key, value, nx))

    async def execute(self):
        results = []
        for op in self._ops:
            if op[0] == "hset":
                self._client._hashes.setdefault(op[1], {}).update(op[2])
                results.append(1)
            elif op[0] == "expire":
                results.append(1)
            else:
                _, key, value, nx = op
                if nx and key in self._client._strings:
                    results.append(None)
                else:
                    self._client._strings[key] = value
                    results.append(True)
        return results


class _FakeRedis:
    """attach_or_enqueue 限流环的最小 Redis 桩（照 test_draw_queue 的做法）。"""

    def __init__(self) -> None:
        self._strings: dict[str, str] = {}
        self._hashes: dict[str, dict[str, str]] = {}
        self._lists: dict[str, list[str]] = {}
        self.expire_calls: list[tuple[str, int]] = []

    async def get(self, key):
        return self._strings.get(key)

    async def hget(self, key, field):
        return self._hashes.get(key, {}).get(field)

    async def llen(self, key):
        return len(self._lists.get(key, []))

    async def incr(self, key):
        value = int(self._strings.get(key, "0")) + 1
        self._strings[key] = str(value)
        return value

    async def expire(self, key, ttl):
        self.expire_calls.append((key, ttl))
        return 1

    async def delete(self, key):
        self._strings.pop(key, None)

    async def lpush(self, key, value):
        self._lists.setdefault(key, []).insert(0, value)

    def pipeline(self):
        return _FakePipeline(self)


def _inprocess_harness(tmp_path, monkeypatch: pytest.MonkeyPatch, *, day: int) -> None:
    """hutuji_draw 进程内路径最小桩：假生成链 + 关队列 + 清模块级日限计数。"""
    monkeypatch.setattr(
        server,
        "settings",
        SimpleNamespace(
            output_dir=tmp_path,
            public_base_url="https://draw.example",
            output_ttl_seconds=600,
            paper_risk_gate=False,
            draw_per_device_day=day,
        ),
    )
    # hutuji_draw 的 queue_enabled() 读 draw_queue.settings；置空即走进程内路径
    monkeypatch.setattr(
        "hutuji_mcp.draw_queue.settings", SimpleNamespace(draw_queue_url="")
    )

    async def fake_run(
        description: str,
        public_base_url: str,
        *,
        job_id: str | None = None,
        chain_push: bool = True,
    ) -> str:
        return json.dumps({"url": f"{public_base_url}/files/x.gcode"}, ensure_ascii=False)

    monkeypatch.setattr(server, "_run_draw_job", fake_run)
    server._draw_inflight.clear()
    server._device_day_counts.clear()


def test_memory_backend_rejects_21st_draw(monkeypatch: pytest.MonkeyPatch) -> None:
    _queue_settings(monkeypatch, draw_per_device_day=20)
    backend = draw_queue.MemoryDrawQueueBackend()

    async def run_case() -> None:
        for i in range(20):
            await backend.attach_or_enqueue(
                f"主题{i}", "https://hutuji.example", device_id="dev-mem"
            )
        with pytest.raises(
            draw_queue.DrawDailyLimitError, match="今日绘画次数已达上限，明天再来"
        ):
            await backend.attach_or_enqueue(
                "主题20", "https://hutuji.example", device_id="dev-mem"
            )

    asyncio_run(run_case())


def test_redis_backend_rejects_21st_draw(monkeypatch: pytest.MonkeyPatch) -> None:
    _queue_settings(monkeypatch, draw_per_device_day=20)
    fake = _FakeRedis()
    backend = draw_queue.RedisDrawQueueBackend("redis://stub")
    monkeypatch.setattr(backend, "_client", lambda: fake)

    async def run_case() -> None:
        for i in range(20):
            await backend.attach_or_enqueue(
                f"主题{i}", "https://hutuji.example", device_id="dev-redis"
            )
        with pytest.raises(
            draw_queue.DrawDailyLimitError, match="今日绘画次数已达上限，明天再来"
        ):
            await backend.attach_or_enqueue(
                "主题20", "https://hutuji.example", device_id="dev-redis"
            )

    asyncio_run(run_case())

    # 日键形如 hutuji:draw:device:day:<id>:<yyyymmdd>，首次 incr 后给 48h TTL
    day_key = f"{draw_queue.DEVICE_RATE_PREFIX}day:dev-redis:{draw_queue.day_stamp_cst()}"
    assert fake._strings[day_key] == "21"  # Redis 侧 incr 后比较，被拒请求也占一格
    assert (day_key, 48 * 3600) in fake.expire_calls


def test_inprocess_draw_rejects_21st_call(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _inprocess_harness(tmp_path, monkeypatch, day=20)

    async def run_case() -> None:
        for i in range(20):
            await server.hutuji_draw(f"主题{i}", device_id="dev-inproc")
        with pytest.raises(
            server.DrawLimitError, match="今日绘画次数已达上限，明天再来"
        ):
            await server.hutuji_draw("主题20", device_id="dev-inproc")

    asyncio_run(run_case())


def test_memory_backend_daily_limit_skips_empty_device(monkeypatch: pytest.MonkeyPatch) -> None:
    _queue_settings(monkeypatch, draw_per_device_day=5)
    backend = draw_queue.MemoryDrawQueueBackend()

    async def run_case() -> None:
        for i in range(12):
            await backend.attach_or_enqueue(f"无设备{i}", "https://hutuji.example")

    asyncio_run(run_case())


def test_inprocess_draw_skips_daily_limit_without_device(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _inprocess_harness(tmp_path, monkeypatch, day=1)

    async def run_case() -> None:
        for i in range(3):
            await server.hutuji_draw(f"无设备{i}")

    asyncio_run(run_case())


def test_memory_backend_daily_limit_resets_on_day_rollover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _queue_settings(monkeypatch, draw_per_device_day=2)
    backend = draw_queue.MemoryDrawQueueBackend()
    monkeypatch.setattr(draw_queue, "day_stamp_cst", lambda: "20260910")

    async def day_one() -> None:
        await backend.attach_or_enqueue("甲", "https://hutuji.example", device_id="dev-roll")
        await backend.attach_or_enqueue("乙", "https://hutuji.example", device_id="dev-roll")
        with pytest.raises(draw_queue.DrawDailyLimitError):
            await backend.attach_or_enqueue("丙", "https://hutuji.example", device_id="dev-roll")

    asyncio_run(day_one())

    monkeypatch.setattr(draw_queue, "day_stamp_cst", lambda: "20260911")

    async def day_two() -> None:
        job_id = await backend.attach_or_enqueue(
            "丁", "https://hutuji.example", device_id="dev-roll"
        )
        assert job_id

    asyncio_run(day_two())


def test_inprocess_daily_limit_resets_on_day_rollover(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _inprocess_harness(tmp_path, monkeypatch, day=2)
    monkeypatch.setattr(draw_queue, "day_stamp_cst", lambda: "20260910")

    async def day_one() -> None:
        await server.hutuji_draw("甲", device_id="dev-roll2")
        await server.hutuji_draw("乙", device_id="dev-roll2")
        with pytest.raises(server.DrawLimitError, match="今日绘画次数已达上限"):
            await server.hutuji_draw("丙", device_id="dev-roll2")

    asyncio_run(day_one())

    monkeypatch.setattr(draw_queue, "day_stamp_cst", lambda: "20260911")

    async def day_two() -> None:
        result = json.loads(await server.hutuji_draw("丁", device_id="dev-roll2"))
        assert result["url"].endswith("x.gcode")

    asyncio_run(day_two())


def test_queue_path_daily_limit_surfaces_draw_limit_error(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """queue 路径 DrawDailyLimitError 必须转成 DrawLimitError（/internal/draw 映 429）。"""
    monkeypatch.setattr(
        server,
        "settings",
        SimpleNamespace(
            output_dir=tmp_path,
            public_base_url="https://draw.example",
            output_ttl_seconds=600,
            paper_risk_gate=False,
            draw_per_device_day=20,
        ),
    )
    monkeypatch.setattr(
        "hutuji_mcp.draw_queue.settings", SimpleNamespace(draw_queue_url="redis://stub")
    )

    async def fake_enqueue(
        description: str,
        public_base_url: str,
        *,
        device_id: str = "",
        chain_push: bool = True,
    ) -> str:
        raise draw_queue.DrawDailyLimitError("今日绘画次数已达上限，明天再来")

    monkeypatch.setattr(draw_queue, "attach_or_enqueue", fake_enqueue)
    server._device_day_counts.clear()

    with pytest.raises(server.DrawLimitError, match="今日绘画次数已达上限，明天再来"):
        asyncio_run(server.hutuji_draw("主题", device_id="dev-q"))


def test_draw_per_device_day_env_default_and_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUTUJI_HOST", "127.0.0.1")
    monkeypatch.setenv("HUTUJI_PUBLIC_BASE_URL", "http://127.0.0.1:8300")
    monkeypatch.delenv("HUTUJI_DRAW_PER_DEVICE_DAY", raising=False)
    assert config.load_settings().draw_per_device_day == 20

    monkeypatch.setenv("HUTUJI_DRAW_PER_DEVICE_DAY", "7")
    assert config.load_settings().draw_per_device_day == 7

    monkeypatch.setenv("HUTUJI_DRAW_PER_DEVICE_DAY", "0")
    with pytest.raises(ValueError, match="draw_per_device_day"):
        config.load_settings()

    monkeypatch.setenv("HUTUJI_DRAW_PER_DEVICE_DAY", "501")
    with pytest.raises(ValueError, match="draw_per_device_day"):
        config.load_settings()
