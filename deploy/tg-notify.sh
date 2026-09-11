#!/bin/bash
# Telegram 运维通知出口：双机统一入口，唯一凭据面 /etc/hutuji/tg.env
# （chmod 600，凭据禁入库，仓内无副本）。
# 语义：通知是配件不是功能闸——env 缺失/发失败一律静默 exit 0，绝不掀业务链路；
# 消息自动带主机名前缀（双机同发时有归属）。
#
# 出路二选一（tg.env 决定，运行期探测）：
#   直连（阿里云主节点可直达 api.telegram.org）：TG_RELAY_HOST 不配即直连；
#   SSH 中继（京东云直连被 DNS 污染+封 SNI，2026-09-12 实测 curl 000）：
#   配 TG_RELAY_HOST=47.112.162.80（lima 公网 SSH）与 TG_RELAY_KEY=专用 key，
#   对端 authorized_keys 以 command= 锁死只许跑 tg-relay-recv.sh（stdin 带正文）。
#   tailnet HTTP 8092 与 tailnet SSH 均被 ACL/HIDS 挡死（同日实测），走公网。
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
if [[ -n "${TG_RELAY_HOST:-}" && -n "${TG_RELAY_KEY:-}" ]]; then
  # 中继路径：正文走 stdin，避免长文本撞 argv 限额/引号问题。
  printf '%s' "$FULLTEXT" | ssh -i "$TG_RELAY_KEY" \
    -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 \
    "$TG_RELAY_HOST" >/dev/null 2>&1 || true
  exit 0
fi

if ! curl -fsS --max-time 10 -s -o /dev/null \
  "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${TG_CHAT_ID}" \
  --data-urlencode "text=${FULLTEXT}" 2>/dev/null; then
  # 通知发失败必须留痕（journald），否则「静默=健康」分不清通道死了没。
  logger -t tg-notify -p user.warning "sendMessage 失败（非 2xx/网络），消息: ${FULLTEXT:0:200}"
fi
exit 0
