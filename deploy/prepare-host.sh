#!/usr/bin/env bash
# 京东云主机「上线前准备」——不装业务密钥、不拉代码也可先跑。
# 用法（root）：bash deploy/prepare-host.sh
set -euo pipefail

# systemd/nginx/preflight 均以该生产拓扑为契约，不能假装支持局部覆盖。
APP_USER="hutuji"
APP_ROOT="/opt/hutuji"
ETC_DIR="/etc/hutuji"
SSL_DIR="/etc/nginx/ssl/hutuji"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "需要 root" >&2
  exit 1
fi

echo "==> 创建系统用户 ${APP_USER}"
if ! id -u "${APP_USER}" >/dev/null 2>&1; then
  useradd --system --home "${APP_ROOT}" --shell /usr/sbin/nologin "${APP_USER}"
fi

echo "==> 目录 ${APP_ROOT} ${ETC_DIR} ${SSL_DIR} output"
mkdir -p "${APP_ROOT}" "${ETC_DIR}" "${SSL_DIR}" "${APP_ROOT}/mcp-server/output"
# 与 install-from-repo.sh 同口径：不先把整棵树收归 root 再回补 output（两条 chown
# 之间被打断会让 output 停在 root:root，服务用户写不进产物且重启不自愈），而是
# 绕开 output 收代码树，output 的属主始终只被设成 APP_USER。
find "${APP_ROOT}" -mindepth 1 -maxdepth 1 ! -name mcp-server -exec chown -R root:root {} +
if [[ -d "${APP_ROOT}/mcp-server" ]]; then
  find "${APP_ROOT}/mcp-server" -mindepth 1 -maxdepth 1 ! -name output \
    -exec chown -R root:root {} +
fi
chown root:root "${APP_ROOT}" "${APP_ROOT}/mcp-server"
chown -R "${APP_USER}:${APP_USER}" "${APP_ROOT}/mcp-server/output"
chmod 750 "${APP_ROOT}/mcp-server/output"
chmod 750 "${ETC_DIR}"
chmod 700 "${SSL_DIR}"

echo "==> 占位 EnvironmentFile（若不存在）"
if [[ ! -f "${ETC_DIR}/mcp.env" ]]; then
  cp "$(dirname "$0")/env.mcp.example" "${ETC_DIR}/mcp.env"
  chmod 600 "${ETC_DIR}/mcp.env"
  chown root:root "${ETC_DIR}/mcp.env"
  echo "    已写入 ${ETC_DIR}/mcp.env（仍含 REPLACE_ME，上线前必填）"
fi
if [[ ! -f "${ETC_DIR}/bridge.env" ]]; then
  cp "$(dirname "$0")/env.bridge.example" "${ETC_DIR}/bridge.env"
  chmod 600 "${ETC_DIR}/bridge.env"
  chown root:root "${ETC_DIR}/bridge.env"
  echo "    已写入 ${ETC_DIR}/bridge.env（仍含 REPLACE_ME，上线前必填）"
fi

echo "==> 防火墙提示（未自动改安全组）"
echo "    京东云安全组放行: TCP 22(或SSH改口) / 80 / 443"
echo "    禁止放行: TCP 8300"
if command -v ufw >/dev/null 2>&1; then
  echo "    本机检测到 ufw；未自动 enable。建议: ufw allow OpenSSH; ufw allow 80/tcp; ufw allow 443/tcp; ufw enable"
fi

echo "==> 准备完成。下一步见 deploy/README.md「首次上线」"
