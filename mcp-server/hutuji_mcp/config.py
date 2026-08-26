"""hutuji ③ MCP 生成服务配置（全部走环境变量，禁止 .env）。"""
from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


_LOOPBACK_HOST_NAMES = frozenset({"localhost"})


def _is_loopback_host(host: str) -> bool:
    """与 server.LoopbackOnlyMcpGuard 同口径：ipaddress 判整段 127/8 与 ::1，
    另放行 localhost 主机名（守卫按 ASGI socket 数值地址，见不到主机名）。
    旧实现用 3 字符串集合，对 127.0.0.2 这类 loopback 地址会误当公网。"""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in _LOOPBACK_HOST_NAMES


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
    if not _is_loopback_host(host) and parsed.scheme != "https":
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
    if _is_loopback_host(host):
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

    # 文生图备用端点（agnes 主链失败时的降级链；imagefree 免鉴权公共服务，
    # 置空=禁用）。默认即生产可用值，env 只用于覆盖或关闭。
    image_fallback_url: str = "https://imagefree.tingfengai.art/v1/generate"
    # 可选商汤 U1 Fast PNG 主后端；三项全空=保持 Agnes，任一配置则必须三项齐全。
    sensenova_base_url: str = ""
    sensenova_api_key: str = ""
    sensenova_image_model: str = ""
    # SiliconFlow：可图已备案（Beijing-KeTu-202404280003）；单 key 即启用
    # schnell + 可图双 racer，空值禁用。base URL 固定官方端点，不走配置面。
    siliconflow_api_key: str = ""
    # Quick Draw 本地缓存目录（quickdraw.lookup 的数据根；设计稿
    # docs/design/quickdraw-local-cache.md）。空值 = 禁用并回落 AI 对冲；
    # import 期不探测目录存在性（生产目录由部署保证），首次 lookup 惰性确认，
    # 缺失/不可读按 miss 处理——可用性特性不加 fail-closed 面。
    quickdraw_dir: str = ""
    # Curated SVG library (design docs/superpowers/specs/2026-08-26-curated-svg-library-design.md).
    # Empty curated_dir = disabled. curated_replace_qd only takes effect when manifest.ready.
    curated_dir: str = ""
    curated_replace_qd: bool = False
    # 激活码中转（activation_relay）：设备把 OTA 激活码 POST 给 /register，由服务器
    # 持控制台 token 代绑。token/agent_id 任一空 = 中转禁用（端点 503，设备走人工
    # 输码兜底）；api_base 必须是 https 且带主机名。
    xiaozhi_api_base: str = "https://xiaozhi.me/api"
    xiaozhi_console_token: str = ""
    xiaozhi_agent_id: str = ""
    # 设备消息 API token（2026-08-24 P1-3）：生成完成后服务端经官方
    # /messaging/device/tools/call 链式调 hutuji.draw，消灭云端第二步漏调
    # （78/xiaozhi-esp32#1011）。空值 = 禁用，云端两步衔接仍是合法回落。
    xiaozhi_device_token: str = ""


    def __post_init__(self) -> None:
        object.__setattr__(self, "public_base_url", validate_public_base_url(self.public_base_url))
        object.__setattr__(self, "llm_base_url", validate_llm_base_url(self.llm_base_url))
        # 纯空白 key 视同未配置（fail-closed）：不 strip 会让空白 key 过
        # llm_configured 的非空闸，全部请求以 401 假性可用、掩盖部署事故。
        # 与下方 image_fallback_url 的 strip 同口径（MCPDetail-F1）。
        object.__setattr__(self, "llm_api_key", self.llm_api_key.strip())
        # 与 llm_api_key 同口径：纯空白视同未配置，避免空白串被 Path(".") 类
        # 解析成进程 CWD 而把整棵工作目录当数据根扫。
        object.__setattr__(self, "quickdraw_dir", self.quickdraw_dir.strip())
        # 备用端点只允许 https 且必须带主机名（置空=禁用）；旧 startswith 前缀
        # 校验会放过裸 `https://`（无 hostname），与 validate_llm_base_url 的
        # urlsplit 口径对齐（MCPDetail-F8）。回显的是部署方自己的配置值，非凭据。
        fallback = self.image_fallback_url.strip()
        if fallback:
            parsed = urlsplit(fallback)
            if parsed.scheme != "https" or not parsed.hostname:
                raise ValueError(
                    "HUTUJI_IMAGE_FALLBACK_URL 必须是 https:// 且带主机名，或置空禁用，"
                    f"当前 {self.image_fallback_url!r}"
                )
        object.__setattr__(self, "image_fallback_url", fallback)
        sensenova_base_url = self.sensenova_base_url.strip()
        sensenova_api_key = self.sensenova_api_key.strip()
        sensenova_image_model = self.sensenova_image_model.strip()
        sensenova_values = (sensenova_base_url, sensenova_api_key, sensenova_image_model)
        if any(sensenova_values) and not all(sensenova_values):
            missing = []
            if not sensenova_base_url:
                missing.append("HUTUJI_SENSENOVA_BASE_URL")
            if not sensenova_api_key:
                missing.append("HUTUJI_SENSENOVA_API_KEY")
            if not sensenova_image_model:
                missing.append("HUTUJI_SENSENOVA_IMAGE_MODEL")
            raise ValueError("商汤生图配置必须三项齐全，缺少 " + ", ".join(missing))
        if sensenova_base_url:
            sensenova_base_url = validate_llm_base_url(sensenova_base_url)
        object.__setattr__(self, "sensenova_base_url", sensenova_base_url)
        object.__setattr__(self, "sensenova_api_key", sensenova_api_key)
        # 激活码中转：token 与 agent_id 必须同时配置或同时留空（只配一半会让
        # /register 以 503 假性禁用、掩盖部署事故）；api_base 同 image_fallback 口径。
        xiaozhi_api_base = self.xiaozhi_api_base.strip().rstrip("/")
        if xiaozhi_api_base:
            parsed = urlsplit(xiaozhi_api_base)
            if parsed.scheme != "https" or not parsed.hostname:
                raise ValueError(
                    "HUTUJI_XIAOZHI_API_BASE 必须是 https:// 且带主机名，"
                    f"当前 {self.xiaozhi_api_base!r}"
                )
        object.__setattr__(self, "xiaozhi_api_base", xiaozhi_api_base)
        token = self.xiaozhi_console_token.strip()
        agent_id = self.xiaozhi_agent_id.strip()
        if bool(token) != bool(agent_id):
            raise ValueError(
                "HUTUJI_XIAOZHI_TOKEN 与 HUTUJI_XIAOZHI_AGENT_ID 必须同时配置或同时留空"
            )
        object.__setattr__(self, "xiaozhi_console_token", token)
        object.__setattr__(self, "xiaozhi_agent_id", agent_id)
        object.__setattr__(self, "sensenova_image_model", sensenova_image_model)
        # 非 loopback 一律拒绝：锁定决策是「/mcp 只本机可达，公网由 nginx 404」。
        # 生产 unit 用 `--host 127.0.0.1` 钉死且 `-m uvicorn` 不读本字段，所以设成
        # 0.0.0.0 在生产静默无效、在开发直跑 `python -m hutuji_mcp.server` 却是真裸曝。
        # 静默无效比报错危险：它让人误以为监听面已改。两条路径在此统一口径。
        object.__setattr__(self, "siliconflow_api_key", self.siliconflow_api_key.strip())
        if not _is_loopback_host(self.host.lower()):
            raise ValueError(
                f"HUTUJI_HOST 只允许 loopback，当前 {self.host!r}；"
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


def _resolve_output_dir() -> Path:
    """HUTUJI_OUTPUT_DIR：未设置回落 ./output；显式空白 fail-closed 抛错——
    Path("") 会静默解析为进程 CWD，产物与 /files 服务根双双偏离文档默认且难排查
    （与 preflight 对显式空白 env 判红同口径）。"""
    raw = os.environ.get("HUTUJI_OUTPUT_DIR", "./output")
    # 先 strip 再判空/建 Path（MCPDetail-F15）：首尾空白会在文件系统里造出
    # 带空格的目录名，产物与 /files 服务根双双偏离预期且难排查；与
    # llm_api_key / image_fallback_url 的 strip 同口径。
    raw = raw.strip()
    if not raw:
        raise ValueError("HUTUJI_OUTPUT_DIR 不得为空白：显式空值会静默解析为进程 CWD")
    return Path(raw).resolve()


def load_settings() -> Settings:
    """从环境变量读取配置，缺省值面向本机开发。"""
    return Settings(
        llm_base_url=os.environ.get("HUTUJI_LLM_BASE_URL", "https://api.freemodel.dev/v1"),
        llm_api_key=os.environ.get("HUTUJI_LLM_API_KEY", ""),
        llm_model=os.environ.get("HUTUJI_LLM_MODEL", "auto"),
        image_model=os.environ.get("HUTUJI_IMAGE_MODEL", "agnes-image-2.1-flash"),
        output_dir=_resolve_output_dir(),
        public_base_url=os.environ.get("HUTUJI_PUBLIC_BASE_URL", "http://127.0.0.1:8300"),
        # 默认仅本机监听；公网只让 nginx 反代 /files，绝不直曝 /mcp。
        image_fallback_url=os.environ.get(
            "HUTUJI_IMAGE_FALLBACK_URL", "https://imagefree.tingfengai.art/v1/generate"
        ),
        xiaozhi_api_base=os.environ.get("HUTUJI_XIAOZHI_API_BASE", "https://xiaozhi.me/api"),
        xiaozhi_console_token=os.environ.get("HUTUJI_XIAOZHI_TOKEN", ""),
        xiaozhi_agent_id=os.environ.get("HUTUJI_XIAOZHI_AGENT_ID", ""),
        sensenova_base_url=os.environ.get("HUTUJI_SENSENOVA_BASE_URL", ""),
        sensenova_api_key=os.environ.get("HUTUJI_SENSENOVA_API_KEY", ""),
        xiaozhi_device_token=os.environ.get("HUTUJI_XIAOZHI_DEVICE_TOKEN", ""),
        sensenova_image_model=os.environ.get("HUTUJI_SENSENOVA_IMAGE_MODEL", ""),
        siliconflow_api_key=os.environ.get("HUTUJI_SILICONFLOW_API_KEY", ""),
        quickdraw_dir=os.environ.get("HUTUJI_QUICKDRAW_DIR", ""),
        curated_dir=os.environ.get("HUTUJI_CURATED_DIR", ""),
        curated_replace_qd=os.environ.get("HUTUJI_CURATED_REPLACE_QD", "0").strip()
        in ("1", "true", "TRUE", "yes"),
        host=os.environ.get("HUTUJI_HOST", "127.0.0.1"),
        port=_env_int("HUTUJI_PORT", "8300"),
        output_ttl_seconds=_env_int("HUTUJI_OUTPUT_TTL_SECONDS", "600"),
    )


settings = load_settings()
