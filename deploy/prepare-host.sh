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

echo "==> 量产加固（2026-09-10 双机已实施的口径，新节点照搬）"
# SYN 洪水/突发吸收
cat > /etc/sysctl.d/99-hutuji-hardening.conf <<'SYSCTL'
net.ipv4.tcp_max_syn_backlog = 4096
net.core.netdev_max_backlog = 4096
net.ipv4.tcp_slow_start_after_idle = 0
net.ipv4.tcp_fin_timeout = 15
SYSCTL
sysctl --system >/dev/null 2>&1 || true
# fail2ban：ssh 暴破（3 次/1h 封）+ nginx 429 洪水（10 次/10min 封 1h）
if command -v apt-get >/dev/null 2>&1; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y fail2ban >/dev/null 2>&1 || true
  F2B_LOG=/var/log/auth.log
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y fail2ban >/dev/null 2>&1 || true
  F2B_LOG=/var/log/secure
fi
if command -v fail2ban-client >/dev/null 2>&1; then
  mkdir -p /etc/fail2ban/jail.d
  cat > /etc/fail2ban/jail.d/hutuji.conf <<F2B
[DEFAULT]
bantime = 3600
findtime = 600
backend = auto

[sshd]
enabled = true
maxretry = 3
logpath = ${F2B_LOG}

[nginx-limit-req]
enabled = true
maxretry = 10
logpath = /var/log/nginx/error.log
F2B
  systemctl enable --now fail2ban >/dev/null 2>&1 || systemctl restart fail2ban || true
fi
# sshd：密钥唯一通道（阿里云 Alibaba Linux 3 无 Include，须直写主配置首现行）
if [[ -d /etc/ssh/sshd_config.d ]] && grep -q "^Include /etc/ssh/sshd_config.d" /etc/ssh/sshd_config; then
  cat > /etc/ssh/sshd_config.d/99-hutuji.conf <<'SSHD'
PasswordAuthentication no
PermitRootLogin prohibit-password
MaxAuthTries 3
LoginGraceTime 30
X11Forwarding no
SSHD
else
  sed -i -E "s/^#?PasswordAuthentication .*/PasswordAuthentication no/;s/^#?PermitRootLogin .*/PermitRootLogin prohibit-password/;s/^#?X11Forwarding .*/X11Forwarding no/" /etc/ssh/sshd_config
  grep -q "^MaxAuthTries" /etc/ssh/sshd_config || echo "MaxAuthTries 3" >> /etc/ssh/sshd_config
  grep -q "^LoginGraceTime" /etc/ssh/sshd_config || echo "LoginGraceTime 30" >> /etc/ssh/sshd_config
fi
sshd -t && systemctl reload sshd || echo "    ⚠ sshd 配置校验失败，请人工核对 /etc/ssh/sshd_config"

echo "==> 防火墙提示（未自动改安全组）"
echo "    京东云安全组放行: TCP 22(或SSH改口) / 80 / 443"
echo "    禁止放行: TCP 8300"
if command -v ufw >/dev/null 2>&1; then
  echo "    本机检测到 ufw；未自动 enable。建议: ufw allow OpenSSH; ufw allow 80/tcp; ufw allow 443/tcp; ufw enable"
fi

echo "==> 准备完成。下一步见 deploy/README.md「首次上线」"
