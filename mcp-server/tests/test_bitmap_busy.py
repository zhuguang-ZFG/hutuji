"""位图链路「瞬态全忙」契约（2026-09-12 诊断批）。

生产事故口径：郭富城/日本等描述连败时商汤+魔搭同轮 429 而 Agnes 单飞可成；
全对冲通道同一轮都撞 429/5xx/超时才判「忙」——忙类整链做一次 2.5s 后真重跑，
并把 /internal/draw 的失败从 502「服务不可用」改映射 429「通道忙」白话。
内容审核/参数类 400 属永久失败，不判忙、不重试、不映射。
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from hutuji_mcp import bitmap_svg, draw_queue


def _settings(**over):
    base = {"llm_api_key": "", "image_model": "m", "draw_racer_max": 0}
    base.update(over)
    return SimpleNamespace(**base)


def _stub_trace_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """把描摹/预览五件套换成最小桩：只关心生图轮次的忙语义，不跑真栅格。"""
    monkeypatch.setattr(
        bitmap_svg, "trace_bitmap_to_polylines", lambda png: ([object()], 2, 2)
    )
    monkeypatch.setattr(
        bitmap_svg, "polylines_to_svg", lambda polys, w, h: "<svg></svg>"
    )
    monkeypatch.setattr(bitmap_svg, "_validate_svg", lambda svg: None)
    monkeypatch.setattr(
        bitmap_svg, "polylines_to_preview_png", lambda polys, w, h: b"png"
    )


def test_busy_retry_delay_constant_pinned():
    """2.5s 是「吃掉 429 令牌窗」与「少占 DrawLimiter 名额」的折中，不许顺手调。"""
    assert bitmap_svg._BUSY_RETRY_DELAY_SECONDS == 2.5
    assert bitmap_svg._BUSY_ERROR_CLASSES == frozenset({"http_429", "http_5xx", "timeout"})


def test_all_errors_busy_classifier():
    busy = lambda text: BitmapStub(text)  # noqa: E731
    assert bitmap_svg._all_errors_busy(
        {"商汤生图": BitmapStub("商汤生图 API 返回 429"), "魔搭生图": BitmapStub("魔搭提交返回 429")}
    )
    assert bitmap_svg._all_errors_busy(
        {"商汤生图": BitmapStub("上游 503"), "Agnes": BitmapStub("请求超时 timeout")}
    )
    # 混入 400（内容审核类）或未知 error：不判忙
    assert not bitmap_svg._all_errors_busy(
        {"商汤生图": BitmapStub("返回 429"), "Agnes": BitmapStub("生图 API 返回 400")}
    )
    assert not bitmap_svg._all_errors_busy(
        {"Agnes": BitmapStub("位图解码失败")}
    )
    assert not bitmap_svg._all_errors_busy({})


class BitmapStub(bitmap_svg.BitmapError):
    pass


class TestRaceAggregateBusy:
    def _race(self, monkeypatch, outcomes):
        """动态 racer 对冲桩：outcomes 依序作为每个 racer 的结果/异常。"""
        import hutuji_mcp.image_racers as image_racers

        calls = {"n": 0}

        async def fake_racer(client, description, racer):
            idx = calls["n"]
            calls["n"] += 1
            outcome = outcomes[idx]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        monkeypatch.setattr(
            image_racers, "request_racer_image_url", fake_racer
        )
        monkeypatch.setattr(bitmap_svg, "settings", _settings())
        monkeypatch.setattr(
            "hutuji_mcp.breaker.should_skip_backend", lambda bid: False
        )
        monkeypatch.setattr(
            "hutuji_mcp.breaker.record_outcome", lambda *a, **k: None
        )
        racers = [
            {"id": "r1", "name": "通道甲", "url": "https://r1.example.com/x"},
            {"id": "r2", "name": "通道乙", "url": "https://r2.example.com/x"},
        ]
        return asyncio.run(
            bitmap_svg._race_dynamic_image_racers(object(), "一只猫", racers)
        )

    def test_all_429_raises_bitmap_busy(self, monkeypatch):
        with pytest.raises(bitmap_svg.BitmapBusyError) as ei:
            self._race(
                monkeypatch,
                [
                    BitmapStub("商汤生图 API 返回 429"),
                    BitmapStub("魔搭提交返回 429"),
                ],
            )
        assert "通道甲" in str(ei.value) and "通道乙" in str(ei.value)
        assert isinstance(ei.value, bitmap_svg.BitmapError)

    def test_mixed_400_not_busy(self, monkeypatch):
        with pytest.raises(bitmap_svg.BitmapError) as ei:
            self._race(
                monkeypatch,
                [
                    BitmapStub("生图 API 返回 400"),
                    BitmapStub("商汤生图 API 返回 429"),
                ],
            )
        assert not isinstance(ei.value, bitmap_svg.BitmapBusyError)
        url = self._race(
            monkeypatch,
            ["https://img.example.com/win.png", BitmapStub("商汤生图 API 返回 429")],
        )
        assert url == "https://img.example.com/win.png"


class TestUncachedBusyRetry:
    def _make(self, monkeypatch, outcomes):
        """_generate_bitmap_uncached 的重试环：outcomes 依序消耗。"""
        _stub_trace_pipeline(monkeypatch)
        monkeypatch.setattr(bitmap_svg, "settings", _settings())
        monkeypatch.setattr(bitmap_svg, "_BUSY_RETRY_DELAY_SECONDS", 0.0)
        sleeps: list[float] = []
        real_sleep = asyncio.sleep

        async def fake_sleep(delay, *a, **k):
            sleeps.append(delay)
            await real_sleep(0)

        monkeypatch.setattr(bitmap_svg.asyncio, "sleep", fake_sleep)
        state = {"n": 0}

        async def fake_fetch(description, *, transport=None):
            idx = state["n"]
            state["n"] += 1
            outcome = outcomes[idx]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        monkeypatch.setattr(bitmap_svg, "_fetch_image_png", fake_fetch)

        def run():
            return asyncio.run(
                bitmap_svg._generate_bitmap_uncached("一只猫", "key")
            )

        return run, state, sleeps

    def test_busy_then_success_retries_once(self, monkeypatch):
        run, state, sleeps = self._make(
            monkeypatch,
            [bitmap_svg.BitmapBusyError("商汤生图 API 返回 429"), b"png"],
        )
        result = run()
        assert state["n"] == 2
        assert sleeps == [0.0]
        assert result.svg == "<svg></svg>"

    def test_busy_exhausted_raises_busy(self, monkeypatch):
        run, _, _ = self._make(
            monkeypatch,
            [
                bitmap_svg.BitmapBusyError("商汤生图 API 返回 429"),
                bitmap_svg.BitmapBusyError("商汤生图 API 返回 429"),
            ],
        )
        with pytest.raises(bitmap_svg.BitmapBusyError):
            run()

    def test_plain_error_never_retries(self, monkeypatch):
        run, state, sleeps = self._make(
            monkeypatch, [BitmapStub("生图 API 返回 400")]
        )
        with pytest.raises(BitmapStub):
            run()
        assert state["n"] == 1
        assert sleeps == []


class TestInternalDrawRouteMapping:
    def _call(self, monkeypatch, exc):
        from fastapi import HTTPException

        from hutuji_mcp import server

        async def fake_sync(description: str, machine: str = "") -> str:
            raise exc

        monkeypatch.setattr(server, "_draw_internal_sync", fake_sync)
        monkeypatch.setattr(
            server, "settings", SimpleNamespace(usage_ingest_secret="s3cr3t")
        )
        body = json.dumps({"description": "一只猫"}).encode()

        async def _body() -> bytes:
            return body

        request = SimpleNamespace(
            client=SimpleNamespace(host="127.0.0.1"),
            headers={"x-hutuji-usage-secret": "s3cr3t"},
            body=_body,
        )
        with pytest.raises(HTTPException) as ei:
            asyncio.run(server._internal_draw(request))
        return ei.value

    def test_busy_maps_429_with_fixed_plain_words(self, monkeypatch):
        http_exc = self._call(
            monkeypatch, bitmap_svg.BitmapBusyError("商汤生图 API 返回 429；魔搭提交返回 429")
        )
        assert http_exc.status_code == 429
        assert http_exc.detail == "生图通道都在忙，请稍等几秒再试"

    def test_plain_bitmap_error_stays_502(self, monkeypatch):
        http_exc = self._call(monkeypatch, BitmapStub("生图 API 返回 400"))
        assert http_exc.status_code == 502
        assert "400" in http_exc.detail


def test_queue_rebuild_maps_busy_error():
    exc = draw_queue._exception_from_job(
        {"error_type": "BitmapBusyError", "error_message": "商汤生图 API 返回 429"}
    )
    assert isinstance(exc, bitmap_svg.BitmapBusyError)
    assert isinstance(exc, bitmap_svg.BitmapError)
