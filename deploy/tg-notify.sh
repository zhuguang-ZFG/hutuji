#!/bin/bash
# Telegram 运维通知出口：双机统一入口，唯一凭据面 /etc/hutuji/tg.env
# （chmod 600，凭据禁入库，仓内无副本）。
# 语义：通知是配件不是功能闸——env 缺失/发失败一律静默 exit 0，绝不掀业务链路；
# 消息自动带主机名前缀（双机同发时有归属）。
#
# 出路二选一（tg.env 决定，运行期探测）：
#   直连（阿里云主节点可直达 api.telegram.org）：TG_RELAY_HOST 不配即直连；
#   HTTP 中继（京东云被 DNS 污染+封 SNI 且 SSH over tailnet 握手挂起，
#   2026-09-12 实测）：配 TG_RELAY_HOST=100.103.82.78（lima tailnet IP），
#   可选 TG_RELAY_PORT=8092——对端只跑 tailnet 内 hutuji-tg-relay 服务。
#
# 用法: tg-notify.sh <TAG> <TEXT...>      普通调用（TAG 如 unit-failed/token-watch）
#       tg-notify.sh --stdin             中继接收端：正文读 stdin（≤4KB）
set -uo pipefail
if [[ "${1:-}" == "--stdin" ]]; then
  TAG=relay
  TEXT=$(head -c 4000)
else
  TAG="${1:-notify}"
  shift 2>/dev/null || true
  TEXT="$*"
fi
ENVF=/etc/hutuji/tg.env
[[ -r "$ENVF" ]] || exit 0
set -a; . "$ENVF"; set +a
[[ -n "${TG_BOT_TOKEN:-}" && -n "${TG_CHAT_ID:-}" ]] || exit 0
FULLTEXT="[$(hostname -s)] ${TAG}: ${TEXT}"

if [[ -n "${TG_RELAY_HOST:-}" ]]; then
  # 中继路径：正文走 HTTP body 到 tailnet 内 hutuji-tg-relay（wireguard 加密）。
  printf '%s' "$FULLTEXT" | curl -fsS --max-time 10 -s -o /dev/null \
    --data-binary @- "http://${TG_RELAY_HOST}:${TG_RELAY_PORT:-8092}/" || true
  exit 0
fi

curl -fsS --max-time 10 -s -o /dev/null \
  "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${TG_CHAT_ID}" \
  --data-urlencode "text=${FULLTEXT}" 2>/dev/null || true
exit 0
