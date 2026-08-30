"""draw_queue + draw-worker 契约测试（memory:// 后端）。"""
from __future__ import annotations

import json
import subprocess
import sys
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
    calls: list[str] = []

    async def fake_run(description: str, public_base_url: str, *, job_id: str | None = None) -> str:
        calls.append(description)
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
        assert calls == ["一只猫"]

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


class _FakePipeline:
    """attach_or_enqueue 所需的最小 pipeline 面（hset/expire/set + execute）。"""

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
                if nx and (self._client.fail_next_nx or key in self._client._strings):
                    self._client.fail_next_nx = False
                    results.append(None)
                else:
                    self._client._strings[key] = value
                    results.append(True)
        return results


class _FakeRedis:
    """复现 TTL 残留窗口的最小 Redis 桩（真实 redis 包不可用时也能测决策环）。"""

    def __init__(self) -> None:
        self._strings: dict[str, str] = {}
        self._hashes: dict[str, dict[str, str]] = {}
        self._lists: dict[str, list[str]] = {}
        self.fail_next_nx = False

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
        return 1

    async def delete(self, key):
        self._strings.pop(key, None)

    async def lpush(self, key, value):
        self._lists.setdefault(key, []).insert(0, value)

    def pipeline(self):
        return _FakePipeline(self)


def _redis_backend(fake: _FakeRedis, monkeypatch: pytest.MonkeyPatch):
    backend = draw_queue.RedisDrawQueueBackend("redis://stub")
    monkeypatch.setattr(backend, "_client", lambda: fake)
    return backend


@pytest.mark.parametrize("stale_status", [None, "failed"])
def test_redis_attach_replaces_dead_inflight(memory_queue, monkeypatch, stale_status):
    """inflight 指向已消失/已失败任务时：删残留、换新 job_id、重新入队。"""
    fake = _FakeRedis()
    dead_id = "dead-job"
    inflight = draw_queue._inflight_key("一只猫")
    fake._strings[inflight] = dead_id
    if stale_status is not None:
        fake._hashes[draw_queue._job_key(dead_id)] = {"status": stale_status}
    backend = _redis_backend(fake, monkeypatch)

    async def run_case() -> None:
        job_id = await backend.attach_or_enqueue("一只猫", "https://hutuji.example")
        assert job_id != dead_id
        assert fake._strings[inflight] == job_id
        assert fake._hashes[draw_queue._job_key(job_id)]["status"] == "pending"
        assert len(fake._lists[draw_queue.QUEUE_KEY]) == 1

    asyncio_run(run_case())


def test_redis_attach_reuses_live_inflight(memory_queue, monkeypatch):
    fake = _FakeRedis()
    live_id = "live-job"
    inflight = draw_queue._inflight_key("一只猫")
    fake._strings[inflight] = live_id
    fake._hashes[draw_queue._job_key(live_id)] = {"status": "processing"}
    backend = _redis_backend(fake, monkeypatch)

    async def run_case() -> None:
        assert await backend.attach_or_enqueue("一只猫", "https://hutuji.example") == live_id
        assert draw_queue.QUEUE_KEY not in fake._lists  # 搭车不重复入队

    asyncio_run(run_case())


@pytest.mark.parametrize(
    ("winner_status", "expect_winner"),
    [
        ("pending", True),  # 并发 winner 存活 → 搭车
        (None, False),  # winner 也是死任务 → 删残留后第二轮新建成功
    ],
)
def test_redis_attach_nx_conflict_rejudges(memory_queue, monkeypatch, winner_status, expect_winner):
    fake = _FakeRedis()
    winner_id = "winner-job"
    backend = _redis_backend(fake, monkeypatch)

    async def run_case() -> None:
        fake.fail_next_nx = True

        original_set = _FakePipeline.set

        def set_and_plant(self, key, value, nx=False, ex=None):
            if nx and fake.fail_next_nx:
                fake._strings[key] = winner_id
                if winner_status is not None:
                    fake._hashes[draw_queue._job_key(winner_id)] = {"status": winner_status}
            return original_set(self, key, value, nx=nx, ex=ex)

        monkeypatch.setattr(_FakePipeline, "set", set_and_plant)
        job_id = await backend.attach_or_enqueue("一只猫", "https://hutuji.example")
        if expect_winner:
            assert job_id == winner_id
        else:
            assert job_id not in (winner_id, "")
            assert fake._strings[draw_queue._inflight_key("一只猫")] == job_id

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
