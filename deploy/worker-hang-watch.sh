#!/usr/bin/env bash
# worker-hang-watch：draw-worker 卡在 deactivating 超 25s 时 py-spy dump 主进程全线程栈，
# 同时留 cgroup 残余进程清单。背景：2026-09-02 22:01 重启时 8/8 worker 消费循环 4s 内
# 退出但进程又挂 267–298s 才消失（全部 exit 0、无 SIGKILL），根因未定——本脚本就是取证
# 陷阱，下一次 restart 自然捕获真凶；结案后拆除（连同 /root/pyspy-venv 与本 cron）。
# 安装：/etc/cron.d/hutuji-hang-watch 每分钟一次；全程只读 + 写 dump，不碰业务状态。
set -u

STAMP_DIR=/run/hutuji-hang-watch
DUMP_DIR=/root/worker-hang-dumps
PYSPY=/root/pyspy-venv/bin/py-spy
mkdir -p "$STAMP_DIR" "$DUMP_DIR"

for unit in $(systemctl list-units 'hutuji-draw-worker@*' --state=deactivating --no-legend --plain | awk '{print $1}'); do
  stamp="$STAMP_DIR/$unit"
  now=$(date +%s)
  if [ ! -f "$stamp" ]; then
    echo "$now" > "$stamp"
    continue
  fi
  first=$(cat "$stamp" 2>/dev/null || echo "$now")
  pid=$(systemctl show "$unit" -p MainPID --value 2>/dev/null)
  if [ $((now - first)) -ge 25 ]; then
    out="$DUMP_DIR/$(date +%Y%m%d-%H%M%S)-$unit.txt"
    {
      echo "# unit=$unit first_deactivating=$first dump_at=$now main_pid=$pid"
      if [ "$pid" != "0" ] && [ -n "$pid" ]; then
        "$PYSPY" dump --pid "$pid"
      else
        echo "# 主进程已退出，残余在 cgroup："
      fi
      echo "# --- cgroup 残余 ---"
      systemd-cgls --no-page "/system.slice/$unit" 2>&1
    } > "$out" 2>&1
    logger -t hutuji-hang-watch "worker 悬挂 $((now - first))s（unit=$unit pid=$pid），栈落盘 $out"
    rm -f "$stamp"
  fi
done

# 单元复活/消失后清旧标记，防下次 stop 误用陈旧起点
for stamp in "$STAMP_DIR"/hutuji-draw-worker@*; do
  [ -e "$stamp" ] || continue
  unit=${stamp##*/}
  systemctl is-active --quiet "$unit" && rm -f "$stamp"
done

exit 0
