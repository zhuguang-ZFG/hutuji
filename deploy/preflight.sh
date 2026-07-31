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

check "mcp.env 权限 600" bash -c '[[ "$(stat -c %a /etc/hutuji/mcp.env 2>/dev/null || echo x)" == "600" ]]'
check "bridge.env 权限 600" bash -c '[[ "$(stat -c %a /etc/hutuji/bridge.env 2>/dev/null || echo x)" == "600" ]]'
check "mcp.env 无 REPLACE_ME" bash -c '! grep -q REPLACE_ME /etc/hutuji/mcp.env'
check "bridge.env 无 REPLACE_ME" bash -c '! grep -q REPLACE_ME /etc/hutuji/bridge.env'
check "8300 仅本机监听" bash -c 'ss -lnt | grep -q "127.0.0.1:8300" || ss -lnt | grep -q "\[::1\]:8300"'
check "公网未监听 8300" bash -c '! ss -lnt | grep -E ":8300" | grep -vqE "127.0.0.1|\[::1\]"'
check "hutuji-mcp active" systemctl is-active --quiet hutuji-mcp
# 注意：`check name cmd | grep` 会把整个 check 塞进管道左侧——[pass] 行被 grep 吞掉、
# 计数在子 shell 里丢失（曾导致本脚本只打印 8 项）。判据必须自包含在 bash -c 内。
check "本机 /mcp 可达" bash -c 'code=$(curl -s -o /dev/null -w "%{http_code}" -X POST -H "content-type: application/json" -d "{}" http://127.0.0.1:8300/mcp); [[ "$code" =~ ^[2345] ]]'
# /files 无具体 token 时允许 404；只要不是连接失败
check "本机 /files 路径有响应" bash -c 'code=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8300/files/); [[ "$code" =~ ^[2345] ]]'

# ---- nginx 层（应用直连绿≠经 nginx 绿）----
# 2026-07-31 实测教训：本脚本原先只打 127.0.0.1:8300，于是三件事全在盲区——
#   ① sites-enabled/hutuji 曾是普通文件而非符号链接（改 sites-available 静默无效）；
#   ② :80 的 server_name 若不含隧道域名，Host=域名 会掉到 default server，/files 反代失效；
#   ③ 经 nginx 的正文是否真能流回来（只看状态码不看 body 就漏 proxy_pass 断流）。
# 隧道 Host 从 mcp.env 推导，不写死。
TUNNEL_HOST="$(sed -n 's|^HUTUJI_PUBLIC_BASE_URL=https\?://||p' /etc/hutuji/mcp.env 2>/dev/null | tr -d '/' | head -1)"
OUTPUT_DIR="$(sed -n 's|^HUTUJI_OUTPUT_DIR=||p' /etc/hutuji/mcp.env 2>/dev/null | head -1)"
: "${TUNNEL_HOST:=}" "${OUTPUT_DIR:=}"

check "nginx -t 通过" bash -c 'nginx -t >/dev/null 2>&1'
check "sites-enabled/hutuji 是符号链接" bash -c '[[ -L /etc/nginx/sites-enabled/hutuji ]]'
check "线上 nginx conf 与本仓模板一致（LF 归一化）" bash -c '
  a=$(tr -d "\r" < /opt/hutuji/deploy/nginx-hutuji.conf | md5sum | cut -d" " -f1)
  b=$(md5sum /etc/nginx/sites-available/hutuji | cut -d" " -f1)
  [[ -n "$a" && "$a" == "$b" ]]'
check "隧道 Host 经 :80 命中 /files 反代（非 301 自指）" bash -c '
  h="'"${TUNNEL_HOST}"'"; [[ -n "$h" ]] || exit 1
  get=$(curl -s -o /dev/null -w "%{http_code}" -H "Host: $h" http://127.0.0.1/files/__nonexistent__)
  post=$(curl -s -o /dev/null -w "%{http_code}" -X POST -H "Host: $h" http://127.0.0.1/files/__nonexistent__)
  mcp=$(curl -s -o /dev/null -w "%{http_code}" -H "Host: $h" http://127.0.0.1/mcp)
  [[ "$get" == "404" && "$post" == "403" && "$mcp" == "404" ]]'
# 真 200 正例：造一份合法命名产物（TTL 内自清，本检查也自删），验状态码 + 字节数 + 正文
check "隧道 Host 经 :80 真下 G-code 200" bash -c '
  h="'"${TUNNEL_HOST}"'"; d="'"${OUTPUT_DIR}"'"
  [[ -n "$h" && -d "$d" ]] || exit 1
  tok=$(head -c 24 /dev/urandom | base64 | tr "+/" "-_" | tr -d "=" | head -c 22)
  f="draw_$(date +%Y%m%d_%H%M%S)_${tok}.gcode"
  trap "rm -f \"$d/$f\"" EXIT
  printf "G21\nG90\nF8000\n" > "$d/$f"
  body=$(curl -s -H "Host: $h" "http://127.0.0.1/files/$f")
  [[ "$body" == "$(printf "G21\nG90\nF8000")" ]]'

echo "---- preflight pass=${PASS} fail=${FAIL} ----"
if [[ "${FAIL}" -gt 0 ]]; then
  exit 1
fi
echo "PASS — 再验：公网 curl /mcp 须失败；HTTPS /files 经 nginx；小智控制台 tools/list"
