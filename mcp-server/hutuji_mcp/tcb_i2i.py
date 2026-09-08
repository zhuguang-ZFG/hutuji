"""腾讯云开发（TCB）混元 i2i 客户端：portal 照片精修链的对冲通道。

2026-09-08 用户指正「混元应该是有图生图 API 的」后落地：官方 `ImageToImage`
action + CloudBase AI SDK `generateImage(model=HY-Image-v3.0-I2I-ToB-*)` 均支持
底图，此前「TCB 不支持 i2i」的判断只看到了函数模板只接 prompt 的表象。

链路：portal → 云函数 `generateImage-cqA3Hl`（HTTP 触发）→ 函数内 i2i 分支调
`@cloudbase/node-sdk` AI 图像模型（环境内免 CAM 密钥）→ 返回 24h 有效 URL →
本模块立即下载（与魔搭同一把 url_guard SSRF 闸，URL 是函数回包、不可信）。

函数契约（仓外源码，粘贴进 TCB 控制台的 index.js 是配套交付物）：
- 请求 `{prompt, image: <base64 无前缀>, mode: "i2i"}`；无 image 字段走原 t2i 分支；
- 应答 `{success: true, imageUrl}` / `{success: false, error}`；
- 鉴权在 tcloudbasegateway 网关层（2026-09-08 实测无 key/错 key 均 401，
  MISSING_CREDENTIALS / INVALID_CREDENTIALS），函数内不校验、不加第二把锁；
- 函数侧钉死 `revise:{value:false}`（提示词改写多 ~30s，且与魔搭链不改写口径
  一致）与 size 1024x1024。

请求体上限：网关对函数触发有 body 上限，手机照片原尺寸 PNG 的 base64 可超
20MB——入图先缩到长边 1024 / 编码后 ≤3MiB（b64 ≈4.1MB）。缩图只影响 i2i
输入，不影响 Canny 主链与魔搭通道（魔搭 data-URI 收全尺寸）。
"""
from __future__ import annotations

import asyncio
import base64
import io
import json

import httpx

from . import modelscope_client
from .modelscope_client import ModelScopeError

# 函数超时配置 120s；i2i 不开 revise 实测量级 15–40s（SDK 官方口径 revise 额外
# +30s，已关）。单次 POST 一把 120s 总闸，不设瞬态重试——失败交对冲池另一路。
I2I_TIMEOUT_SECONDS = 120.0
_JSON_MAX_BYTES = 1024 * 1024
# 输入闸：解码前原始字节上限（防解压炸弹拖垮 event loop 线程）；上传缩图目标。
_INPUT_MAX_BYTES = 32 * 1024 * 1024
_UPLOAD_MAX_EDGE = 1024
_UPLOAD_MAX_BYTES = 3 * 1024 * 1024


class TcbI2iError(Exception):
    """TCB i2i 窄异常（请求/应答结构/下载/输入图异常统一收敛）。"""


