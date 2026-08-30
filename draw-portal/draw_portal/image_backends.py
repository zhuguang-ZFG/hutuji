"""运营后台生图后端开关（不含 API key，key 仍走 MCP 环境变量）。"""
from __future__ import annotations

IMAGE_BACKEND_NAMES: tuple[str, ...] = (
    "sensenova",
    "agnes",
    "siliconflow",
    "fallback",
)

# platform_settings 表键名
IMAGE_BACKEND_DB_KEYS: dict[str, str] = {
    "sensenova": "image_backend_sensenova",
    "agnes": "image_backend_agnes",
    "siliconflow": "image_backend_siliconflow",
    "fallback": "image_backend_fallback",
}

IMAGE_BACKEND_LABELS: dict[str, str] = {
    "sensenova": "商汤 U1 Fast",
    "agnes": "Agnes 主链",
    "siliconflow": "硅流（Schnell + 可图）",
    "fallback": "imagefree 备用端点",
}


def default_image_backend_toggles() -> dict[str, bool]:
    return {name: True for name in IMAGE_BACKEND_NAMES}


def validate_image_backend_patch(raw: dict[str, object]) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for key, value in raw.items():
        if key not in IMAGE_BACKEND_NAMES:
            raise ValueError(f"未知生图后端 {key!r}")
        if value is None:
            continue
        if isinstance(value, bool):
            out[key] = value
            continue
        if isinstance(value, (int, float)) and value in (0, 1):
            out[key] = bool(value)
            continue
        if isinstance(value, str):
            text = value.strip().lower()
            if text in ("1", "true", "yes", "on"):
                out[key] = True
                continue
            if text in ("0", "false", "no", "off"):
                out[key] = False
                continue
        raise ValueError(f"{key} 必须是布尔开关")
    return out
