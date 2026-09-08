"""魔搭 ModelScope 客户端与对冲池接线契约（2026-09-08）。

实证契约（用户 token 对 api-inference.modelscope.cn 实测）：
- i2i 图字段名是 image_url（传 image 报 400 "image_url is required"），data URI 可用；
- 轮询必须带 X-ModelScope-Task-Type: image_generation；SUCCEED 后取 output_images[0]；
- 免费层排队可达分钟级 → 轮询上限独立于对冲池 45s 单次闸。
混元生图（腾讯云开发 generateImage 云函数原生 invoke）：{prompt,model} 进
{success,imageUrl} 出，实测不支持 i2i（image 参数被静默忽略），只进 t2i 对冲池。
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from hutuji_mcp import bitmap_svg, modelscope_client
from hutuji_mcp.config import Settings
from hutuji_mcp.modelscope_client import ModelScopeError

_MS_HOST = "api-inference.modelscope.cn"


def _png_stub() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"MS-IMAGE-BYTES"


def _submit_ok(task_id: str = "task-1") -> httpx.Response:
    return httpx.Response(200, json={"task_id": task_id})


def _poll_ok(url: str = "https://cdn.example/ms.png") -> httpx.Response:
    return httpx.Response(200, json={"task_status": "SUCCEED", "output_images": [url]})


def test_i2i_submission_uses_image_url_field() -> None:
    """i2i 提交必须走 image_url 字段（image 会被 400），且轮询头钉死。"""
    seen: dict = {}

    # 提交/轮询走魔搭主机，下载走 cdn.example——同一 MockTransport 全接管。
    def handler(request):  # noqa: ANN001, ANN202
        if request.url.host == _MS_HOST and request.method == "POST":
            seen["payload"] = json.loads(request.content)
            seen["auth"] = request.headers["authorization"]
            return _submit_ok()
        if request.url.host == _MS_HOST:
            seen["poll_header"] = request.headers.get("x-modelscope-task-type")
            return _poll_ok()
        return httpx.Response(200, content=_png_stub())

    async def main() -> bytes:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await modelscope_client.generate_i2i_png(
                client, "ms-token", b"\x89PNG-fake", timeout_seconds=10.0
            )

    body = asyncio.run(main())
    assert body == _png_stub()
    payload = seen["payload"]
    assert payload["model"] == modelscope_client.I2I_MODEL
    assert payload["image_url"].startswith("data:image/png;base64,")
    assert "image" not in payload
    assert payload["prompt"] == modelscope_client.I2I_LINE_ART_PROMPT
    assert seen["auth"] == "Bearer ms-token"
    assert seen["poll_header"] == "image_generation"


def test_t2i_returns_result_url_without_image_field() -> None:
    seen: dict = {}

    def handler(request):  # noqa: ANN001, ANN202
        if request.method == "POST":
            seen["payload"] = json.loads(request.content)
            return _submit_ok()
        return _poll_ok("https://cdn.example/t2i.png")

    async def main() -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await modelscope_client.generate_image_url(
                client, "ms-token", prompt="一只猫", timeout_seconds=10.0
            )

    assert asyncio.run(main()) == "https://cdn.example/t2i.png"
    assert seen["payload"] == {
        "model": modelscope_client.T2I_MODEL,
        "prompt": "一只猫",
    }


def test_poll_running_then_succeeds() -> None:
    calls = 0

    def handler(request):  # noqa: ANN001, ANN202
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, json={"task_status": "RUNNING"})
        return _poll_ok()

    async def main() -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await modelscope_client.poll_task(
                client, "ms-token", "task-1", timeout_seconds=10.0, interval_seconds=0.01
            )

    assert asyncio.run(main()) == "https://cdn.example/ms.png"
    assert calls == 2


def test_poll_failed_raises_without_echoing_upstream_text() -> None:
    def handler(request):  # noqa: ANN001, ANN202
        return httpx.Response(
            200, json={"task_status": "FAILED", "message": "content-policy-violation-detail"}
        )

    async def main() -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await modelscope_client.poll_task(
                client, "ms-token", "task-1", timeout_seconds=10.0, interval_seconds=0.01
            )

    with pytest.raises(ModelScopeError, match="魔搭任务失败") as excinfo:
        asyncio.run(main())
    assert "content-policy" not in str(excinfo.value)


def test_submit_http_error_carries_status_only() -> None:
    def handler(request):  # noqa: ANN001, ANN202
        return httpx.Response(400, json={"error": {"message": "image_url is required"}})

    async def main() -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await modelscope_client.submit_task(
                client, "ms-token", model="m", prompt="p"
            )

    with pytest.raises(ModelScopeError, match="魔搭提交返回 400") as excinfo:
        asyncio.run(main())
    assert "image_url is required" not in str(excinfo.value)


def test_poll_timeout_raises() -> None:
    def handler(request):  # noqa: ANN001, ANN202
        return httpx.Response(200, json={"task_status": "RUNNING"})

    async def main() -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await modelscope_client.poll_task(
                client, "ms-token", "task-1", timeout_seconds=0.0, interval_seconds=0.01
            )

    with pytest.raises(ModelScopeError, match="排队超时"):
        asyncio.run(main())


def test_poll_tolerates_transient_then_recovers() -> None:
    calls = 0

    def handler(request):  # noqa: ANN001, ANN202
        nonlocal calls
        calls += 1
        if calls <= 2:
            return httpx.Response(503)
        return _poll_ok()

    async def main() -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await modelscope_client.poll_task(
                client, "ms-token", "task-1", timeout_seconds=10.0, interval_seconds=0.01
            )

    assert asyncio.run(main()) == "https://cdn.example/ms.png"
    assert calls == 3


def test_succeed_without_output_images_raises() -> None:
    def handler(request):  # noqa: ANN001, ANN202
        return httpx.Response(200, json={"task_status": "SUCCEED"})

    async def main() -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await modelscope_client.poll_task(
                client, "ms-token", "task-1", timeout_seconds=10.0, interval_seconds=0.01
            )

    with pytest.raises(ModelScopeError, match="未返回结果图地址"):
        asyncio.run(main())


def test_download_rejects_non_https() -> None:
    async def main() -> bytes:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as client:
            return await modelscope_client.download_image(client, "http://cdn.example/x.png")

    with pytest.raises(ModelScopeError, match="有效的 https"):
        asyncio.run(main())


@pytest.mark.parametrize(
    "url, reason",
    [
        # 任务回包 URL 过与生图主链同一把 SSRF 闸（IMG-URL-SSRF-GAP）：
        # 闸在发起任何 HTTP 请求之前，MockTransport 若被命中即测试失败。
        ("https://127.0.0.1/x.png", "内网"),
        ("https://192.168.1.13/x.png", "内网"),
        ("https://[::1]/x.png", "内网"),
        ("https://localhost./x.png", "本地主机名"),
        ("https://cdn.example.internal/x.png", "本地主机名"),
    ],
)
def test_download_rejects_internal_result_url(url: str, reason: str) -> None:
    def handler(request):  # noqa: ANN001, ANN202
        raise AssertionError("内网地址不应发起 HTTP 请求")

    async def main() -> bytes:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await modelscope_client.download_image(client, url)

    with pytest.raises(ModelScopeError, match=reason):
        asyncio.run(main())


# ---------------------------------------------------------------- 对冲池接线


def _race_settings(**overrides) -> SimpleNamespace:
    base = {
        "llm_api_key": "agnes-key",
        "llm_base_url": "https://agnes.invalid/v1",
        "image_model": "agnes-image",
        "image_fallback_url": "",
        "modelscope_token": "",
        "tcb_image_url": "",
        "tcb_api_key": "",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_modelscope_racer_wins_and_submits_t2i_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """token 配置后魔搭进对冲池；快速任务完成时应赢过慢 Agnes。"""
    monkeypatch.setattr(bitmap_svg, "settings", _race_settings(modelscope_token="ms-token"))
    seen: dict = {}

    async def handler(request):  # noqa: ANN001, ANN202
        if request.url.host == "agnes.invalid":
            await asyncio.sleep(0.2)
            return httpx.Response(200, json={"data": [{"url": "https://cdn.example/a.png"}]})
        if request.url.host == _MS_HOST and request.method == "POST":
            seen["payload"] = json.loads(request.content)
            seen["auth"] = request.headers["authorization"]
            return _submit_ok()
        if request.url.host == _MS_HOST:
            seen["poll_header"] = request.headers.get("x-modelscope-task-type")
            return _poll_ok("https://cdn.example/ms.png")
        return httpx.Response(200, content=_png_stub())

    body = asyncio.run(
        bitmap_svg._fetch_image_png("城堡", transport=httpx.MockTransport(handler))
    )
    assert body == _png_stub()
    assert seen["payload"]["model"] == modelscope_client.T2I_MODEL
    assert seen["payload"]["prompt"] == bitmap_svg._BITMAP_STYLE + "城堡"
    assert "image_url" not in seen["payload"]
    assert seen["auth"] == "Bearer ms-token"
    assert seen["poll_header"] == "image_generation"


def test_modelscope_absent_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """无 token 时魔搭不进池（窄 settings 缺字段也按 getattr 口径缺席）。"""
    monkeypatch.setattr(
        bitmap_svg,
        "settings",
        SimpleNamespace(
            llm_api_key="agnes-key",
            llm_base_url="https://agnes.invalid/v1",
            image_model="agnes-image",
            image_fallback_url="",
        ),
    )
    hosts: list[str] = []

    def handler(request):  # noqa: ANN001, ANN202
        hosts.append(request.url.host)
        if request.url.host == "agnes.invalid":
            return httpx.Response(200, json={"data": [{"url": "https://cdn.example/a.png"}]})
        return httpx.Response(200, content=_png_stub())

    body = asyncio.run(
        bitmap_svg._fetch_image_png("城堡", transport=httpx.MockTransport(handler))
    )
    assert body == _png_stub()
    assert _MS_HOST not in hosts


def test_tcb_racer_wins_with_hunyuan_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        bitmap_svg,
        "settings",
        _race_settings(
            tcb_image_url="https://tcb.invalid/v1/functions/generateImage-x",
            tcb_api_key="tcb-key",
        ),
    )
    seen: dict = {}

    async def handler(request):  # noqa: ANN001, ANN202
        if request.url.host == "agnes.invalid":
            await asyncio.sleep(0.2)
            return httpx.Response(200, json={"data": [{"url": "https://cdn.example/a.png"}]})
        if request.url.host == "tcb.invalid":
            seen["payload"] = json.loads(request.content)
            seen["auth"] = request.headers["authorization"]
            return httpx.Response(
                200, json={"success": True, "imageUrl": "https://cdn.example/tcb.png"}
            )
        return httpx.Response(200, content=_png_stub())

    body = asyncio.run(
        bitmap_svg._fetch_image_png("城堡", transport=httpx.MockTransport(handler))
    )
    assert body == _png_stub()
    assert seen["payload"] == {
        "prompt": bitmap_svg._BITMAP_STYLE + "城堡",
        "model": "hunyuan-image",
    }
    assert seen["auth"] == "Bearer tcb-key"


def test_tcb_success_false_falls_back_and_names_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """success=false 是业务失败不是结构异常；全灭时错误文案点名混元。"""
    monkeypatch.setattr(
        bitmap_svg,
        "settings",
        _race_settings(
            tcb_image_url="https://tcb.invalid/v1/functions/generateImage-x",
            tcb_api_key="tcb-key",
        ),
    )
    monkeypatch.setattr(bitmap_svg, "_RETRY_DELAYS_SECONDS", ())

    def handler(request):  # noqa: ANN001, ANN202
        if request.url.host == "tcb.invalid":
            return httpx.Response(200, json={"success": False, "error": "quota exceeded"})
        if request.url.host == "agnes.invalid":
            return httpx.Response(500)
        return httpx.Response(200, content=_png_stub())

    with pytest.raises(bitmap_svg.BitmapError) as excinfo:
        asyncio.run(
            bitmap_svg._fetch_image_png("城堡", transport=httpx.MockTransport(handler))
        )
    text = str(excinfo.value)
    assert "混元生图同样失败" in text
    assert "success=false" in text
    assert "quota exceeded" not in text


def test_modelscope_failure_named_in_error_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        bitmap_svg, "settings", _race_settings(modelscope_token="ms-token")
    )
    monkeypatch.setattr(bitmap_svg, "_RETRY_DELAYS_SECONDS", ())

    def handler(request):  # noqa: ANN001, ANN202
        if request.url.host == _MS_HOST and request.method == "POST":
            return httpx.Response(400)
        if request.url.host == "agnes.invalid":
            return httpx.Response(500)
        return httpx.Response(200, content=_png_stub())

    with pytest.raises(bitmap_svg.BitmapError) as excinfo:
        asyncio.run(
            bitmap_svg._fetch_image_png("城堡", transport=httpx.MockTransport(handler))
        )
    assert "魔搭生图同样失败：魔搭提交返回 400" in str(excinfo.value)


# ---------------------------------------------------------------- 配置校验


def _settings_kwargs(tmp_path, **overrides):
    base = dict(
        llm_base_url="https://example.invalid/v1",
        llm_api_key="",
        llm_model="test",
        output_dir=tmp_path,
        public_base_url="https://draw.example",
        host="127.0.0.1",
        port=8300,
        output_ttl_seconds=600,
    )
    base.update(overrides)
    return base


def test_tcb_config_requires_pair(tmp_path) -> None:
    with pytest.raises(ValueError, match="HUTUJI_TCB_IMAGE_URL 与 HUTUJI_TCB_API_KEY"):
        Settings(**_settings_kwargs(tmp_path, tcb_image_url="https://tcb.invalid/f"))
    with pytest.raises(ValueError, match="HUTUJI_TCB_IMAGE_URL 与 HUTUJI_TCB_API_KEY"):
        Settings(**_settings_kwargs(tmp_path, tcb_api_key="k"))


def test_tcb_url_requires_https(tmp_path) -> None:
    with pytest.raises(ValueError, match="HUTUJI_TCB_IMAGE_URL 必须是 https"):
        Settings(
            **_settings_kwargs(
                tmp_path, tcb_image_url="http://tcb.invalid/f", tcb_api_key="k"
            )
        )


def test_tcb_and_modelscope_accepted_and_stripped(tmp_path) -> None:
    configured = Settings(
        **_settings_kwargs(
            tmp_path,
            modelscope_token="  ms-token  ",
            tcb_image_url="https://tcb.invalid/f/",
            tcb_api_key=" k ",
        )
    )
    assert configured.modelscope_token == "ms-token"
    assert configured.tcb_image_url == "https://tcb.invalid/f"
    assert configured.tcb_api_key == "k"