def _shrink_for_upload(png_bytes: bytes) -> bytes:
    """PNG 字节 → 长边 ≤1024、编码后 ≤3MiB 的 PNG 字节（同步 CPU，量级 100ms）。

    解码失败/超限一律 TcbI2iError；调用方（portal）已保证输入是 PNG。
    """
    import imageio.v3 as iio
    import numpy as np
    from skimage import transform

    if len(png_bytes) > _INPUT_MAX_BYTES:
        raise TcbI2iError("i2i 输入图超过 32MiB 上限")
    try:
        arr = iio.imread(png_bytes)
    except Exception:
        raise TcbI2iError("i2i 输入图不是可解码的 PNG") from None

    def _resize(a, edge: int) -> bytes:
        h, w = a.shape[:2]
        scale = edge / max(h, w)
        out_shape = (max(1, round(h * scale)), max(1, round(w * scale))) + a.shape[2:]
        resized = transform.resize(a, out_shape, anti_aliasing=True, preserve_range=True)
        buf = io.BytesIO()
        iio.imwrite(buf, np.clip(resized, 0, 255).astype(np.uint8), extension=".png")
        return buf.getvalue()

    out = png_bytes
    if max(arr.shape[:2]) > _UPLOAD_MAX_EDGE:
        out = _resize(arr, _UPLOAD_MAX_EDGE)
    if len(out) > _UPLOAD_MAX_BYTES:
        out = _resize(arr, _UPLOAD_MAX_EDGE // 2)
    if len(out) > _UPLOAD_MAX_BYTES:
        raise TcbI2iError("i2i 输入图缩图后仍超过 3MiB 上限")
    return out


async def _read_capped(resp: httpx.Response, limit: int, what: str) -> bytes:
    """流式读取 + 双闸（声明值/逐块累计），与 modelscope_client 同口径。"""
    cl = resp.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > limit:
        raise TcbI2iError(f"{what}超过读取上限")
    buf = bytearray()
    async for chunk in resp.aiter_bytes(64 * 1024):
        buf.extend(chunk)
        if len(buf) > limit:
            raise TcbI2iError(f"{what}超过读取上限")
    return bytes(buf)


async def request_i2i_image_url(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    image_bytes: bytes,
    *,
    prompt: str = modelscope_client.I2I_LINE_ART_PROMPT,
    timeout_seconds: float = I2I_TIMEOUT_SECONDS,
) -> str:
    """i2i 单次 POST：返回结果图 URL（下载交回调用方）。

    提示词默认复用魔搭链的线稿提示词（同一精修意图，单源不抄第二份）。
    """
    if not base_url or not api_key:
        raise TcbI2iError("TCB i2i 未配置（缺 URL 或 key）")
    payload = {
        "prompt": prompt,
        "image": base64.b64encode(_shrink_for_upload(image_bytes)).decode("ascii"),
        "mode": "i2i",
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with asyncio.timeout(timeout_seconds):
            resp = await client.post(base_url, json=payload, headers=headers)
            raw = await _read_capped(resp, _JSON_MAX_BYTES, "TCB i2i 响应")
    except (httpx.HTTPError, TimeoutError) as exc:
        raise TcbI2iError(f"TCB i2i 请求网络错误（{type(exc).__name__}）") from None
    if resp.status_code != 200:
        # 401=网关 key 失效（改函数不动网关，key 仍是同一把）；5xx=函数/网关故障。
        # 不回显响应体：网关错误文本可能带环境细节，只记状态码。
        raise TcbI2iError(f"TCB i2i 返回 {resp.status_code}")
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise TcbI2iError("TCB i2i 响应不是合法 JSON") from None
    if not isinstance(data, dict):
        raise TcbI2iError("TCB i2i 响应结构异常")
    if data.get("success") is not True:
        # error 字段是函数自由文本，不回显（与 t2i racer 同口径）。
        raise TcbI2iError("TCB i2i 未出图（success=false）")
    image_url = data.get("imageUrl")
    if not isinstance(image_url, str) or not image_url:
        raise TcbI2iError("TCB i2i 未返回有效的图片地址")
    return image_url


async def generate_i2i_png(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    image_bytes: bytes,
    *,
    prompt: str = modelscope_client.I2I_LINE_ART_PROMPT,
    timeout_seconds: float = I2I_TIMEOUT_SECONDS,
) -> bytes:
    """i2i 一条龙：POST → imageUrl → url_guard 下载 PNG 字节。

    下载复用 modelscope_client.download_image（公网 https 闸唯一实现口径，
    不得为 TCB 另开下载路径）；异常映射为 TcbI2iError 保持调用方窄捕获。
    """
    image_url = await request_i2i_image_url(
        client,
        base_url,
        api_key,
        image_bytes,
        prompt=prompt,
        timeout_seconds=timeout_seconds,
    )
    try:
        return await modelscope_client.download_image(client, image_url)
    except ModelScopeError as exc:
        raise TcbI2iError(str(exc)) from None
