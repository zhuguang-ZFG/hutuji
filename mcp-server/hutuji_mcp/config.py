"""hutuji ③ MCP 生成服务配置（全部走环境变量，禁止 .env）。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def validate_llm_base_url(value: str) -> str:
    """校验 LLM API 基址；本机开发允许 loopback HTTP，公网强制 HTTPS。

    API key 会经该基址以明文 header 传输，配成 http:// 的公网地址等于裸奔；
    开发用本地推理服务（如 http://127.0.0.1:11434）仍放行。"""
    value = value.strip()
    if "?" in value or "#" in value:
        raise ValueError("HUTUJI_LLM_BASE_URL 不允许裸 ? 或 #")
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("HUTUJI_LLM_BASE_URL 端口无效") from exc
    if (
        not host
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("HUTUJI_LLM_BASE_URL 必须是无凭据、无查询的绝对 URL")
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("HUTUJI_LLM_BASE_URL scheme 不受支持")
    if host not in _LOOPBACK_HOSTS and parsed.scheme != "https":
        raise ValueError("公网 HUTUJI_LLM_BASE_URL 必须使用 HTTPS")
    return value.rstrip("/")


def validate_public_base_url(value: str) -> str:
    """校验能力 URL 公网边界；本机开发仍允许 loopback HTTP。"""
    value = value.strip()
    if "?" in value or "#" in value:
        # urlsplit 对裸 `?`/`#` 给出空 query/fragment，但分隔符本身会让拼接出的
        # 能力 URL 路径变成查询/片段。显式拒绝，避免产出 `https://host?/files/x`。
        raise ValueError("HUTUJI_PUBLIC_BASE_URL 不允许裸 ? 或 #")
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("HUTUJI_PUBLIC_BASE_URL 端口无效") from exc
    if (
        not host
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("HUTUJI_PUBLIC_BASE_URL 必须是无凭据、无路径、无查询的绝对 URL")
    if host in _LOOPBACK_HOSTS:
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("HUTUJI_PUBLIC_BASE_URL scheme 不受支持")
        return value.rstrip("/")
    if parsed.scheme != "https" or (port is not None and port != 443):
        raise ValueError("公网 HUTUJI_PUBLIC_BASE_URL 必须使用 HTTPS 默认端口")
    return value.rstrip("/")

@dataclass(frozen=True)
class Settings:
    # LLM（OpenAI 兼容接口）三件套
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    # G-code 产出目录与对外 URL 前缀
    output_dir: Path
    public_base_url: str
    # HTTP 服务监听与产物生命周期
    host: str
    port: int
    output_ttl_seconds: int

    # 文生图模型（hutuji_draw 的位图路线）；与 LLM 共用 base_url/api_key。
    # 带默认值：既有测试构造点不必全跟着加参数。
    image_model: str = "agnes-image-2.1-flash"

    def __post_init__(self) -> None:
        object.__setattr__(self, "public_base_url", validate_public_base_url(self.public_base_url))
        object.__setattr__(self, "llm_base_url", validate_llm_base_url(self.llm_base_url))
        # 非 loopback 一律拒绝：锁定决策是「/mcp 只本机可达，公网由 nginx 404」。
        # 生产 unit 用 `--host 127.0.0.1` 钉死且 `-m uvicorn` 不读本字段，所以设成
        # 0.0.0.0 在生产静默无效、在开发直跑 `python -m hutuji_mcp.server` 却是真裸曝。
        # 静默无效比报错危险：它让人误以为监听面已改。两条路径在此统一口径。
        if self.host.lower() not in _LOOPBACK_HOSTS:
            raise ValueError(
                f"HUTUJI_HOST 只允许 loopback（{sorted(_LOOPBACK_HOSTS)}），当前 {self.host!r}；"
                "公网访问只经 nginx 反代 /files，绝不直曝 /mcp"
            )
        if not 1 <= self.port <= 65535:
            raise ValueError(f"port 必须在 1..65535，当前 {self.port}")
        if self.output_ttl_seconds <= 0:
            raise ValueError(f"output_ttl_seconds 必须 > 0，当前 {self.output_ttl_seconds}")

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key)


def _env_int(name: str, default: str) -> int:
    raw = os.environ.get(name, default)
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是整数，当前 {raw!r}") from exc


def load_settings() -> Settings:
    """从环境变量读取配置，缺省值面向本机开发。"""
    return Settings(
        llm_base_url=os.environ.get("HUTUJI_LLM_BASE_URL", "https://api.freemodel.dev/v1"),
        llm_api_key=os.environ.get("HUTUJI_LLM_API_KEY", ""),
        llm_model=os.environ.get("HUTUJI_LLM_MODEL", "auto"),
        image_model=os.environ.get("HUTUJI_IMAGE_MODEL", "agnes-image-2.1-flash"),
        output_dir=Path(os.environ.get("HUTUJI_OUTPUT_DIR", "./output")).resolve(),
        public_base_url=os.environ.get("HUTUJI_PUBLIC_BASE_URL", "http://127.0.0.1:8300"),
        # 默认仅本机监听；公网只让 nginx 反代 /files，绝不直曝 /mcp。
        host=os.environ.get("HUTUJI_HOST", "127.0.0.1"),
        port=_env_int("HUTUJI_PORT", "8300"),
        output_ttl_seconds=_env_int("HUTUJI_OUTPUT_TTL_SECONDS", "600"),
    )


settings = load_settings()
