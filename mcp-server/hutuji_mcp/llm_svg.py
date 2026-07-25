"""调 OpenAI 兼容 LLM 生成单线笔画 SVG。

写字机画的是"笔画"不是"填充"，所以 prompt 强制模型只输出
path/line/polyline 的 stroke 图形，并限制在 190×190mm 画布内。
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import httpx

from .config import settings

# 画布与 system prompt（单线笔画约束是本模块的核心）
CANVAS_MM = 190

SYSTEM_PROMPT = f"""你是一个写字机绘图助手。根据用户的描述，生成一份可直接绘图的单线笔画 SVG。

硬性要求：
1. 只输出一个 <svg> 文档，不要输出任何解释文字、不要用 Markdown 代码块。
2. 画布：width="{CANVAS_MM}mm" height="{CANVAS_MM}mm" viewBox="0 0 {CANVAS_MM} {CANVAS_MM}"，所有坐标必须在 0~{CANVAS_MM} 范围内并留边距。
3. 只能用 <path>、<line>、<polyline>、<polygon>、<circle>、<ellipse>、<rect> 基本形状；
   所有元素只描边不填充：fill="none"，stroke="black"，stroke-width 在 0.3~1 之间。
4. 这是单线写字机：禁止填充色块、禁止渐变、禁止滤镜、禁止 <text>（要写字请用折线拼出单线字形）、禁止 <image>。
5. 线条尽量连续、简洁，总长度控制在合理范围（这是一台真实的机器在逐笔画图）。"""

_SVG_RE = re.compile(r"<svg[\s\S]*?</svg>", re.IGNORECASE)


class LlmError(RuntimeError):
    """LLM 调用或 SVG 解析失败。"""


def _extract_svg(text: str) -> str:
    """从模型回复中提取 <svg…</svg>，容忍代码块包裹与前后废话。"""
    match = _SVG_RE.search(text)
    if not match:
        raise LlmError(f"LLM 回复中未找到 <svg> 内容：{text[:200]}...")
    svg = match.group(0)
    _validate_svg(svg)
    return svg


def _validate_svg(svg: str) -> None:
    """基础校验：合法 XML、根标签是 svg。"""
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as e:
        raise LlmError(f"SVG 不是合法 XML：{e}") from e
    tag = root.tag.rsplit("}", 1)[-1].lower()  # 容忍命名空间前缀
    if tag != "svg":
        raise LlmError(f"根标签不是 svg：{root.tag}")


async def generate_svg(description: str) -> str:
    """调 chat completions 生成 SVG 文本。未配置 API key 时抛清晰错误。"""
    if not settings.llm_api_key:
        raise LlmError(
            "未配置 LLM API key：请设置环境变量 HUTUJI_LLM_API_KEY"
            "（另见 HUTUJI_LLM_BASE_URL / HUTUJI_LLM_MODEL）"
        )

    url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"请画：{description}"},
        ],
        "temperature": 0.7,
    }
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"}

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(url, json=payload, headers=headers)
    if resp.status_code != 200:
        raise LlmError(f"LLM API 返回 {resp.status_code}：{resp.text[:300]}")

    data = resp.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise LlmError(f"LLM 响应格式异常：{e}") from e
    return _extract_svg(content)
