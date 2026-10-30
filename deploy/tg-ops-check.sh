#!/bin/bash
# VPS 巡检（cron 每小时异常即报 / 每日 08:00 全量 digest）：
#   - hutuji-* unit 进入 failed 态
#   - 关键服务不再 active（mcp/bridge/portal/admin；京东无 bridge/admin）
#   - 磁盘占用 ≥85%（无 swap 机器，磁盘满与内存尖峰同样致命）
#   - 可用内存 <200MB（本机 3.9G 无 swap，见 hutuji-mcp.service 注释）
# 无异常静默退出；--digest 无条件发一条现状，否则长时间静默分不清「健康」和「脚本死了」。
set -uo pipefail
DIR=$(cd "$(dirname "$0")" && pwd)
exec 9>/run/lock/hutuji-tg-ops-check.lock
flock -n 9 || exit 0
DIGEST=0
[[ "${1:-}" == "--digest" ]] && DIGEST=1
msg=""
host=$(hostname -s)

is_expected_inactive() {
  local unit="${1%.service}"
  [[ -n "${JD_HOT_STANDBY:-}" ]] || return 1
  [[ "$unit" == "hutuji-cloud-bridge" || "$unit" == "hutuji-draw-admin" ]]
}

# 只扫本项目 failed unit；--plain 去掉 systemd 的“●”状态列。
failed=""
while read -r unit _; do
  [[ -n "$unit" ]] || continue
  is_expected_inactive "$unit" && continue
  [[ "$unit" == hutuji-draw-worker@*.service ]] && continue
  failed+="$unit "
done < <(LC_ALL=C systemctl list-units 'hutuji-*' --state=failed --plain --no-legend --no-pager 2>/dev/null)
[[ -n "$failed" ]] && msg+="failed: ${failed% }"$'\n'

for u in hutuji-mcp hutuji-cloud-bridge hutuji-draw-portal hutuji-draw-admin; do
  is_expected_inactive "$u" && continue
  if ! systemctl is-active --quiet "$u" 2>/dev/null; then
    msg+="not active: $u"$'\n'
  fi
done

# 宿主层只点名业务必需依赖，避免同机其他项目/可选 timer 污染项目告警。
systemctl is-active --quiet nginx 2>/dev/null || msg+="not active dependency: nginx"$'\n'
if ! systemctl is-active --quiet ssh 2>/dev/null && ! systemctl is-active --quiet sshd 2>/dev/null; then
  msg+="not active dependency: ssh/sshd"$'\n'
fi

# worker 模板实例逐个点名（任一 failed 即报，八实例分推费）
wfail=$(LC_ALL=C systemctl list-units 'hutuji-draw-worker@*' --plain --no-legend --no-pager 2>/dev/null \
  | awk '$3=="failed" || $4=="failed" {print $1}' | tr '\n' ' ')
[[ -n "$wfail" ]] && msg+="worker failed: ${wfail% }"$'\n'

disk=$(df -x tmpfs -x devtmpfs --output=pcent,target 2>/dev/null \
  | tail -n +2 | awk '{gsub("%","",$1); if ($1+0>=85) print $2" "$1"%"}')
[[ -n "$disk" ]] && msg+="disk>=85%: $(echo "$disk" | tr '\n' ';')"$'\n'

avail=$(awk '/MemAvailable/{print $2}' /proc/meminfo)
if [[ -n "$avail" && $avail -lt 204800 ]]; then
  msg+="mem avail ${avail}kB (<200MB)"$'\n'
fi

if [[ $DIGEST -eq 1 ]]; then
  body="${msg:-all healthy}"
  "$DIR/tg-notify.sh" digest "[$host] 日报：
${body}"
  exit 0
fi
[[ -n "$msg" ]] && "$DIR/tg-notify.sh" ops-check "$msg"
exit 0
