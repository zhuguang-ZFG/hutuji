#!/bin/bash
# 小智设备 token（「主题配置」URL token）看门狗（cron 每小时）：用
# /etc/hutuji/mcp.env 里的 HUTUJI_XIAOZHI_DEVICE_TOKEN 调官方设备消息 API 的
# 只读工具 hutuji.status，401/403 即判作废并 logger 点名（journald）。
# 与控制台 JWT 看门狗（xiaozhi-token-watch.sh）互补：那条只覆盖
# HUTUJI_XIAOZHI_TOKEN，设备 token 此前任何节点都无检测——作废（每次打开
# 控制台「主题配置」面板即重新签发并作废旧 token，2026-08-28 实证）后
# 链式推送静默失能：主节点退化为两步衔接，热备节点到切换当天才暴露。
# 设备 token 无程序化重签路径（不走 /auth/login），故本脚本只检测+报警，
# 不接自动续期——这是与 JWT 看门狗的刻意差异，不要照搬 auto 分支过来。
# 探针语义：auth 在云端先于设备路由判定，真 401/403 与设备在线状态无关；
# 设备关机/网络异常/5xx 均非作废证据，只在状态翻转时记一条 warning。
# 退出码：0 有效/不可判/未启用；1 作废。
set -uo pipefail

ENV_FILE=/etc/hutuji/mcp.env
STATE_FILE=/run/hutuji-device-token-watch.state
ALERT_FILE="$STATE_FILE.alert"
API_DEFAULT=https://xiaozhi.me/api

[[ -r "$ENV_FILE" ]] || exit 0
get_env() { grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- | tr -d "\"'"; }
TOKEN=$(get_env HUTUJI_XIAOZHI_DEVICE_TOKEN)
API=$(get_env HUTUJI_XIAOZHI_API_BASE); API=${API:-$API_DEFAULT}

# 未配设备 token = 链式推送未启用，无事可看，静默退出（同 JWT 看门狗口径）。
[[ -n "$TOKEN" ]] || exit 0

# hutuji.status 是只读状态工具（StatusJson 只读暴露遥测，protocol §4）：
# 不触发 TTS/屏显/运动，对在线设备无副作用；设备离线时云端返回非 401/403，
# 落入下方「不可判」分支。
code=$(curl --noproxy "*" --max-time 15 -s -o /dev/null -w "%{http_code}" \
  -X POST -H "Authorization: Bearer $TOKEN" -H "content-type: application/json" \
  -d '{"name":"hutuji.status","arguments":{}}' \
  "$API/messaging/device/tools/call")

last_state=$(cat "$STATE_FILE" 2>/dev/null || echo "unknown")

if [[ "$code" == "200" ]]; then
  if [[ "$last_state" == "invalid" ]]; then
    logger -t hutuji-device-token-watch -p user.info "小智设备 token 已恢复有效"
  fi
  echo "ok" > "$STATE_FILE"
  exit 0
fi

if [[ "$code" == "401" || "$code" == "403" ]]; then
  # 翻转时必报；持续失效每 6 小时续报一次（cron 每小时一拍，用 alert 时间戳
  # 节流，避免人工处理窗口期内 journald 刷屏）。
  now=$(date +%s)
  last_alert=0
  if [[ -f "$ALERT_FILE" ]]; then
    last_alert=$(cat "$ALERT_FILE" 2>/dev/null || echo 0)
    [[ "$last_alert" =~ ^[0-9]+$ ]] || last_alert=0
  fi
  if [[ "$last_state" != "invalid" || $((now - last_alert)) -ge 21600 ]]; then
    logger -t hutuji-device-token-watch -p user.err \
      "小智设备 token 已失效（HTTP $code）：链式推送 hutuji.draw/hutuji.write 将静默回落两步衔接（多页文章两步回落不可用）。" \
      "请在 xiaozhi.me/console 打开设备「主题配置」面板重新签发 URL，取其中 token 更新双机 mcp.env 的" \
      "HUTUJI_XIAOZHI_DEVICE_TOKEN 并 restart hutuji-mcp。"
    echo "$now" > "$ALERT_FILE"
  fi
  echo "invalid" > "$STATE_FILE"
  exit 1
fi

# 网络错误/5xx/设备离线均非作废证据：只在从 ok 翻转时记一条 warning。
if [[ "$last_state" == "ok" ]]; then
    logger -t hutuji-device-token-watch -p user.warning \
      "小智设备 token 探测不可判 HTTP $code（设备离线或网络异常，非作废证据，不翻转状态）"
fi
echo "unknown" > "$STATE_FILE"
exit 0
