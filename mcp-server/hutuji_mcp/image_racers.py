"""运营后台下发的动态生图 racer（portal image_racers → 对冲任务）。"""
from __future__ import annotations

import itertools
from typing import Any

import httpx

from . import bitmap_svg

_KEY_CYCLE: dict[str, itertools.cycle[str]] = {}
_KEY_CYCLE_SNAPSHOT: dict[str, tuple[str, ...]] = {}


def _pick_api_key(racer_id: str, keys: list[str]) -> str:
    if not keys:
        return ""
    snap = tuple(keys)
    if _KEY_CYCLE_SNAPSHOT.get(racer_id) != snap:
        _KEY_CYCLE[racer_id] = itertools.cycle(keys)
        _KEY_CYCLE_SNAPSHOT[racer_id] = snap
    return next(_KEY_CYCLE[racer_id])


async def request_racer_image_url(
    client: httpx.AsyncClient,
    description: str,
    racer: dict[str, Any],
) -> str:
    provider_type = str(racer.get("provider_type") or "")
    name = str(racer.get("name") or racer.get("id") or provider_type)
    base_url = str(racer.get("base_url") or "").rstrip("/")
    model = str(racer.get("model") or "")
    extra = racer.get("extra") if isinstance(racer.get("extra"), dict) else {}
    api_key = _pick_api_key(str(racer.get("id") or name), list(racer.get("api_keys") or []))

    if provider_type == "agnes":
        url = f"{base_url}/images/generations"
        payload = {
            "model": model,
            "prompt": bitmap_svg._BITMAP_STYLE + description,
            "size": extra.get("image_size", bitmap_svg._IMAGE_SIZE),
            "n": 1,
        }
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        data = await bitmap_svg._post_image_request(
            client, url, payload, headers, name
        )
        return bitmap_svg._extract_agnes_image_url(data)

    if provider_type == "sensenova":
        url = f"{base_url}/images/generations"
        payload = {
            "model": model,
            "prompt": bitmap_svg._BITMAP_STYLE + description,
            "size": extra.get("image_size", bitmap_svg._SENSENOVA_IMAGE_SIZE),
            "n": 1,
            "watermark": False,
        }
        headers = {"Authorization": f"Bearer {api_key}"}
        data = await bitmap_svg._post_image_request(
            client,
            url,
            payload,
            headers,
            name,
            retry_delays=bitmap_svg._SENSENOVA_RETRY_DELAYS_SECONDS,
            attempt_timeout_seconds=bitmap_svg._SENSENOVA_ATTEMPT_TIMEOUT_SECONDS,
        )
        return bitmap_svg._extract_agnes_image_url(data)

    if provider_type == "siliconflow":
        url = f"{base_url}/images/generations"
        payload = {
            "model": model,
            "prompt": bitmap_svg._BITMAP_STYLE + description,
            "image_size": extra.get("image_size", bitmap_svg._IMAGE_SIZE),
            "batch_size": 1,
        }
        for key in ("num_inference_steps", "guidance_scale"):
            if key in extra:
                payload[key] = extra[key]
        headers = {"Authorization": f"Bearer {api_key}"}
        data = await bitmap_svg._post_image_request(
            client,
            url,
            payload,
            headers,
            name,
            retry_delays=(),
            attempt_timeout_seconds=bitmap_svg._SILICONFLOW_ATTEMPT_TIMEOUT_SECONDS,
        )
        try:
            image_url = data["images"][0]["url"]
        except (KeyError, IndexError, TypeError) as exc:
            raise bitmap_svg.BitmapError(
                f"{name} 响应结构异常（{type(exc).__name__}）"
            ) from exc
        if not isinstance(image_url, str):
            raise bitmap_svg.BitmapError(f"{name} 未返回有效的 https 图片地址")
        return image_url

    if provider_type == "custom_post":
        payload = {
            "prompt": bitmap_svg._BITMAP_STYLE + description,
            **{k: v for k, v in extra.items() if k not in {"image_size"}},
        }
        if "aspect_ratio" not in payload and "aspect_ratio" in extra:
            payload["aspect_ratio"] = extra["aspect_ratio"]
        data = await bitmap_svg._post_image_request(
            client, base_url, payload, None, name, retry_delays=()
        )
        status = data.get("status")
        if status != "completed":
            raise bitmap_svg.BitmapError(f"{name} 未出图（status={status!r}）")
        image_url = data.get("image_url")
        if not isinstance(image_url, str):
            raise bitmap_svg.BitmapError(f"{name} 未返回有效的 https 图片地址")
        return image_url

    raise bitmap_svg.BitmapError(f"未知 provider_type {provider_type!r}")
