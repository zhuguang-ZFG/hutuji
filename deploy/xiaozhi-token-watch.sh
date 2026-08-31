#!/bin/bash
# 小智控制台 JWT 看门狗（cron 每 10 分钟）：用 /etc/hutuji/mcp.env 里的 token
# 调 GET /agents，401/403 即判作废并 logger 点名（journald）。
# 自动换 token（2026-08-31 起）：检出作废先试 `xiaozhi-token-auto.py`（OCR
# 过图形验证码走 /auth/login；2026-08-20 前端证据 POST /auth/login 需
# captcha_code、account-settings 无令牌管理，今天仍成立，但验证码实测为
# svg-captcha 类 SVG 文本图，可 OCR）。自动缺装/失败时回落原有报警+人工路径。
set -uo pipefail

ENV_FILE=/etc/hutuji/mcp.env
STATE_FILE=/run/hutuji-token-watch.state
API_DEFAULT=https://xiaozhi.me/api

[[ -r "$ENV_FILE" ]] || exit 0
get_env() { grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- | tr -d "\"'"; }
TOKEN=$(get_env HUTUJI_XIAOZHI_TOKEN)
API=$(get_env HUTUJI_XIAOZHI_API_BASE); API=${API:-$API_DEFAULT}

# 未启用中转（无 token）则无事可看，静默退出。
[[ -n "$TOKEN" ]] || exit 0

code=$(curl --noproxy "*" --max-time 10 -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer $TOKEN" "$API/agents?page=1&pageSize=1")

now=$(date +%s)
last_state=$(cat "$STATE_FILE" 2>/dev/null || echo "unknown")

if [[ "$code" == "200" ]]; then
  if [[ "$last_state" == "invalid" ]]; then
    logger -t hutuji-token-watch -p user.info "小智控制台 JWT 已恢复有效"
  fi
  echo "ok" > "$STATE_FILE"
  exit 0
fi

if [[ "$code" == "401" || "$code" == "403" ]]; then
  minute=$(date +%M)
  # 自动续期（2026-08-31）：仅状态翻转或每小时整点窗口尝试一次，flock 防
  # cron 重叠。凭据在 /etc/hutuji/xiaozhi-login.env（600）；未配/失败不影响
  # 下方人工报警路径。auto 成功即 refresh 脚本已写回+重启+探针。
  AUTO=/opt/hutuji/deploy/xiaozhi-token-auto.py
  AUTO_PY=/opt/hutuji/token-auto-venv/bin/python
  if [[ -f "$AUTO" && -x "$AUTO_PY" && -r /etc/hutuji/xiaozhi-login.env ]] \
     && [[ "$last_state" != "invalid" || "$minute" -lt 10 ]]; then
    if flock -n /run/hutuji-token-auto.lock "$AUTO_PY" "$AUTO" >/dev/null 2>&1; then
      logger -t hutuji-token-watch -p user.info "小智控制台 JWT 已自动续期（OCR 登录成功并写回）"
      echo "ok" > "$STATE_FILE"
      exit 0
    fi
    logger -t hutuji-token-watch -p user.warning "小智控制台 JWT 自动续期失败，回落人工路径"
  fi
  # 翻转时必报；持续失效只在整点（分钟<10，cron 10 分钟一拍即每小时一次）续报。
  if [[ "$last_state" != "invalid" || "$minute" -lt 10 ]]; then
    logger -t hutuji-token-watch -p user.err \
      "小智控制台 JWT 已失效（HTTP $code）：/register 无感激活与 KB 同步将失败。" \
      "请登录 xiaozhi.me/console 浏览器控制台 localStorage.getItem('token') 取新值，" \
      "然后运行 /opt/hutuji/deploy/refresh-xiaozhi-token.sh '<token>'"
  fi
  echo "invalid" > "$STATE_FILE"
  exit 1
fi

# 网络错误/5xx 不是作废证据（token 可能仍有效），仅 warning 且不翻转状态。
logger -t hutuji-token-watch -p user.warning "小智控制台探测异常 HTTP $code（非作废证据，不翻转状态）"
exit 0
