#!/bin/bash
# Telegram 中继接收端（只部署阿里云主节点）：jd 节点的专用 SSH key 以
# command= 锁死只落到本脚本，正文经 stdin 传入（≤4KB），本机直连 TG 发出。
# 不 eval/不解析 SSH_ORIGINAL_COMMAND——正文只从 stdin 读，注入面=消息文本本身。
# （2026-09-12：tailnet HTTP 8092 被 tailnet ACL 挡死，SSH over tailnet 握手
# 挂起疑 jd HIDS DPI；jd→lima 公网 :22 实测 1.2s 完成认证，故中继走公网 SSH。）
set -uo pipefail
exec /opt/hutuji/deploy/tg-notify.sh --stdin
