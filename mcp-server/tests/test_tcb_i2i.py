"""TCB 混元 i2i 客户端契约（2026-09-08 用户指正混元有图生图 API 后接入）。

实证锚点：
- 网关鉴权（2026-09-08 实测）：无 key → 401 MISSING_CREDENTIALS，错 key → 401
  INVALID_CREDENTIALS，均由 tcloudbasegateway 在函数外拦掉；客户端只传 Bearer。
- 函数契约：{prompt, image(b64 无前缀), mode:"i2i"} → {success, imageUrl}；
  URL 24h 有效，立即下载。
- 网关 body 上限 → 输入先缩长边 1024 / 编码后 ≤3MiB（b64 ≈4.1MB）。
- 下载过 url_guard 唯一 SSRF 闸（复用 modelscope_client.download_image）。
"""
from __future__ import annotations

import asyncio
import base64
import io
import json

import httpx
import imageio.v3 as iio
import numpy as np
import pytest

from hutuji_mcp import modelscope_client, tcb_i2i
from hutuji_mcp.tcb_i2i import TcbI2iError

_FN_URL = "https://tcb.example/v1/functions/generateImage-x"


def _png(width: int = 64, height: int = 48) -> bytes:
    arr = (np.random.rand(height, width, 3) * 255).astype("uint8")
    buf = io.BytesIO()
    iio.imwrite(buf, arr, extension=".png")
    return buf.getvalue()


def _run(coro):
    return asyncio.run(coro)


def test_i2i_payload_and_download_contract() -> None:
    """正向：载荷字段/鉴权/默认提示词/下载链全对。"""
    src = _png()
    seen: dict = {}

    def handler(request):  # noqa: ANN001, ANN202
        if request.url.host == "tcb.example":
            seen["payload"] = json.loads(request.content)
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json={"success": True, "imageUrl": "https://cdn.example/r.png"})
        seen["download"] = str(request.url)
        return httpx.Response(200, content=b"\x89PNG-refined")

    async def main() -> bytes:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await tcb_i2i.generate_i2i_png(
                client, _FN_URL, "tcb-key", src, timeout_seconds=10.0
            )

    out = _run(main())
    payload = seen["payload"]
    assert payload["mode"] == "i2i"
    assert payload["prompt"] == modelscope_client.I2I_LINE_ART_PROMPT, "提示词必须与魔搭链同源"
    # 小图原样上传（不重复编码）：b64 解码回源字节。
    assert base64.b64decode(payload["image"]) == src
    assert seen["auth"] == "Bearer tcb-key"
    assert seen["download"] == "https://cdn.example/r.png"
    assert out == b"\x89PNG-refined"


def test_i2i_success_false_raises_without_download() -> None:
    calls: list[str] = []

    def handler(request):  # noqa: ANN001, ANN202
        calls.append(request.url.host)
        return httpx.Response(200, json={"success": False, "error": "content policy"})

    async def main() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await tcb_i2i.generate_i2i_png(client, _FN_URL, "k", _png(), timeout_seconds=10.0)

    with pytest.raises(TcbI2iError, match="未出图"):
        _run(main())
    assert calls == ["tcb.example"], "success=false 不得触发下载"


@pytest.mark.parametrize(
    ("status", "reason"),
    [(401, "返回 401"), (502, "返回 502")],
)
def test_i2i_http_error_raises(status: int, reason: str) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json={"code": "X"}))

    async def main() -> None:
        async with httpx.AsyncClient(transport=transport) as client:
            await tcb_i2i.generate_i2i_png(client, _FN_URL, "k", _png(), timeout_seconds=10.0)

    with pytest.raises(TcbI2iError, match=reason):
        _run(main())


def test_i2i_bad_json_raises() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"<html>Bad Gateway</html>")
    )

    async def main() -> None:
        async with httpx.AsyncClient(transport=transport) as client:
            await tcb_i2i.generate_i2i_png(client, _FN_URL, "k", _png(), timeout_seconds=10.0)

    with pytest.raises(TcbI2iError, match="不是合法 JSON"):
        _run(main())


def test_i2i_missing_image_url_raises() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"success": True})
    )

    async def main() -> None:
        async with httpx.AsyncClient(transport=transport) as client:
            await tcb_i2i.generate_i2i_png(client, _FN_URL, "k", _png(), timeout_seconds=10.0)

    with pytest.raises(TcbI2iError, match="有效的图片地址"):
        _run(main())


def test_i2i_non_https_result_url_blocked_by_guard() -> None:
    """函数回包给 http/私网地址时 url_guard 必须在下载前拦死（SSRF 硬约定）。"""
    calls: list[str] = []

    def handler(request):  # noqa: ANN001, ANN202
        calls.append(request.url.host)
        return httpx.Response(
            200, json={"success": True, "imageUrl": "http://169.254.169.254/meta.png"}
        )

    async def main() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await tcb_i2i.generate_i2i_png(client, _FN_URL, "k", _png(), timeout_seconds=10.0)

    with pytest.raises(TcbI2iError, match="有效的 https 图片地址"):
        _run(main())
    assert calls == ["tcb.example"], "闸后不得有任何下载请求"


def test_i2i_unconfigured_raises() -> None:
    async def main() -> None:
        async with httpx.AsyncClient() as client:
            await tcb_i2i.generate_i2i_png(client, "", "", _png(), timeout_seconds=10.0)

    with pytest.raises(TcbI2iError, match="未配置"):
        _run(main())


def test_shrink_downscales_large_photo() -> None:
    big = _png(2000, 1000)
    out = tcb_i2i._shrink_for_upload(big)
    arr = iio.imread(out)
    assert max(arr.shape[:2]) == 1024
    assert len(out) <= tcb_i2i._UPLOAD_MAX_BYTES


def test_shrink_rejects_undecodable() -> None:
    with pytest.raises(TcbI2iError, match="可解码"):
        tcb_i2i._shrink_for_upload(b"\x89PNG-not-really")


def test_shrink_rejects_oversize_input() -> None:
    with pytest.raises(TcbI2iError, match="32MiB"):
        tcb_i2i._shrink_for_upload(b"\x00" * (tcb_i2i._INPUT_MAX_BYTES + 1))
