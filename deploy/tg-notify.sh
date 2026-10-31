#!/bin/bash
# Telegram 运维通知出口：双机统一入口，唯一凭据面 /etc/hutuji/tg.env
# （chmod 600，凭据禁入库，仓内无副本）。
# 语义：通知是配件不是功能闸——env 缺失/发失败一律静默 exit 0，绝不掀业务链路；
# 消息自动带主机名前缀（双机同发时有归属）。
#
# 出路二选一（tg.env 决定，运行期探测）：
#   直连（lima 配 TG_PROXY=http://127.0.0.1:7890 经 sing-box 出网）；
#   HTTPS 中继（jd）：配 TG_RELAY_URL=https://hutuji.donglicao.com/tg-relay-<秘密>/
#   正文 POST 给 lima nginx 保密路径（gray conf 只放行 jd 出口 IP），落地
#   127.0.0.1:8092 由 tg-relay-server 代发（本机走 TG_PROXY 出网）。
#   京东云直连 TG 被 DNS 污染+SNI 封锁（2026-09-12 实测 curl 000）；SSH 中继
#   （tailnet :22 握手挂起 + 公网 :22 HIDS 疑似节流 3 连挂死）双双否决。
#
# 用法: tg-notify.sh <TAG> <TEXT...>      普通调用（TAG 如 unit-failed/token-watch）
#       tg-notify.sh --stdin             中继接收端：正文读 stdin（≤4KB）
set -uo pipefail
RELAY_INPUT=0
if [[ "${1:-}" == "--stdin" ]]; then
  RELAY_INPUT=1
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
if [[ -n "${TG_RELAY_URL:-}" ]]; then
  # 中继路径：正文进 body（≤4096 由接收端把关），不撞 argv/引号面。
  # jd HIDS 疑似 SSH 节流的教训：偶发失败重试 3 次、间隔 2s，全败仍静默。
  # 总预算最坏 3×20s + 2×2s = 64s；Python 调用方（portal tg_notify / mcp activation_relay）
  # 的子进程超时必须大于它，否则重试未完即被 SIGKILL、curl 成孤儿，幂等重试形同虚设。
  tries=0
  notify_id="$(hostname -s)-$$-$(date +%s%N)"
  until curl -fsS --max-time 20 -s -o /dev/null \
    -H "X-Hutuji-Notify-Id: ${notify_id}" --data-binary "$FULLTEXT" \
    "${TG_RELAY_URL}" 2>/dev/null; do
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
  [[ $RELAY_INPUT -eq 1 ]] && exit 1
fi
exit 0
