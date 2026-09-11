#!/bin/bash
# Telegram 运维通知出口：双机统一入口，唯一凭据面 /etc/hutuji/tg.env
# （chmod 600，凭据禁入库，仓内无副本）。
# 语义：通知是配件不是功能闸——env 缺失/发失败一律静默 exit 0，绝不掀业务链路；
# 消息自动带主机名前缀（双机同发时有归属）。
#
# 出路二选一（tg.env 决定，运行期探测）：
#   直连（lima 配 TG_PROXY 经 sing-box 出网）；SSH 中继（京东云，见下）：
#   对端 authorized_keys 以 command= 锁死只许跑 tg-relay-recv.sh（stdin 带正文）。
# 直连（或中继落地侧）tg.env 可配 TG_PROXY=http://127.0.0.1:7890（lima sing-box
#   本机 HTTP 代理）：大陆节点直连 api.telegram.org 2026-09-12 实测全超时，必须走代理。
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
  # jd→lima SSH 偶发 KEX 挂起（jd HIDS 疑似节流，2026-09-12 同路径 3 连成功后
  # 又一次挂起），通知重试 3 次、间隔 2s，全败也静默（通知非业务闸）。
  local tries=0
  until printf '%s' "$FULLTEXT" | ssh -i "$TG_RELAY_KEY" \
    -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 \
    "$TG_RELAY_HOST" >/dev/null 2>&1; do
    tries=$((tries + 1))
    [[ $tries -ge 3 ]] && break
    sleep 2
  done
  exit 0
fi

PROXY_ARGS=()
[[ -n "${TG_PROXY:-}" ]] && PROXY_ARGS=(--proxy "$TG_PROXY")
if ! curl -fsS --max-time 10 -s -o /dev/null "${PROXY_ARGS[@]}" \
  "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${TG_CHAT_ID}" \
  --data-urlencode "text=${FULLTEXT}" 2>/dev/null; then
  # 通知发失败必须留痕（journald），否则「静默=健康」分不清通道死了没。
  logger -t tg-notify -p user.warning "sendMessage 失败（非 2xx/网络），消息: ${FULLTEXT:0:200}"
fi
exit 0
