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


def _validate_draw_queue_url(raw: str) -> str:
    value = raw.strip()
    if not value:
        return ""
    if value == "memory://":
        return value
    parsed = urlsplit(value)
    if parsed.scheme not in ("redis", "rediss") or not parsed.hostname:
        raise ValueError(
            "HUTUJI_DRAW_QUEUE_URL 须为空、memory:// 或 redis(s):// 且带主机名，"
            f"当前 {raw!r}"
        )
    return value


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
    # 异步生成+完成推送（2026-08-28 决策）：默认关闭（同步）；开启且已配
    # HUTUJI_XIAOZHI_DEVICE_TOKEN 时 hutuji_draw 立即返回 accepted 并由
    # _run_draw_job 完成后经 _chain_device_draw 自动推送到设备预览；无 token
    # 时回落同步（fail-open 不丢可用性）。
    draw_async: bool = False
    # 纸面风险闸（2026-08-27）：AI 位图链 G-code 画糊风险统计拒收重画，最多
    # 3 轮；QD/curated 命中不经闸。`HUTUJI_PAPER_RISK_GATE=0` 关闭 enforce
    # （仍记 shadow 日志由 paper_risk.evaluate 提供，见 server 重试环）。
    paper_risk_gate: bool = True
    # 课堂级并发生图闸（单进程 DrawLimiter + vpype；默认 8/4/120/6）。
    # 禁止靠多 uvicorn worker「翻倍」——每进程各有一份内存闸，会静默放大。
    draw_max_concurrent: int = 8
    draw_per_second: int = 4
    draw_per_minute: int = 120
    vpype_max_concurrent: int = 6
    # Redis 绘图队列（空=进程内 asyncio 任务，与现网一致；redis://=MCP 入队+worker 消费）
    draw_queue_url: str = ""
    draw_queue_max_depth: int = 200
    draw_enqueue_per_second: int = 10
    draw_per_device_minute: int = 6
    draw_racer_max: int = 0
    # 产物存储：local=本机 output_dir；oss=阿里云 OSS（多 worker 共享）
    output_backend: str = "local"
    oss_endpoint: str = ""
    oss_bucket: str = ""
    oss_access_key_id: str = ""
    oss_access_key_secret: str = ""
    oss_prefix: str = "draw"
    # Phase-3：Postgres 任务账本（空=禁用）
    pg_dsn: str = ""
    pg_retention_days: int = 30
    # Phase-3：生图通道熔断（3a 默认 shadow：breaker_enforce=0 只记事件不过滤）
    breaker_enforce: bool = False
    breaker_window_sec: int = 60
    breaker_fail_ratio: float = 0.5
    breaker_open_sec: int = 120
    breaker_consecutive_fail: int = 3
    metrics_interval_sec: int = 15
    cdn_enabled: bool = False
    # draw-portal usage 采集（loopback internal API；secret 与 portal 同值）
    usage_ingest_url: str = "http://127.0.0.1:8302/api/internal/usage"
    usage_ingest_secret: str = ""

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
        for name, value, lo, hi in (
            ("draw_max_concurrent", self.draw_max_concurrent, 1, 32),
            ("draw_per_second", self.draw_per_second, 1, 32),
            ("draw_per_minute", self.draw_per_minute, 1, 600),
            ("vpype_max_concurrent", self.vpype_max_concurrent, 1, 16),
            ("draw_queue_max_depth", self.draw_queue_max_depth, 1, 2000),
            ("draw_enqueue_per_second", self.draw_enqueue_per_second, 1, 100),
            ("draw_per_device_minute", self.draw_per_device_minute, 1, 60),
            ("draw_racer_max", self.draw_racer_max, 0, 8),
            ("pg_retention_days", self.pg_retention_days, 1, 365),
            ("breaker_window_sec", self.breaker_window_sec, 10, 600),
            ("breaker_open_sec", self.breaker_open_sec, 30, 3600),
            ("breaker_consecutive_fail", self.breaker_consecutive_fail, 1, 20),
            ("metrics_interval_sec", self.metrics_interval_sec, 5, 300),
        ):
            if not lo <= value <= hi:
                raise ValueError(f"{name} 必须在 {lo}..{hi}，当前 {value}")
        if not 0.0 <= self.breaker_fail_ratio <= 1.0:
            raise ValueError(
                f"breaker_fail_ratio 必须在 0..1，当前 {self.breaker_fail_ratio}"
            )
        object.__setattr__(self, "pg_dsn", self.pg_dsn.strip())
        backend = self.output_backend.strip().lower()
        if backend not in ("local", "oss"):
            raise ValueError(f"output_backend 必须是 local 或 oss，当前 {self.output_backend!r}")
        object.__setattr__(self, "output_backend", backend)
        if backend == "oss":
            missing = [
                name
                for name, val in (
                    ("HUTUJI_OSS_ENDPOINT", self.oss_endpoint),
                    ("HUTUJI_OSS_BUCKET", self.oss_bucket),
                    ("HUTUJI_OSS_ACCESS_KEY_ID", self.oss_access_key_id),
                    ("HUTUJI_OSS_ACCESS_KEY_SECRET", self.oss_access_key_secret),
                )
                if not str(val).strip()
            ]
            if missing:
                raise ValueError("OSS 产物后端必须配置 " + ", ".join(missing))
        object.__setattr__(self, "oss_prefix", self.oss_prefix.strip().strip("/"))
        object.__setattr__(self, "oss_access_key_id", self.oss_access_key_id.strip())
        object.__setattr__(self, "oss_access_key_secret", self.oss_access_key_secret.strip())
        object.__setattr__(
            self,
            "draw_queue_url",
            _validate_draw_queue_url(self.draw_queue_url),
        )

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key)


