"""小程序 AI 写文：主题生成 / 草稿润色（纯文本，禁止 SVG/G-code）。

复用 HUTUJI_LLM_* 与 llm_svg 同源网关；只回简体正文给排版链。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

import httpx

from . import t2s
from .config import settings
from .llm_svg import LlmError, _CONTROL_CHAR_RE

logger = logging.getLogger(__name__)

ARTICLE_AI_MAX_CHARS = 2000
ARTICLE_PROMPT_MAX_CHARS = 500
ARTICLE_LLM_MAX_TOKENS = 1600
ARTICLE_LLM_TIMEOUT_SECONDS = 60.0
# 回包字节上限：文章正文短（≤2000 字 ≈ 6KiB），64KiB 远够正常生成；
# 必须在网络读取阶段生效，防故障/被接管上游推爆内存（与 chat._call_llm 同口径机制）。
ARTICLE_LLM_MAX_RESPONSE_BYTES = 64 * 1024

_GENERATE_SYSTEM = """你是中文写作助手，为手写字机生成可誊抄的简体正文。
硬规则：
- 只输出正文，不要标题栏、不要 Markdown、不要列表符号装饰、不要解释。
- 用换行分段；段与段之间一个空行即可。
- 不要输出 SVG、HTML、代码、G-code、JSON。
- 不要编造需保密的身份证/银行卡/验证码内容。
- 篇幅适中，适合小学生到成人手抄，避免超长空话。"""

_POLISH_SYSTEM = """你是中文润色助手，在保留原意与事实的前提下润色或适度扩写用户草稿。
硬规则：
- 只输出润色后的正文，不要解释改了什么。
- 不要改题跑偏；不要新增与草稿无关的长篇情节。
- 用换行分段；不要 Markdown/SVG/HTML/代码/G-code/JSON。
- 输出必须是简体中文为主（可含必要英文专名）。"""


def _clean_prompt(text: str, limit: int) -> str:
    if not isinstance(text, str):
        return ""
    return _CONTROL_CHAR_RE.sub("", text).strip()[:limit]


def _strip_model_wrappers(text: str) -> str:
    body = text.strip()
    if body.startswith("```"):
        body = re.sub(r"^```(?:\w+)?\s*", "", body)
        body = re.sub(r"\s*```$", "", body)
    return body.strip()


def _reject_non_article(text: str) -> None:
    low = text.lower()
    if "<svg" in low or "g0 " in low or "g1" in low[:200] and "x" in low[:200]:
        raise LlmError("生成结果不可用，请换个主题再试")
    if text.lstrip().startswith("{") and "\"url\"" in text:
        raise LlmError("生成结果不可用，请换个主题再试")


async def generate_or_polish(*, mode: str, prompt: str = "", text: str = "") -> str:
    """mode=generate|polish → 简体正文（已截断）。"""
    if mode not in ("generate", "polish"):
        raise LlmError("不支持的写作模式")
    if not settings.llm_api_key:
        logger.warning("文章 LLM 未配置 HUTUJI_LLM_API_KEY")
        raise LlmError("写作服务未就绪，请稍后再试")

    if mode == "generate":
        user = _clean_prompt(prompt, ARTICLE_PROMPT_MAX_CHARS)
        if not user:
            raise LlmError("请先写主题或提纲")
        system = _GENERATE_SYSTEM
        user_msg = f"请根据主题/提纲写一篇可手抄的短文：\n{user}"
    else:
        user = _clean_prompt(text, ARTICLE_AI_MAX_CHARS)
        if not user:
            raise LlmError("请先粘贴要润色的草稿")
        system = _POLISH_SYSTEM
        user_msg = f"请润色或适度扩写下面草稿：\n{user}"

    url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.6,
        "max_tokens": ARTICLE_LLM_MAX_TOKENS,
    }
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"}

    body = bytearray()
    try:
        async with asyncio.timeout(ARTICLE_LLM_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(timeout=ARTICLE_LLM_TIMEOUT_SECONDS) as client:
                async with client.stream("POST", url, json=payload, headers=headers) as resp:
                    if resp.status_code != 200:
                        logger.warning("文章 LLM HTTP %s", resp.status_code)
                        # 只带状态码，不回显上游正文
                        raise LlmError(f"写作服务返回 {resp.status_code}")
                    async for chunk in resp.aiter_bytes(16384):
                        body.extend(chunk)
                        if len(body) > ARTICLE_LLM_MAX_RESPONSE_BYTES:
                            raise LlmError("写作服务响应超过大小上限")
    except LlmError:
        raise
    except (httpx.HTTPError, TimeoutError) as exc:
        raise LlmError(f"调用写作服务失败：{type(exc).__name__}") from exc

    try:
        data = json.loads(bytes(body).decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
    except (UnicodeDecodeError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise LlmError("写作服务回包异常") from exc

    if not isinstance(content, str):
        raise LlmError("写作服务回包异常")

    article = _strip_model_wrappers(content)
    article = t2s.to_simplified(_CONTROL_CHAR_RE.sub("", article).strip())
    if not article:
        raise LlmError("没有写出内容，请换个说法再试")
    _reject_non_article(article)
    if len(article) > ARTICLE_AI_MAX_CHARS:
        article = article[:ARTICLE_AI_MAX_CHARS].rstrip()
    return article
