#!/usr/bin/env bash
# 上线前/变更后本机冒烟（不打印密钥、不访问公网 MCP）。
set -euo pipefail

EXPECTED_CHECKS=15
PASS=0
FAIL=0

# 只有 pass / fail 两种状态，没有 skip。
# 历史上有过 OPTIONAL_SKIP(125) 机制：判据取不到前提条件时返回它，计为 skip。
# 但门禁算术是 (PASS+SKIP)==EXPECTED_CHECKS，skip 与 pass 在放行上完全等价——
# 于是「公网访问 :80 /files 被拒」这条铁律可以从未被验证过，脚本却打印 15/15 全绿
# （已实测：pass=14 fail=0 skip=1 照样输出 PASS）。任何「这项没法验」都必须是红，
# 由人确认，不得由脚本自行豁免。请勿重新引入 skip 状态。
check() {
  local name="$1"; shift
  if "$@"; then
    echo "[pass] ${name}"
    PASS=$((PASS + 1))
  else
    echo "[fail] ${name}"
    FAIL=$((FAIL + 1))
  fi
}

# 只按 systemd EnvironmentFile 的简单 KEY=VALUE 语法读取目标键：不 source、不 eval，
# 重复键按 systemd 的最后一次赋值生效；引号只去除成对的首尾单/双引号。
env_value() {
  local file="$1" key="$2" line name value
  ENV_VALUE=""
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line#${line%%[![:space:]]*}}"
    [[ -z "$line" || "${line:0:1}" == "#" || "${line:0:1}" == ";" ]] && continue
    [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=[[:space:]]*(.*)$ ]] || continue
    name="${BASH_REMATCH[1]}"
    [[ "$name" == "$key" ]] || continue
    value="${BASH_REMATCH[2]}"
    while [[ "$value" == *[[:space:]] ]]; do value="${value%?}"; done
    if [[ "${value:0:1}" == '"' && "${value: -1}" == '"' && ${#value} -ge 2 ]]; then
      value="${value:1:${#value}-2}"
    elif [[ "${value:0:1}" == "'" && "${value: -1}" == "'" && ${#value} -ge 2 ]]; then
      value="${value:1:${#value}-2}"
    fi
    ENV_VALUE="$value"
  done < "$file"
}

check_no_placeholder() {
  local file="$1" rc
  [[ -r "$file" ]] || return 1
  if grep -q REPLACE_ME "$file"; then
    return 1
  else
    rc=$?
    [[ "$rc" -eq 1 ]]
  fi
}

check_public_base_url() {
  local value="${1,,}" host
  [[ "$value" =~ ^https://([a-z0-9-]+\.)*[a-z0-9-]+(:443)?/?$ ]] || return 1
  host="${value#https://}"; host="${host%/}"; host="${host%:443}"
  # loopback 与私网都不是公网可达前缀：S3 拿到它下载必失败。旧版只排除
  # localhost / 127.*，于是 https://192.168.1.5、https://10.0.0.8、https://intranet
  # 全部报 pass（已实测），属「门禁绿但功能坏」。
  case "$host" in
    localhost|127.*|0.0.0.0) return 1 ;;
    10.*|192.168.*|169.254.*) return 1 ;;
    172.1[6-9].*|172.2[0-9].*|172.3[01].*) return 1 ;;
  esac
  # 单标签主机名（无点）不可能是公网 DNS 名，如 https://intranet。
  [[ "$host" == *.* ]]
}

safe_output_dir() {
  local value="$1" resolved
  [[ "$value" == /opt/* ]] || return 1
  resolved="$(realpath -m -- "$value" 2>/dev/null)" || return 1
  [[ "$resolved" == /opt/* ]]
}

check_mcp_env() {
  [[ -f /etc/hutuji/mcp.env ]] && check_no_placeholder /etc/hutuji/mcp.env &&
    [[ -n "$LLM_BASE_URL" && -n "$LLM_API_KEY" && -n "$LLM_MODEL" ]] &&
    check_public_base_url "$PUBLIC_BASE_URL" &&
    safe_output_dir "$OUTPUT_DIR"
}

check_bridge_ready() {
  local pid ready_pid
  systemctl is-active --quiet hutuji-cloud-bridge || return 1
  pid="$(systemctl show --property=MainPID --value hutuji-cloud-bridge)"
  [[ "$pid" =~ ^[1-9][0-9]*$ && -r /run/hutuji/cloud-bridge.ready ]] || return 1
  ready_pid="$(tr -d '[:space:]' < /run/hutuji/cloud-bridge.ready)"
  [[ "$ready_pid" == "$pid" ]]
}

check_bridge_env() {
  [[ -f /etc/hutuji/bridge.env ]] && check_no_placeholder /etc/hutuji/bridge.env || return 1
  # endpoint 常带 token，走 stdin 避免完整 URL 出现在进程命令行。
  python3 -c '
import sys
from urllib.parse import urlsplit

raw = sys.stdin.read()
value = raw[:-1] if raw.endswith("\n") else raw
try:
    parsed = urlsplit(value)
    parsed.port
except ValueError:
    raise SystemExit(1)
valid = (
    parsed.scheme == "wss"
    and bool(parsed.hostname)
    and parsed.username is None
    and parsed.password is None
    and not parsed.fragment
    and not any(ch.isspace() for ch in value)
)
raise SystemExit(0 if valid else 1)
' <<<"$BRIDGE_ENDPOINT"
}

check_active_nginx_acl() {
  local conf site port80 files acl deny_line allow_v4_line allow_v6_line
  nginx -t >/dev/null 2>&1 || return 1
  conf="$(nginx -T 2>/dev/null)" || return 1
  site="$(awk '/^# configuration file \/etc\/nginx\/sites-enabled\/hutuji:/{inside=1; next} /^# configuration file /{if(inside) exit} inside{print}' <<<"$conf")"
  [[ -n "$site" ]] || return 1
  port80="$(sed -n '/^[[:space:]]*listen 80;[[:space:]]*$/,$p' <<<"$site")"
  files="$(sed -n '/^[[:space:]]*location \/files\/ {/,/^[[:space:]]*}/p' <<<"$port80")"
  [[ -n "$files" ]] || return 1
  # `deny all;` 在本块内出现两次：ACL 的兜底 deny，与 `limit_except … { deny all; }`
  # 行内那条。`grep -Fq 'deny all;'` 分不清二者——删掉兜底 deny 后 allow/deny 链
  # 只剩两条 allow、默认放行，`:80 /files` 对全公网可读，而 grep 仍命中 limit_except
  # 那行报 pass（已用本函数原样管道实测复现）。故先剔除 limit_except 行，只在纯
  # ACL 语句里找独立成行的兜底 deny。
  acl="$(grep -v 'limit_except' <<<"$files")"
  deny_line="$(grep -n '^[[:space:]]*deny all;[[:space:]]*$' <<<"$acl" | head -1 | cut -d: -f1 || true)"
  allow_v4_line="$(grep -n '^[[:space:]]*allow 127\.0\.0\.1;[[:space:]]*$' <<<"$acl" | head -1 | cut -d: -f1 || true)"
  allow_v6_line="$(grep -n '^[[:space:]]*allow ::1;[[:space:]]*$' <<<"$acl" | head -1 | cut -d: -f1 || true)"
  [[ -n "$deny_line" && -n "$allow_v4_line" && -n "$allow_v6_line" ]] || return 1
  # nginx allow/deny 按出现顺序匹配：兜底 deny 必须排在两条 allow 之后，否则
  # cloudflared 的 loopback 回源会被先拒，公网经隧道下载全挂。
  [[ "$allow_v4_line" -lt "$deny_line" && "$allow_v6_line" -lt "$deny_line" ]]
}

# 取本机非 loopback IPv4：依次 hostname -I / 默认路由接口。取不到即 fail（见
# check_acl_response 注释）：拿不到地址意味着「公网访问 :80 /files 被拒」这条铁律
# 无法验证，不得当作已验证。
probe_non_loopback_ip() {
  local ip
  ip="$(hostname -I 2>/dev/null | tr " " "\n" |
    awk '/^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ && $0 !~ /^127\./ {print; exit}')"
  if [[ -n "$ip" ]]; then
    printf '%s' "$ip"
    return 0
  fi
  # 按 dev 关键字定位接口名，不依赖字段序号：无网关的 `default dev ppp0 scope link`
  # 用 $5 会取到 "link"。
  ip="$(ip -4 route show default 2>/dev/null |
    awk '{for (i = 1; i <= NF; i++) if ($i == "dev") {print $(i + 1); exit}}')"
  if [[ -n "$ip" ]]; then
    ip="$(ip -4 -o addr show dev "$ip" 2>/dev/null |
      awk '{split($4,a,"/"); if (a[1] !~ /^127\./) {print a[1]; exit}}')"
    if [[ -n "$ip" ]]; then
      printf '%s' "$ip"
      return 0
    fi
  fi
  return 1
}

# 用隧道 Host 头探 :80 /files 的 ACL：loopback 放行（GET 404 / POST 403 / /mcp 404），
# 非 loopback 拒绝。
#
# 非 loopback 探针是全脚本唯一真正验证「公网访问 :80 /files 被拒」的断言——
# check_active_nginx_acl 只看配置文本，配置对了不代表运行时行为对。取不到非
# loopback IPv4 时**必须 fail**，不能 skip：旧版返回 OPTIONAL_SKIP，而门禁算术
# 是 (PASS+SKIP)==EXPECTED，skip 与 pass 等价，于是这条铁律可以从未被验证过却
# 报 15/15 全绿。宁可在无公网 IPv4 的机器上显式红、由运维确认，也不要静默放过。
check_acl_response() {
  local h="$1" get post mcp ip denied
  [[ -n "$h" ]] || return 1
  get=$(curl --noproxy "*" --max-time 5 -s -o /dev/null -w "%{http_code}" -H "Host: $h" http://127.0.0.1/files/__nonexistent__)
  post=$(curl --noproxy "*" --max-time 5 -s -o /dev/null -w "%{http_code}" -X POST -H "Host: $h" http://127.0.0.1/files/__nonexistent__)
  mcp=$(curl --noproxy "*" --max-time 5 -s -o /dev/null -w "%{http_code}" -H "Host: $h" http://127.0.0.1/mcp)
  [[ "$get" == "404" && "$post" == "403" && "$mcp" == "404" ]] || return 1
  ip="$(probe_non_loopback_ip || true)"
  if [[ -z "$ip" ]]; then
    echo "取不到非 loopback IPv4：无法验证公网 :80 /files 被拒，判 fail（不得跳过）" >&2
    return 1
  fi
  denied=$(curl --noproxy "*" --max-time 5 -s -o /dev/null -w "%{http_code}" -H "Host: $h" "http://$ip/files/__nonexistent__")
  [[ "$denied" == "403" ]]
}

check_download() (
  local h="$TUNNEL_HOST" d="$OUTPUT_DIR" tok f headers body code expected_len actual_len expected_crc actual_crc
  [[ -n "$h" && -d "$d" ]] && safe_output_dir "$d" || exit 1
  tok="$(python3 -c 'import secrets; print(secrets.token_urlsafe(16))')"
  f="draw_$(date +%Y%m%d_%H%M%S)_${tok}.gcode"; headers="$(mktemp)"; body="$(mktemp)"
  trap 'rm -f -- "$d/$f" "$headers" "$body"' EXIT
  printf "G21\nG90\nF8000\n" > "$d/$f"
  code="$(curl --noproxy "*" --max-time 5 -sS -D "$headers" -o "$body" -w "%{http_code}" -H "Host: $h" "http://127.0.0.1/files/$f")" || exit 1
  expected_len="$(wc -c < "$d/$f" | tr -d " ")"
  actual_len="$(awk 'tolower($1) == "content-length:" {gsub("\r", "", $2); print $2}' "$headers" | tail -1)"
  expected_crc="$(python3 -c 'import sys, zlib; print("%08x" % (zlib.crc32(open(sys.argv[1], "rb").read()) & 0xffffffff))' "$d/$f")"
  actual_crc="$(awk 'tolower($1) == "x-hutuji-crc32:" {gsub("\r", "", $2); print tolower($2)}' "$headers" | tail -1)"
  [[ "$code" == "200" && "$actual_len" == "$expected_len" && "$actual_crc" == "$expected_crc" ]] && cmp -s "$d/$f" "$body"
)

PUBLIC_BASE_URL=""
OUTPUT_DIR=""
LLM_BASE_URL=""
LLM_API_KEY=""
LLM_MODEL=""
if [[ -f /etc/hutuji/mcp.env ]]; then
  env_value /etc/hutuji/mcp.env HUTUJI_PUBLIC_BASE_URL; PUBLIC_BASE_URL="$ENV_VALUE"
  env_value /etc/hutuji/mcp.env HUTUJI_OUTPUT_DIR; OUTPUT_DIR="$ENV_VALUE"
  env_value /etc/hutuji/mcp.env HUTUJI_LLM_BASE_URL; LLM_BASE_URL="$ENV_VALUE"
  env_value /etc/hutuji/mcp.env HUTUJI_LLM_API_KEY; LLM_API_KEY="$ENV_VALUE"
  env_value /etc/hutuji/mcp.env HUTUJI_LLM_MODEL; LLM_MODEL="$ENV_VALUE"
fi
BRIDGE_ENDPOINT=""
if [[ -f /etc/hutuji/bridge.env ]]; then
  env_value /etc/hutuji/bridge.env MCP_ENDPOINT; BRIDGE_ENDPOINT="$ENV_VALUE"
fi
TUNNEL_HOST=""
if [[ "$PUBLIC_BASE_URL" =~ ^https://([A-Za-z0-9-]+\.)*[A-Za-z0-9-]+(:443)?/?$ ]]; then
  TUNNEL_HOST="${PUBLIC_BASE_URL#https://}"
  TUNNEL_HOST="${TUNNEL_HOST%/}"
  TUNNEL_HOST="${TUNNEL_HOST%:443}"
fi

check "mcp.env 权限 600" bash -c '[[ "$(stat -c %a /etc/hutuji/mcp.env 2>/dev/null || echo x)" == "600" ]]'
check "bridge.env 权限 600" bash -c '[[ "$(stat -c %a /etc/hutuji/bridge.env 2>/dev/null || echo x)" == "600" ]]'
check "mcp.env 无占位、LLM 完整且公网前缀为 HTTPS:443" check_mcp_env
check "bridge.env 无占位且 MCP_ENDPOINT 为 wss://" check_bridge_env
check "8300 仅本机监听" bash -c 'ss -lnt | grep -q "127.0.0.1:8300" || ss -lnt | grep -q "\[::1\]:8300"'
# `ss` 不在时 `! ss | grep` 会因无输出而假绿（第 5 项那条 fail-closed，这条不是），先探二进制。
check "公网未监听 8300" bash -c 'command -v ss >/dev/null && ! ss -lnt | grep -E ":8300" | grep -vqE "127.0.0.1|\[::1\]"'
check "hutuji-mcp active" systemctl is-active --quiet hutuji-mcp
check "hutuji-cloud-bridge active 且当前 WSS 已连接" check_bridge_ready
# `check … | grep` 会丢失 [pass] 行并在子 shell 中丢计数；判据必须自包含。
# 404 必须排除：MCP 子应用挂载失败（server.py 的 app.mount("/", _mcp_app) 未生效）时
# 裸 POST /mcp 由 FastAPI 返回 404，落在 ^[234] 内会把「端点已不存在」报成 pass。
# 合法响应是 2xx/3xx，或 FastMCP 对不合规 body 的 400/406/415；5xx 与 404 都不算。
check "本机 /mcp 可达" bash -c 'code=$(curl --noproxy "*" --max-time 5 -s -o /dev/null -w "%{http_code}" -X POST -H "content-type: application/json" -d "{}" http://127.0.0.1:8300/mcp); [[ "$code" =~ ^[23] || "$code" == "400" || "$code" == "406" || "$code" == "415" ]]'
# /files 无具体 token 时允许 404（4xx 在列）；连接失败（000）与 5xx 都不得报 pass。
check "本机 /files 路径有响应" bash -c 'code=$(curl --noproxy "*" --max-time 5 -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8300/files/); [[ "$code" =~ ^[234] ]]'

# ---- nginx 层（应用直连绿≠经 nginx 绿）----
check "nginx -t 通过且 enabled 配置含 loopback-only :80 /files ACL" check_active_nginx_acl
check "sites-enabled/hutuji 指向 sites-available/hutuji" bash -c '
  [[ -L /etc/nginx/sites-enabled/hutuji ]] &&
  [[ "$(readlink -f /etc/nginx/sites-enabled/hutuji)" == "/etc/nginx/sites-available/hutuji" ]]'
check "运行中 nginx conf 与固定部署模板一致（LF 归一化）" bash -c '
  [[ -f /opt/hutuji/deploy/nginx-hutuji.conf && -f /etc/nginx/sites-enabled/hutuji ]] || exit 1
  a=$(tr -d "\r" < /opt/hutuji/deploy/nginx-hutuji.conf | md5sum | cut -d" " -f1)
  b=$(tr -d "\r" < /etc/nginx/sites-enabled/hutuji | md5sum | cut -d" " -f1)
  [[ -n "$a" && "$a" == "$b" ]]'
check "活动 :80 /files ACL：loopback 放行、非 loopback 拒绝" check_acl_response "$TUNNEL_HOST"

# 真 200 正例：造一份合法命名产物（TTL 内自清），验完整下载契约。
check "localhost :80 /files 真下 G-code 200" check_download

echo "---- preflight pass=${PASS} fail=${FAIL} expected=${EXPECTED_CHECKS} ----"
# 必须全 pass：无 skip 可抵扣，少一项都不放行（漏跑与失败同等对待）。
if ! [[ "${PASS}" -eq "${EXPECTED_CHECKS}" && "${FAIL}" -eq 0 ]]; then
  exit 1
fi
echo "PASS — 再验：公网 curl /mcp 须失败；HTTPS /files 经 nginx；小智控制台 tools/list"
