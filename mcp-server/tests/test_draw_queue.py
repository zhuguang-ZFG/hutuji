"""draw_queue + draw-worker 契约测试（memory:// 后端）。"""
from __future__ import annotations

import json
import os
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
    calls: list[tuple[str, bool]] = []

    async def fake_run(
        description: str,
        public_base_url: str,
        *,
        job_id: str | None = None,
        chain_push: bool = True,
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

    async def brpop(self, key, timeout=0):
        items = self._lists.get(key) or []
        if not items:
            return None
        return (key, items.pop())

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


def test_redis_dequeue_chain_push_defaults_true_for_old_payload(memory_queue, monkeypatch):
    """旧 payload（2026-08-31 前入队、无 chain_push 字段）反序列化必须回 True，
    保持语音推送行为；显式 False 原样保留。"""
    fake = _FakeRedis()
    backend = _redis_backend(fake, monkeypatch)

    async def run_case() -> None:
        # brpop 从表尾取（最旧），故旧 payload 放末尾
        fake._lists[draw_queue.QUEUE_KEY] = [
            json.dumps({
                "job_id": "new",
                "description": "d",
                "public_base_url": "u",
                "chain_push": False,
            }),
            json.dumps({"job_id": "old", "description": "d", "public_base_url": "u"}),
        ]
        legacy = await backend.dequeue(timeout=0)
        modern = await backend.dequeue(timeout=0)
        assert legacy is not None and legacy.chain_push is True
        assert modern is not None and modern.chain_push is False

    asyncio_run(run_case())


def test_redis_attach_records_chain_push_and_separates_inflight(memory_queue, monkeypatch):
    fake = _FakeRedis()
    backend = _redis_backend(fake, monkeypatch)

    async def run_case() -> None:
        voice = await backend.attach_or_enqueue("一只猫", "https://hutuji.example")
        web = await backend.attach_or_enqueue(
            "一只猫", "https://hutuji.example", chain_push=False
        )
        assert voice != web
        assert fake._strings[draw_queue._inflight_key("一只猫", True)] == voice
        queued = {
            json.loads(item)["job_id"]: json.loads(item)
            for item in fake._lists[draw_queue.QUEUE_KEY]
        }
        assert queued[voice]["chain_push"] is True
        assert queued[web]["chain_push"] is False
        assert fake._strings[draw_queue._inflight_key("一只猫", False)] == web
        # 清网页键不得误清语音键
        await backend.clear_inflight("一只猫", web, chain_push=False)
        assert draw_queue._inflight_key("一只猫", False) not in fake._strings
        assert fake._strings[draw_queue._inflight_key("一只猫", True)] == voice

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
    proc = subprocess.run(
        [str(py), "-m", "hutuji_mcp.draw_worker"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env=env,
    )
    combined = proc.stdout + proc.stderr
    assert "Traceback" not in combined, combined
    assert "未配置" in combined, combined
