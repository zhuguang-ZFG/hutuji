#!/bin/bash
# Telegram 运维通知出口：双机统一入口，唯一凭据面 /etc/hutuji/tg.env
# （chmod 600，两行：TG_BOT_TOKEN=… / TG_CHAT_ID=…；凭据禁入库，仓内无副本）。
# 语义：通知是配件不是功能闸——env 缺失/发失败一律静默 exit 0，绝不掀业务链路；
# 消息自动带主机名前缀（双机同发时有归属）。
# 用法: tg-notify.sh <TAG> <TEXT...>   （TAG 用于过滤，如 unit-failed / token-watch）
set -uo pipefail
TAG="${1:-notify}"
shift 2>/dev/null || true
ENVF=/etc/hutuji/tg.env
[[ -r "$ENVF" ]] || exit 0
set -a; . "$ENVF"; set +a
[[ -n "${TG_BOT_TOKEN:-}" && -n "${TG_CHAT_ID:-}" ]] || exit 0
TEXT="[$(hostname -s)] ${TAG}: $*"
# --max-time 10：cron/OnFailure 语境不能悬挂；stderr 全弃（失败不吵 journal）
curl -fsS --max-time 10 -s -o /dev/null \
  "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${TG_CHAT_ID}" \
  --data-urlencode "text=${TEXT}" 2>/dev/null || true
exit 0
