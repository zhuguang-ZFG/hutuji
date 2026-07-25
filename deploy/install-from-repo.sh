#!/usr/bin/env bash
# 在主机上：代码已放到 /opt/hutuji 后，安装 venv + systemd + nginx 片段。
# 用法（root）：cd /opt/hutuji && bash deploy/install-from-repo.sh
set -euo pipefail

APP_ROOT="${HUTUJI_APP_ROOT:-/opt/hutuji}"
APP_USER="${HUTUJI_APP_USER:-hutuji}"
MCP_DIR="${APP_ROOT}/mcp-server"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "需要 root" >&2
  exit 1
fi
if [[ ! -f "${MCP_DIR}/requirements.txt" ]]; then
  echo "未找到 ${MCP_DIR}/requirements.txt — 先把本仓同步到 ${APP_ROOT}" >&2
  exit 1
fi
if grep -q 'REPLACE_ME' /etc/hutuji/mcp.env /etc/hutuji/bridge.env 2>/dev/null; then
  echo "警告: EnvironmentFile 仍含 REPLACE_ME — 服务可装但勿 enable 生产流量" >&2
fi

echo "==> Python venv + 生产依赖"
sudo -u "${APP_USER}" bash -lc "
  set -euo pipefail
  cd '${MCP_DIR}'
  python3 -m venv .venv
  .venv/bin/pip install -U pip
  .venv/bin/pip install -r requirements.txt
"

echo "==> systemd units"
cp "${APP_ROOT}/deploy/hutuji-mcp.service" /etc/systemd/system/
cp "${APP_ROOT}/deploy/hutuji-cloud-bridge.service" /etc/systemd/system/
systemctl daemon-reload

echo "==> nginx 站点（未自动签发证书）"
if [[ -d /etc/nginx/sites-available ]]; then
  cp "${APP_ROOT}/deploy/nginx-hutuji.conf" /etc/nginx/sites-available/hutuji
  ln -sfn /etc/nginx/sites-available/hutuji /etc/nginx/sites-enabled/hutuji
  nginx -t
else
  echo "    未检测到 sites-available；请手工 include ${APP_ROOT}/deploy/nginx-hutuji.conf"
fi

echo "==> 未自动 start。填密钥并放好 TLS 后："
echo "    systemctl enable --now hutuji-mcp hutuji-cloud-bridge nginx"
echo "    bash ${APP_ROOT}/deploy/preflight.sh"
