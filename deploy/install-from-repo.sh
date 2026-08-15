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
if [[ ! -f /etc/hutuji/mcp.env && ! -f /etc/hutuji/bridge.env ]]; then
  # DEP-UX-02：旧版 grep 带 2>/dev/null 把「文件不存在」静默吞掉，首次安装（没跑过
  # prepare-host.sh）时脚本从头到尾没有任何提示，装完 enable 才发现服务起不来。
  # 两个 env 全缺必须先点明：跑 prepare-host.sh 生成后再装。
  echo "警告: 未找到 /etc/hutuji/mcp.env 与 /etc/hutuji/bridge.env — 请先运行 deploy/prepare-host.sh 生成，否则服务将因缺 EnvironmentFile 无法启动" >&2
elif grep -q 'REPLACE_ME' /etc/hutuji/mcp.env /etc/hutuji/bridge.env 2>/dev/null; then
  echo "警告: EnvironmentFile 仍含 REPLACE_ME — 服务可装但勿 enable 生产流量" >&2
fi

echo "==> Python venv + 生产依赖"
# 京东云直连 PyPI 常卡住；默认清华 HTTPS 源（可用 HUTUJI_PIP_INDEX 覆盖）。
# 代理使用私有 CA 时显式传 HUTUJI_PIP_CERT；禁止用 --trusted-host 绕过 TLS 校验。
PIP_INDEX="${HUTUJI_PIP_INDEX:-https://pypi.tuna.tsinghua.edu.cn/simple}"
PIP_CERT="${HUTUJI_PIP_CERT:-}"
# 两个 env 都以 root 身份直达 pip，必须先过形状白名单再用。它们不是「配置」而是
# **提权面**：本脚本以 root 跑，pip 会执行索引端提供的构建后端代码，索引地址即
# 代码来源。裸取值有两类真实攻击：①索引换成攻击者主机 → 依赖投毒（上面刚显式
# 清掉 PIP_* 与代理正是为了让来源只由本脚本决定，取值不校验等于把闸门重新打开）；
# ②值里塞空白/分号即多带一个 pip 选项（如 ` --trusted-host x` 或 ` --extra-index-url …`），
# 因为 pip_args 是数组、单元素内含空格虽不会被 word-split，但 `--cert` 之类
# 需要独立参数的选项一旦被拼进同一元素，pip 的解析结果与作者意图不一致，
# 而 `--index-url=<url>` 形态又允许把整段值当选项值走。fail-closed 更省事。
# 索引：必须 https:// 起头（不接受 http/file/git+ 等任何非 TLS 方案），
# 且不含空白与 shell/选项元字符。
case "${PIP_INDEX}" in
  https://*) ;;
  *) echo "HUTUJI_PIP_INDEX 必须以 https:// 开头（拒绝非 TLS 索引）：${PIP_INDEX}" >&2; exit 1 ;;
esac
if printf '%s' "${PIP_INDEX}" | grep -qE '[[:space:]<>|;&$`"'"'"'\\]'; then
  echo "HUTUJI_PIP_INDEX 含空白或元字符，拒绝（防夹带 pip 选项）：${PIP_INDEX}" >&2
  exit 1
fi
# 证书：给了就必须是已存在的绝对路径普通文件，且同样无元字符。相对路径会随 cwd
# 漂移（本脚本在 MCP_DIR 下跑 pip），指到不存在的文件 pip 只会报晦涩的 TLS 错。
if [[ -n "${PIP_CERT}" ]]; then
  if printf '%s' "${PIP_CERT}" | grep -qE '[[:space:]<>|;&$`"'"'"'\\]'; then
    echo "HUTUJI_PIP_CERT 含空白或元字符，拒绝：${PIP_CERT}" >&2
    exit 1
  fi
  case "${PIP_CERT}" in
    /*) ;;
    *) echo "HUTUJI_PIP_CERT 必须是绝对路径：${PIP_CERT}" >&2; exit 1 ;;
  esac
  if [[ ! -f "${PIP_CERT}" ]]; then
    echo "HUTUJI_PIP_CERT 指向的文件不存在：${PIP_CERT}" >&2
    exit 1
  fi
fi
# 不用 `bash -lc`：`-l` 会 source root 的登录期配置（/etc/profile、/root/.bashrc），
# 其中导出的 PIP_TRUSTED_HOST / PIP_EXTRA_INDEX_URL / PIP_INDEX_URL 会静默生效，
# 而本脚本只传 -i 与可选 --cert，覆盖不了它们——上面「禁止绕过 TLS 校验」的承诺
# 会被环境悄悄推翻，PIP_EXTRA_INDEX_URL 更直接打开依赖投毒面。这里既不取登录
# 环境，也显式清掉所有 PIP_* 与代理变量，让安装行为只由本脚本的参数决定。
# `--isolated`（R5-INST-1）再封死**配置文件**层：pip 还会读 /etc/pip.conf、
# $HOME/.pip/pip.conf、.venv/pip.conf 与 PIP_CONFIG_FILE 指向的任意文件，其中的
# extra-index-url / trusted-host 不在上面 -u 清环境变量的覆盖面内。加了 --isolated
# 后 pip 忽略一切配置文件与环境变量，只认命令行传进去的 -i / --cert，fail-closed。
env -u PIP_TRUSTED_HOST -u PIP_INDEX_URL -u PIP_EXTRA_INDEX_URL -u PIP_CERT \
    -u PIP_CONFIG_FILE -u PIP_NO_INDEX -u PIP_FIND_LINKS \
    -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
    MCP_DIR="${MCP_DIR}" HUTUJI_INDEX="${PIP_INDEX}" HUTUJI_CERT="${PIP_CERT}" bash -c '
  set -euo pipefail
  cd "$MCP_DIR"
  pip_args=(-i "$HUTUJI_INDEX" --isolated)
  if [[ -n "$HUTUJI_CERT" ]]; then
    pip_args+=(--cert "$HUTUJI_CERT")
  fi
  # R14-DEP-01：venv 模块对已存在的 .venv 是 no-op（不重建不清空），pip install -r
  # 只升级不删残留——requirements.txt 删包后重跑本脚本，旧包仍留在 venv。刻意不加
  # --clear：修复性重跑时若索引不可达，--clear 会把在跑系统变成装不回来的空 venv。
  # 真要清残留：手工 rm -rf .venv 后再跑本脚本。
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
  if ! nginx -t; then
    # DEP-UX-02：set -e 下裸 `nginx -t` 失败即静默退出，operator 不知道 systemd unit
    # 已装好、只是 nginx 配置没修——修好后直接重跑本脚本即可（幂等）。
    echo "nginx -t 失败：unit 已安装未启动，修复 nginx 配置后可重跑本脚本" >&2
    exit 1
  fi
else
  echo "    未检测到 sites-available；请手工 include ${APP_ROOT}/deploy/nginx-hutuji.conf"
fi

echo "==> 未自动 start。填密钥并放好 TLS 后（首次上线与更新均显式 restart）："
echo "    systemctl enable hutuji-mcp hutuji-cloud-bridge nginx"
echo "    systemctl restart hutuji-mcp hutuji-cloud-bridge"
echo "    systemctl start nginx"
echo "    bash ${APP_ROOT}/deploy/preflight.sh"
