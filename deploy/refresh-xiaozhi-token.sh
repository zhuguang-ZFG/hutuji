#!/bin/bash
# 一键更换小智控制台 JWT（看门狗报警或人工例行刷新时使用）：
#   /opt/hutuji/deploy/refresh-xiaozhi-token.sh '<new-token>'
# 无参则交互读取。流程：形状校验 → 线上验证（GET /agents 必须 200）→
# 备份并原子更新 /etc/hutuji/mcp.env → 重启 hutuji-mcp → /register 探针
# → 对端钩子（token-peer.env 在场时）→ kb-admin token 互联（kb-admin.env 在场时）。
# 退出码：0 成功；1 验证/探针失败；2 用法或形状错误。
set -euo pipefail

ENV_FILE=/etc/hutuji/mcp.env
NEW="${1:-}"
if [[ -z "$NEW" ]]; then
  read -r -p "粘贴新 token: " NEW
fi

# JWT 三段式形状（字符集 base64url）；captcha 登录体系下 token 不可能含别的字符。
if ! [[ "$NEW" =~ ^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$ ]]; then
  echo "token 形状不对（应为三段 JWT）" >&2
  exit 2
fi

get_env() { grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- | tr -d "\"'"; }
API=$(get_env HUTUJI_XIAOZHI_API_BASE); API=${API:-https://xiaozhi.me/api}

code=$(curl --noproxy "*" --max-time 10 -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer $NEW" "$API/agents?page=1&pageSize=1")
if [[ "$code" != "200" ]]; then
  echo "新 token 线上验证失败 HTTP $code，未写入（旧 token 保持不变）" >&2
  exit 1
fi

cp -a "$ENV_FILE" "$ENV_FILE.bak.$(date +%Y%m%d-%H%M%S)"
if grep -q '^HUTUJI_XIAOZHI_TOKEN=' "$ENV_FILE"; then
  # JWT 只含 base64url 字符，| 作分隔符安全。
  sed -i "s|^HUTUJI_XIAOZHI_TOKEN=.*|HUTUJI_XIAOZHI_TOKEN=$NEW|" "$ENV_FILE"
else
  printf 'HUTUJI_XIAOZHI_TOKEN=%s\n' "$NEW" >> "$ENV_FILE"
fi
chmod 600 "$ENV_FILE"

systemctl restart hutuji-mcp
sleep 2
# 探针：本机 /register 对坏形状应回 400（服务活、中转已启用）；503 = env 未生效。
probe=$(curl --noproxy "*" --max-time 8 -s -o /dev/null -w "%{http_code}" \
  -X POST -H "content-type: application/json" -d '{"mac":"zz","code":"x"}' \
  http://127.0.0.1:8300/register)
if [[ "$probe" != "400" ]]; then
  echo "WARN: token 已写入并重启，但 /register 探针回 $probe（期望 400）" >&2
  exit 1
fi
# 清看门狗状态，下一次 cron 重新按有效计。
echo "ok" > /run/hutuji-token-watch.state 2>/dev/null || true

# 对端同步钩子（2026-08-31）：仅当 /etc/hutuji/token-peer.env 存在时生效
# （只装阿里云主节点；京东云不装该文件，天然不回推成环）。自动续期重签会
# 作废旧 token，若不推，对端看门狗下一轮必然 401 报警等人工——本钩子消掉
# 这个固定噪音。推送失败不翻盘本地成功：对端看门狗会报警兜底。
# 子壳隔离是硬要求：set -u 下 token-peer.env 缺 PEER_KEY/PEER_HOST 或文件内
# 引用未定义变量会让 *整个脚本* 以非零退出——而此刻本地重签探针已通过，
# 看门狗会把成功误判为失败、下轮再签造成 token churn。子壳死掉只影响自身，
# 外层 || 兜底 echo，钩子任何结果都不翻盘主流程退出码。
PEER_ENV=/etc/hutuji/token-peer.env
if [[ -f "$PEER_ENV" ]]; then
  (
    # shellcheck disable=SC1090
    . "$PEER_ENV"
    PEER_KEY=${PEER_KEY:-}
    PEER_HOST=${PEER_HOST:-}
    if [[ -z "$PEER_KEY" || -z "$PEER_HOST" ]]; then
      echo "WARN: $PEER_ENV 缺 PEER_KEY/PEER_HOST，跳过对端同步（对端看门狗兜底报警）" >&2
      exit 0
    fi
    if ssh -i "$PEER_KEY" -o BatchMode=yes -o ConnectTimeout=10 \
         -o StrictHostKeyChecking=accept-new \
         "root@$PEER_HOST" "set-token $NEW" >/dev/null 2>&1; then
      echo "OK: 对端（$PEER_HOST）token 已同步"
    else
      echo "WARN: 对端（$PEER_HOST）同步失败，其对端看门狗将报警，需人工补同步" >&2
    fi
  ) || echo "WARN: 对端同步钩子异常退出，已跳过（对端看门狗兜底报警）" >&2
fi
# kb-admin token 互联（2026-08-31）：kb-admin.env 持同一控制台 JWT 用于 KB 发布；
# 重签互踢旧会话后若不同步，KB 发布当场 401「Session expired or logged out」
# （当日 16:15 实证 83 条 FAQ 发布失败）。仅阿里云装 kb-admin；京东云无该文件
# 自然跳过。同钩子纪律：子壳隔离，任何失败不外溢退出码。
KB_ENV=/etc/hutuji/kb-admin.env
if [[ -f "$KB_ENV" ]]; then
  (
    if grep -q '^XIAOZHI_TOKEN=' "$KB_ENV"; then
      # JWT 只含 base64url 字符，| 作分隔符安全（同 mcp.env 写法）。
      sed -i "s|^XIAOZHI_TOKEN=.*|XIAOZHI_TOKEN=$NEW|" "$KB_ENV"
    else
      printf 'XIAOZHI_TOKEN=%s\n' "$NEW" >> "$KB_ENV"
    fi
    chmod 600 "$KB_ENV"
    # unit 不在场（未装机节点）只写文件不重启；restart 失败由子壳外 || 兜底成 WARN。
    if systemctl cat hutuji-kb-admin.service >/dev/null 2>&1; then
      systemctl restart hutuji-kb-admin
      echo "OK: kb-admin token 已同步并重启"
    else
      echo "OK: kb-admin token 已同步（unit 不在场，未重启）"
    fi
  ) || echo "WARN: kb-admin token 同步失败，KB 发布将保持 401，需人工补同步" >&2
fi
echo "OK: token 已更换，hutuji-mcp 已重启，/register 探针通过"
