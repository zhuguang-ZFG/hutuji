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
# 京东云直连 PyPI 常卡住；默认清华 HTTPS 源（可用 HUTUJI_PIP_INDEX 覆盖）。
# 代理使用私有 CA 时显式传 HUTUJI_PIP_CERT；禁止用 --trusted-host 绕过 TLS 校验。
PIP_INDEX="${HUTUJI_PIP_INDEX:-https://pypi.tuna.tsinghua.edu.cn/simple}"
PIP_CERT="${HUTUJI_PIP_CERT:-}"
sudo -u "${APP_USER}" env MCP_DIR="${MCP_DIR}" PIP_INDEX="${PIP_INDEX}" PIP_CERT="${PIP_CERT}" bash -lc '
  set -euo pipefail
  cd "$MCP_DIR"
  pip_args=(-i "$PIP_INDEX")
  if [[ -n "$PIP_CERT" ]]; then
    pip_args+=(--cert "$PIP_CERT")
  fi
  python3 -m venv .venv
  .venv/bin/pip install -U pip "${pip_args[@]}"
  .venv/bin/pip install -r requirements.txt "${pip_args[@]}"
'

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
