"""魔搭 ModelScope API-INFERENCE 生图客户端：t2i 对冲 racer 与 i2i 照片精修共用。

实证契约（2026-09-08，用户 token 对 api-inference.modelscope.cn 实测）：
- 提交 POST {base}/v1/images/generations → {"task_id": ...}；i2i 的图字段名是
  image_url（传 image 报 400 "image_url is required"），data URI 可用。
- 轮询 GET {base}/v1/tasks/{task_id}，须带请求头
  X-ModelScope-Task-Type: image_generation；task_status 走
  PENDING / RUNNING / SUCCEED / FAILED，结果 URL 在 output_images[0]。
- 免费层有日配额且会排队（实测 Qwen-Image-Edit-2509 排队约 3 分钟），故轮询
  上限独立于对冲池 45s 单次超时；提交本身是异步任务，没有同步出图模式。

凭据纪律（与 redact 硬约定同）：token 只进请求头；异常与日志只含状态码/
错误类型名，不回显上游正文（可能回显请求片段）与 URL。
"""
from __future__ import annotations

import asyncio
import base64
import json

import httpx

from .url_guard import UrlGuardError, validate_public_https_url

BASE_URL = "https://api-inference.modelscope.cn"
T2I_MODEL = "Qwen/Qwen-Image"
I2I_MODEL = "Qwen/Qwen-Image-Edit-2509"

# 照片→艺术家素描（笔式可画）提示词 v6（2026-09-10）：
# 按 Qwen-Image-Edit 官方 EDIT_SYSTEM_PROMPT（src/examples/tools/prompt_utils.py）
# 重构：指令直述 + 人像身份约束前置（五官/脸型/发型/表情 +「其余保持不变」
# 官方人像范式）+ 风格特征描述压句尾（官方风格转换规则：风格用关键视觉特征
# 描述且放句尾）；否定排比保留——Edit 系官方接受否定约束（与 t2i 不同）。
# 「排线组数少/每条长/间距大/方向统一/只盖暗面」是 v5 实证有效的排线特征词，
# 原样保留只调语序。supersede v5（v2 均匀单线、v3 密交叉、v4 短排线+无排线轮廓回退）。
I2I_LINE_ART_PROMPT = (
    "把这张照片改画成黑白钢笔素描，其余保持不变：人物须严格保持五官、脸型、"
    "发型、表情与姿势，必须能看出是本人；物体与场景须能一眼辨认。"
    "纯白背景上只画黑色线条，外轮廓与五官、主体结构线清晰连续，"
    "阴影处用排线表现体积。风格：钢笔绘图仪素描——排线组数少、每条尽量长、"
    "间距大、方向统一，只覆盖暗面，有明显素描排线感；禁止密交叉排线、"
    "禁止雕版网点，禁止短碎排线糊成一片，不要阴影涂抹、不要灰度渐变、"
    "不要颜色、不要文字、不要水印，不要均匀单线描边的上色本风格，"
    "不要照片写实灰阶"
)

# 抬笔偏高时的第二轮：仍保留排线（量产不可丢掉素描感），但更稀、更长、更少。
# 与主提示词同 v6 结构（身份前置、风格句尾、否定保留）。
I2I_SPARSE_HATCH_RETRY_PROMPT = (
    "把这张照片改画成钢笔素描线稿，其余保持不变：严格保持主体可辨认"
    "（人物须能看出是本人）。纯白背景上只画黑色线条：清晰外轮廓与五官、"
    "主体结构线，暗面用少量排线暗示明暗。风格：极简排线钢笔素描——"
    "排线组数尽量少、每条尽量长、间距更大、方向统一；"
    "禁止交叉排线与密网点，不要短碎线，不要阴影涂抹，不要灰度，"
    "不要颜色，不要文字，不要水印"
)

# 兼容旧测试名：轮廓回退已废弃，指向仍带排线的稀疏重试。
I2I_CONTOUR_FALLBACK_PROMPT = I2I_SPARSE_HATCH_RETRY_PROMPT

