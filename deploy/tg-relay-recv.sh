#!/bin/bash
# Telegram 中继接收端（只部署阿里云主节点）：jd 节点的专用 SSH key 以
# command= 锁死只落到本脚本，正文经 stdin 传入（≤4KB），本机直连 TG 发出。
# 不 eval/不解析 SSH_ORIGINAL_COMMAND——正文只从 stdin 读，注入面=消息文本本身。
set -uo pipefail
exec /opt/hutuji/deploy/tg-notify.sh --stdin
