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
