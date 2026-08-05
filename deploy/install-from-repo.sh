#!/usr/bin/env bash
# 在主机上：代码已放到 /opt/hutuji 后，安装 venv + systemd + nginx 片段。
# 用法（root）：cd /opt/hutuji && bash deploy/install-from-repo.sh
set -euo pipefail

APP_ROOT="/opt/hutuji"
APP_USER="hutuji"
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
# 不用 `bash -lc`：`-l` 会 source root 的登录期配置（/etc/profile、/root/.bashrc），
# 其中导出的 PIP_TRUSTED_HOST / PIP_EXTRA_INDEX_URL / PIP_INDEX_URL 会静默生效，
# 而本脚本只传 -i 与可选 --cert，覆盖不了它们——上面「禁止绕过 TLS 校验」的承诺
# 会被环境悄悄推翻，PIP_EXTRA_INDEX_URL 更直接打开依赖投毒面。这里既不取登录
# 环境，也显式清掉所有 PIP_* 与代理变量，让安装行为只由本脚本的参数决定。
env -u PIP_TRUSTED_HOST -u PIP_INDEX_URL -u PIP_EXTRA_INDEX_URL -u PIP_CERT \
    -u PIP_CONFIG_FILE -u PIP_NO_INDEX -u PIP_FIND_LINKS \
    -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
    MCP_DIR="${MCP_DIR}" HUTUJI_INDEX="${PIP_INDEX}" HUTUJI_CERT="${PIP_CERT}" bash -c '
  set -euo pipefail
  cd "$MCP_DIR"
  pip_args=(-i "$HUTUJI_INDEX")
  if [[ -n "$HUTUJI_CERT" ]]; then
    pip_args+=(--cert "$HUTUJI_CERT")
  fi
  python3 -m venv .venv
  .venv/bin/pip install -U pip "${pip_args[@]}"
  .venv/bin/pip install -r requirements.txt "${pip_args[@]}"
'
# 运行时只允许服务用户写 output；代码、venv 和部署脚本均保持 root 所有。
# 顺序要紧：先把 APP_ROOT 整棵树收归 root 会连带把 output 也改成 root 所有，若在
# 两条 chown 之间被打断（Ctrl-C / SSH 断线 / OOM），output 会停在 root:root，服务
# 用户 hutuji 写不进产物，而 unit 的 ReadWritePaths 只授予挂载可写、不改 DAC 属主，
# 重启也不自愈。故先 --exclude 式地只收代码树，output 的属主自始至终不被动过。
find "${APP_ROOT}" -mindepth 1 -maxdepth 1 ! -name mcp-server -exec chown -R root:root {} +
find "${MCP_DIR}" -mindepth 1 -maxdepth 1 ! -name output -exec chown -R root:root {} +
chown root:root "${APP_ROOT}" "${MCP_DIR}"
chown -R "${APP_USER}:${APP_USER}" "${MCP_DIR}/output"
chmod 750 "${MCP_DIR}/output"

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