# 免费层排队实测 ~3min：i2i 精修给 330s（后台任务，用户已先拿到 Canny 预览）；
# t2i racer 只给 120s——它是「有机会赢」的对冲成员，不为它拖住整单。
POLL_INTERVAL_SECONDS = 4.0
I2I_TIMEOUT_SECONDS = 330.0
T2I_TIMEOUT_SECONDS = 120.0
# 单次 HTTP 请求（提交/轮询/下载各一跳）的独立超时。
_REQUEST_TIMEOUT_SECONDS = 30.0
_DOWNLOAD_TIMEOUT_SECONDS = 60.0

_JSON_MAX_BYTES = 1024 * 1024
_IMAGE_MAX_BYTES = 16 * 1024 * 1024
# 轮询遇瞬态故障（429/5xx/网络异常）允许连续重试次数：3 分钟排队窗口里
# 一次抖动不该杀死整个精修任务。
_POLL_TRANSIENT_TOLERANCE = 3
_TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})


class ModelScopeError(Exception):
    """魔搭生图窄异常（提交/轮询/下载/结构异常统一收敛）。"""


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _read_capped(resp: httpx.Response, limit: int, what: str) -> bytes:
    """流式读取 + 双闸（声明值/逐块累计），与 bitmap_svg R10-BMP-03 同口径。"""
    declared = resp.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise ModelScopeError(f"魔搭{what}超过读取上限")
    buf = bytearray()
    async for chunk in resp.aiter_bytes():
        if len(buf) + len(chunk) > limit:
            raise ModelScopeError(f"魔搭{what}超过读取上限")
        buf.extend(chunk)
    return bytes(buf)


def _parse_json(raw: bytes, what: str) -> dict:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        # 200 但正文不是 JSON（CDN/网关 HTML 错误页）：重试同一份垃圾无意义。
        raise ModelScopeError(f"魔搭{what}不是合法 JSON") from None
    if not isinstance(data, dict):
        raise ModelScopeError(f"魔搭{what}结构异常（TypeError）")
    return data


async def _request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    payload: dict | None,
    what: str,
) -> tuple[int, bytes]:
    """单跳请求：30s 超时 + 流式上限闸；网络异常统一收敛为 ModelScopeError。"""
    try:
        async with asyncio.timeout(_REQUEST_TIMEOUT_SECONDS):
            async with client.stream(
                method, url, json=payload, headers=headers
            ) as resp:
                status = resp.status_code
                raw = (
                    await _read_capped(resp, _JSON_MAX_BYTES, what)
                    if status == 200
                    # 错误正文不回显不读取（可能回显请求片段/凭据），只记状态码。
                    else b""
                )
    except (httpx.HTTPError, TimeoutError) as exc:
        raise ModelScopeError(f"魔搭{what}网络错误（{type(exc).__name__}）") from None
    return status, raw


async def submit_task(
    client: httpx.AsyncClient,
    token: str,
    *,
    model: str,
    prompt: str,
    image_data_uri: str | None = None,
    base_url: str = BASE_URL,
) -> str:
    """提交异步生图任务，返回 task_id。i2i 走 image_data_uri（字段名 image_url）。"""
    payload: dict = {"model": model, "prompt": prompt}
    if image_data_uri is not None:
        payload["image_url"] = image_data_uri
    status, raw = await _request(
        client,
        "POST",
        f"{base_url.rstrip('/')}/v1/images/generations",
        headers=_headers(token),
        payload=payload,
        what="提交",
    )
    if status != 200:
        raise ModelScopeError(f"魔搭提交返回 {status}")
    task_id = _parse_json(raw, "提交响应").get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise ModelScopeError("魔搭未返回 task_id")
    return task_id