def _env_int(name: str, default: str) -> int:
    raw = os.environ.get(name, default)
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是整数，当前 {raw!r}") from exc


def _env_float(name: str, default: str) -> float:
    raw = os.environ.get(name, default)
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是浮点数，当前 {raw!r}") from exc


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
        draw_async=os.environ.get("HUTUJI_DRAW_ASYNC", "0").strip()
        in ("1", "true", "TRUE", "yes"),
        paper_risk_gate=os.environ.get("HUTUJI_PAPER_RISK_GATE", "1").strip()
        not in ("0", "false", "FALSE", "no"),
        host=os.environ.get("HUTUJI_HOST", "127.0.0.1"),
        port=_env_int("HUTUJI_PORT", "8300"),
        output_ttl_seconds=_env_int("HUTUJI_OUTPUT_TTL_SECONDS", "600"),
        draw_max_concurrent=_env_int("HUTUJI_DRAW_MAX_CONCURRENT", "8"),
        draw_per_second=_env_int("HUTUJI_DRAW_PER_SECOND", "4"),
        draw_per_minute=_env_int("HUTUJI_DRAW_PER_MINUTE", "120"),
        vpype_max_concurrent=_env_int("HUTUJI_VPYPE_MAX_CONCURRENT", "6"),
        draw_queue_url=os.environ.get("HUTUJI_DRAW_QUEUE_URL", ""),
        draw_queue_max_depth=_env_int("HUTUJI_DRAW_QUEUE_MAX_DEPTH", "200"),
        draw_enqueue_per_second=_env_int("HUTUJI_DRAW_ENQUEUE_PER_SECOND", "10"),
        draw_per_device_minute=_env_int("HUTUJI_DRAW_PER_DEVICE_MINUTE", "6"),
        draw_racer_max=_env_int("HUTUJI_DRAW_RACER_MAX", "0"),
        output_backend=os.environ.get("HUTUJI_OUTPUT_BACKEND", "local"),
        oss_endpoint=os.environ.get("HUTUJI_OSS_ENDPOINT", ""),
        oss_bucket=os.environ.get("HUTUJI_OSS_BUCKET", ""),
        oss_access_key_id=os.environ.get("HUTUJI_OSS_ACCESS_KEY_ID", ""),
        oss_access_key_secret=os.environ.get("HUTUJI_OSS_ACCESS_KEY_SECRET", ""),
        oss_prefix=os.environ.get("HUTUJI_OSS_PREFIX", "draw"),
        pg_dsn=os.environ.get("HUTUJI_PG_DSN", ""),
        pg_retention_days=_env_int("HUTUJI_PG_RETENTION_DAYS", "30"),
        breaker_enforce=os.environ.get("HUTUJI_BREAKER_ENFORCE", "0").strip()
        in ("1", "true", "TRUE", "yes"),
        breaker_window_sec=_env_int("HUTUJI_BREAKER_WINDOW_SEC", "60"),
        breaker_fail_ratio=_env_float("HUTUJI_BREAKER_FAIL_RATIO", "0.5"),
        breaker_open_sec=_env_int("HUTUJI_BREAKER_OPEN_SEC", "120"),
        breaker_consecutive_fail=_env_int("HUTUJI_BREAKER_CONSECUTIVE_FAIL", "3"),
        metrics_interval_sec=_env_int("HUTUJI_METRICS_INTERVAL_SEC", "15"),
        cdn_enabled=os.environ.get("HUTUJI_CDN_ENABLED", "0").strip()
        in ("1", "true", "TRUE", "yes"),
        usage_ingest_url=os.environ.get(
            "HUTUJI_USAGE_INGEST_URL", "http://127.0.0.1:8302/api/internal/usage"
        ),
        usage_ingest_secret=os.environ.get("HUTUJI_USAGE_INGEST_SECRET", "").strip(),
    )


settings = load_settings()
