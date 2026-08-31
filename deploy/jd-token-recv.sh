#!/bin/bash
# 京东云 token 接收端（2026-08-31）：只经 authorized_keys command= 强制调用。
# 阿里云自动续期成功后经专用 SSH key 推 token 过来；本脚本是该 key 在京东云
# 上唯一能执行的东西。只放行 `set-token <三段 JWT>`，其余命令一律拒绝。
# token 只走 argv（SSH_ORIGINAL_COMMAND 解析），不打印、不落日志。
# 退出码：0 成功；2 命令形状被拒；其余为 refresh 脚本自身退出码。
set -euo pipefail

cmd="${SSH_ORIGINAL_COMMAND:-}"
if [[ "$cmd" =~ ^set-token\ ([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)$ ]]; then
  exec /opt/hutuji/deploy/refresh-xiaozhi-token.sh "${BASH_REMATCH[1]}"
fi
echo "rejected: 仅接受 set-token <JWT>" >&2
exit 2
