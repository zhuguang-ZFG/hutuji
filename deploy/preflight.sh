#!/usr/bin/env bash
# 上线前/变更后本机冒烟（不打印密钥、不访问公网 MCP）。
set -euo pipefail

PASS=0
FAIL=0
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

safe_output_dir() {
  local value="$1" resolved
  [[ "$value" == /opt/* ]] || return 1
  resolved="$(realpath -m -- "$value" 2>/dev/null)" || return 1
  [[ "$resolved" == /opt/* ]]
}

check_mcp_env() {
  [[ -f /etc/hutuji/mcp.env ]] && ! grep -q REPLACE_ME /etc/hutuji/mcp.env &&
    [[ "$PUBLIC_BASE_URL" =~ ^https://([A-Za-z0-9-]+\.)*[A-Za-z0-9-]+(:443)?/?$ ]] &&
    ! [[ "$PUBLIC_BASE_URL" =~ ^https://(127\.0\.0\.1|localhost|::1)(:443)?/?$ ]] &&
    safe_output_dir "$OUTPUT_DIR"
}

check_active_nginx_acl() {
  local conf site port80 files
  nginx -t >/dev/null 2>&1 || return 1
  conf="$(nginx -T 2>/dev/null)" || return 1
  site="$(awk '/^# configuration file \/etc\/nginx\/sites-enabled\/hutuji:/{inside=1; next} /^# configuration file /{if(inside) exit} inside{print}' <<<"$conf")"
  [[ -n "$site" ]] || return 1
  port80="$(sed -n '/^[[:space:]]*listen 80;[[:space:]]*$/,$p' <<<"$site")"
  files="$(sed -n '/^[[:space:]]*location \/files\/ {/,/^[[:space:]]*}/p' <<<"$port80")"
  [[ -n "$files" ]] && grep -Fq 'allow 127.0.0.1;' <<<"$files" &&
    grep -Fq 'allow ::1;' <<<"$files" && grep -Fq 'deny all;' <<<"$files"
}

# 取本机非 loopback IPv4：依次 hostname -I / 默认路由接口。无公网 IPv4 时返回空，
# 调用方应跳过"非 loopback 拒绝"子判据（loopback 正例已覆盖 ACL 本身），
# 而不是把整项检查判失败。
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
# 非 loopback 拒绝。无公网 IPv4 时跳过 denied 子判据——loopback 正例已覆盖 ACL 本身，
# 不该把环境缺 IPv4 误报成配置缺失。
check_acl_response() {
  local h="$1" get post mcp base ip denied
  [[ -n "$h" ]] || return 1
  get=$(curl --noproxy "*" --max-time 5 -s -o /dev/null -w "%{http_code}" -H "Host: $h" http://127.0.0.1/files/__nonexistent__)
  post=$(curl --noproxy "*" --max-time 5 -s -o /dev/null -w "%{http_code}" -X POST -H "Host: $h" http://127.0.0.1/files/__nonexistent__)
  mcp=$(curl --noproxy "*" --max-time 5 -s -o /dev/null -w "%{http_code}" -H "Host: $h" http://127.0.0.1/mcp)
  base=ok
  # `|| true`：probe 无 IPv4 时 return 1，不让 set -e 在非 if 上下文里腰斩脚本。
  ip="$(probe_non_loopback_ip || true)"
  if [[ -n "$ip" ]]; then
    denied=$(curl --noproxy "*" --max-time 5 -s -o /dev/null -w "%{http_code}" -H "Host: $h" "http://$ip/files/__nonexistent__")
    [[ "$denied" == "403" ]] || base=fail
  else
    # 无证据不宣称：跳过的半边判据必须自己出声，不能让 [pass] 替没验过的部分背书。
    echo "[skip] 非 loopback 拒绝子判据：本机无非 loopback IPv4，本项只验了 loopback 放行"
  fi
  [[ "$get" == "404" && "$post" == "403" && "$mcp" == "404" && "$base" == "ok" ]]
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
if [[ -f /etc/hutuji/mcp.env ]]; then
  env_value /etc/hutuji/mcp.env HUTUJI_PUBLIC_BASE_URL; PUBLIC_BASE_URL="$ENV_VALUE"
  env_value /etc/hutuji/mcp.env HUTUJI_OUTPUT_DIR; OUTPUT_DIR="$ENV_VALUE"
fi
TUNNEL_HOST=""
if [[ "$PUBLIC_BASE_URL" =~ ^https://([A-Za-z0-9-]+\.)*[A-Za-z0-9-]+(:443)?/?$ ]]; then
  TUNNEL_HOST="${PUBLIC_BASE_URL#https://}"
  TUNNEL_HOST="${TUNNEL_HOST%/}"
  TUNNEL_HOST="${TUNNEL_HOST%:443}"
fi

check "mcp.env 权限 600" bash -c '[[ "$(stat -c %a /etc/hutuji/mcp.env 2>/dev/null || echo x)" == "600" ]]'
check "bridge.env 权限 600" bash -c '[[ "$(stat -c %a /etc/hutuji/bridge.env 2>/dev/null || echo x)" == "600" ]]'
check "mcp.env 无占位且公网前缀为 HTTPS:443" check_mcp_env
check "bridge.env 无 REPLACE_ME" bash -c '[[ -f /etc/hutuji/bridge.env ]] && ! grep -q REPLACE_ME /etc/hutuji/bridge.env'
check "8300 仅本机监听" bash -c 'ss -lnt | grep -q "127.0.0.1:8300" || ss -lnt | grep -q "\[::1\]:8300"'
# `ss` 不在时 `! ss | grep` 会因无输出而假绿（第 5 项那条 fail-closed，这条不是），先探二进制。
check "公网未监听 8300" bash -c 'command -v ss >/dev/null && ! ss -lnt | grep -E ":8300" | grep -vqE "127.0.0.1|\[::1\]"'
check "hutuji-mcp active" systemctl is-active --quiet hutuji-mcp
# `check … | grep` 会丢失 [pass] 行并在子 shell 中丢计数；判据必须自包含。
check "本机 /mcp 可达" bash -c 'code=$(curl --noproxy "*" --max-time 5 -s -o /dev/null -w "%{http_code}" -X POST -H "content-type: application/json" -d "{}" http://127.0.0.1:8300/mcp); [[ "$code" =~ ^[2345] ]]'
# /files 无具体 token 时允许 404；只要不是连接失败。
check "本机 /files 路径有响应" bash -c 'code=$(curl --noproxy "*" --max-time 5 -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8300/files/); [[ "$code" =~ ^[2345] ]]'

# ---- nginx 层（应用直连绿≠经 nginx 绿）----
check "nginx -t 通过且 enabled 配置含 loopback-only :80 /files ACL" check_active_nginx_acl
check "sites-enabled/hutuji 指向 sites-available/hutuji" bash -c '
  [[ -L /etc/nginx/sites-enabled/hutuji ]] &&
  [[ "$(readlink -f /etc/nginx/sites-enabled/hutuji)" == "/etc/nginx/sites-available/hutuji" ]]'
check "线上 nginx conf 与本仓模板一致（LF 归一化）" bash -c '
  [[ -f /opt/hutuji/deploy/nginx-hutuji.conf && -f /etc/nginx/sites-enabled/hutuji ]] || exit 1
  a=$(tr -d "\r" < /opt/hutuji/deploy/nginx-hutuji.conf | md5sum | cut -d" " -f1)
  b=$(tr -d "\r" < /etc/nginx/sites-enabled/hutuji | md5sum | cut -d" " -f1)
  [[ -n "$a" && "$a" == "$b" ]]'
check "活动 :80 /files ACL：loopback 放行、非 loopback 拒绝" check_acl_response "$TUNNEL_HOST"

# 真 200 正例：造一份合法命名产物（TTL 内自清），验完整下载契约。
check "localhost :80 /files 真下 G-code 200" check_download

echo "---- preflight pass=${PASS} fail=${FAIL} ----"
if [[ "${FAIL}" -gt 0 ]]; then
  exit 1
fi
echo "PASS — 再验：公网 curl /mcp 须失败；HTTPS /files 经 nginx；小智控制台 tools/list"
