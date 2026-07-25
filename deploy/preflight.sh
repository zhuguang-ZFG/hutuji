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
check "本机 /mcp 可达" curl -sf -o /dev/null -w "%{http_code}" http://127.0.0.1:8300/mcp | grep -qE '^[2345]'
# /files 无具体 token 时允许 404；只要不是连接失败
check "本机 /files 路径有响应" bash -c 'code=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8300/files/); [[ "$code" =~ ^[2345] ]]'

echo "---- preflight pass=${PASS} fail=${FAIL} ----"
if [[ "${FAIL}" -gt 0 ]]; then
  exit 1
fi
echo "PASS — 再验：公网 curl /mcp 须失败；HTTPS /files 经 nginx；小智控制台 tools/list"
