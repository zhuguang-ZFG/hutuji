"""hutuji ③ MCP 生成服务配置（全部走环境变量，禁止 .env）。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key)


def load_settings() -> Settings:
    """从环境变量读取配置，缺省值面向本机开发。"""
    return Settings(
        llm_base_url=os.environ.get("HUTUJI_LLM_BASE_URL", "https://api.freemodel.dev/v1"),
        llm_api_key=os.environ.get("HUTUJI_LLM_API_KEY", ""),
        llm_model=os.environ.get("HUTUJI_LLM_MODEL", "auto"),
        output_dir=Path(os.environ.get("HUTUJI_OUTPUT_DIR", "./output")).resolve(),
        public_base_url=os.environ.get("HUTUJI_PUBLIC_BASE_URL", "http://127.0.0.1:8300"),
        # 默认仅本机监听；公网只让 nginx 反代 /files，绝不直曝 /mcp。
        host=os.environ.get("HUTUJI_HOST", "127.0.0.1"),
        port=int(os.environ.get("HUTUJI_PORT", "8300")),
        output_ttl_seconds=int(os.environ.get("HUTUJI_OUTPUT_TTL_SECONDS", "600")),
    )


settings = load_settings()