async def poll_task(
    client: httpx.AsyncClient,
    token: str,
    task_id: str,
    *,
    timeout_seconds: float,
    interval_seconds: float = POLL_INTERVAL_SECONDS,
    base_url: str = BASE_URL,
) -> str:
    """轮询任务直到 SUCCEED，返回结果图 URL；FAILED/超时/瞬态连失败皆窄异常。"""
    url = f"{base_url.rstrip('/')}/v1/tasks/{task_id}"
    headers = {**_headers(token), "X-ModelScope-Task-Type": "image_generation"}
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    transient_failures = 0
    while True:
        if asyncio.get_running_loop().time() >= deadline:
            raise ModelScopeError("魔搭任务排队超时")
        try:
            status, raw = await _request(
                client, "GET", url, headers=headers, payload=None, what="轮询"
            )
        except ModelScopeError:
            transient_failures += 1
            if transient_failures > _POLL_TRANSIENT_TOLERANCE:
                raise
            await asyncio.sleep(interval_seconds)
            continue
        if status != 200:
            if status in _TRANSIENT_STATUSES:
                transient_failures += 1
                if transient_failures > _POLL_TRANSIENT_TOLERANCE:
                    raise ModelScopeError(f"魔搭轮询返回 {status}")
                await asyncio.sleep(interval_seconds)
                continue
            raise ModelScopeError(f"魔搭轮询返回 {status}")
        transient_failures = 0
        data = _parse_json(raw, "轮询响应")
        task_status = data.get("task_status")
        if task_status == "SUCCEED":
            images = data.get("output_images")
            if isinstance(images, list) and images and isinstance(images[0], str) and images[0]:
                return images[0]
            raise ModelScopeError("魔搭未返回结果图地址")
        if task_status == "FAILED":
            # errors/message 是上游自由文本（内容安全拦截原因等），不回显。
            raise ModelScopeError("魔搭任务失败")
        if task_status not in ("PENDING", "RUNNING"):
            raise ModelScopeError(f"魔搭任务状态异常（{task_status!r}）")
        await asyncio.sleep(interval_seconds)


async def download_image(
    client: httpx.AsyncClient,
    image_url: str,
    *,
    timeout_seconds: float = _DOWNLOAD_TIMEOUT_SECONDS,
) -> bytes:
    """下载魔搭结果图（URL 来自任务回包）；公网 https 闸 + 16MiB 上限。"""
    # 任务回包同样可能含内网地址：与生图主链同一把 SSRF 闸（IMG-URL-SSRF-GAP）。
    try:
        validate_public_https_url(image_url)
    except UrlGuardError as exc:
        raise ModelScopeError(str(exc)) from None
    try:
        async with asyncio.timeout(timeout_seconds):
            async with client.stream("GET", image_url) as resp:
                if resp.status_code != 200:
                    raise ModelScopeError(f"魔搭结果图下载返回 {resp.status_code}")
                return await _read_capped(resp, _IMAGE_MAX_BYTES, "结果图")
    except (httpx.HTTPError, TimeoutError) as exc:
        raise ModelScopeError(f"魔搭结果图下载网络错误（{type(exc).__name__}）") from None


def to_image_data_uri(png_bytes: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")


async def generate_image_url(
    client: httpx.AsyncClient,
    token: str,
    *,
    prompt: str,
    model: str = T2I_MODEL,
    timeout_seconds: float = T2I_TIMEOUT_SECONDS,
    base_url: str = BASE_URL,
) -> str:
    """t2i：提交+轮询，返回结果图 URL（下载交回调用方/对冲池共享下载链）。"""
    task_id = await submit_task(client, token, model=model, prompt=prompt, base_url=base_url)
    return await poll_task(
        client, token, task_id, timeout_seconds=timeout_seconds, base_url=base_url
    )


async def generate_i2i_png(
    client: httpx.AsyncClient,
    token: str,
    image_bytes: bytes,
    *,
    prompt: str = I2I_LINE_ART_PROMPT,
    model: str = I2I_MODEL,
    timeout_seconds: float = I2I_TIMEOUT_SECONDS,
    base_url: str = BASE_URL,
) -> bytes:
    """i2i 照片精修一条龙：data URI 提交 → 轮询 → 下载 PNG 字节。"""
    task_id = await submit_task(
        client,
        token,
        model=model,
        prompt=prompt,
        image_data_uri=to_image_data_uri(image_bytes),
        base_url=base_url,
    )
    image_url = await poll_task(
        client, token, task_id, timeout_seconds=timeout_seconds, base_url=base_url
    )
    return await download_image(client, image_url)
