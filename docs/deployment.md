# 京东云部署

> ③ MCP 服务**唯一**生产落点：京东云 VPS。模板在 `../deploy/`。真实凭据不入库。  
> **当前**：生产服务已常驻并实机验收；最新 MCP 校验器发布为 `b50a10a`（2026-07-29），
> 证据见 `release-readiness.md`。

## 目标机

| 项 | 值 |
|----|-----|
| 公网 | `117.72.118.95` |
| 规格 | 2 核 4G / 5Mbps |
| 上行实测 | **2026-07-26**：公网拉 512KiB 中位 **1.13s / ~3.72 Mbps**（本机→`http://117.72.118.95/…`，3 轮；测完文件已删） |
| 角色 | 专机只跑 ③（uvicorn + 小智桥接 + nginx） |
| 冗余 | 无；单点 |
| 实机业务 | **2026-07-27**：`hutuji-mcp` + **Cloudflare Tunnel HTTPS**（Let's Encrypt） + `hutuji-cloud-bridge` 已连小智 WSS；`HUTUJI_PUBLIC_BASE_URL=https://hutuji.donglicao.com`；绘图 LLM = llm7 |

## 拓扑

```
公网客户端 / S3 板
        │ HTTPS（hutuji.donglicao.com）
        ▼
   Cloudflare (Let's Encrypt 证书)
        │ Cloudflare Tunnel（QUIC，出站连接绕过京东云 ICP）
        ▼
   cloudflared (localhost)  ──► nginx :80 /files  ──► 127.0.0.1:8300/files
        │
        ✗  /mcp  nginx 404（禁止暴露）

小智官方云 WSS
        │
        ▼
 hutuji-cloud-bridge（本机）──mcp-proxy──► 127.0.0.1:8300/mcp
```

### Cloudflare Tunnel（2026-07-27 上线）

京东云对未备案域名做 HTTP Host + TLS SNI 双重拦截，无法直连 HTTPS。
使用 Cloudflare Tunnel（免费）从 VPS 出站建隧道，完全绕过。

| 项 | 值 |
|----|-----|
| 隧道名 | `hutuji` |
| 域名 | `hutuji.donglicao.com` |
| DNS 记录 | CNAME → `<tunnel-id>.cfargotunnel.com`（已代理） |
| 证书 | Let's Encrypt（Cloudflare 管理），ESP-IDF cert bundle 信任 |
| cloudflared 配置 | `/etc/cloudflared/config.yml`（本地管理模式） |
| 服务 | `systemctl status cloudflared`（enabled，开机自启） |
| 入口规则 | `hutuji.donglicao.com` → `http://localhost:80`；其余 404 |
| Cloudflare 账号 | 域名 `donglicao.com` 托管于 Cloudflare |

> 尝试过的失败方案：Flexible SSL（JD Cloud SNI 拦截）、Origin Rules（付费功能）、Workers（fetch 不允许裸 IP → 1003）。

## 阶段 0：上线前准备（可先做、不启业务）

目的：空出专机、建好目录与用户，**不**把密钥写进 git、**不**对公网开 8300。

1. 京东云控制台安全组：只放行 **22（或 SSH 改口）/ 80 / 443**；确认 **无 8300**。  
2. SSH 用密钥登录（勿把密码写进仓库脚本）。  
3. 将本仓同步到 `/opt/hutuji`。  
4. 执行：`bash /opt/hutuji/deploy/prepare-host.sh`  
5. （若机上有旧杂物）按 `../deploy/README.md`「清理旧环境」停掉无关服务、清空 `output/`。  
6. 勾选本页下方「阶段 0 清单」——勾完只代表**主机坑位就绪**，不代表已上线。

### 阶段 0 清单

- [x] 主机可登录；已建用户 `hutuji`、`/opt/hutuji`、`/etc/hutuji/{mcp,bridge}.env`（600）、`/etc/nginx/ssl/hutuji/`  
- [x] 点名业务已停删（dlc-drawing / MySQL / Redis / qwen-gateway / nginx new-api）；UFW 仅 22/80/443  
- [x] `/opt/hutuji` **本仓代码**就位  
- [x] UFW 仅 22/80/443；8300 未放行（安全组请再复核一眼）  
- [x] `mcp.env` 已填（llm7）；TLS 自签已放 `/etc/nginx/ssl/hutuji/`  
- [x] `bridge.env` 已填真实 `MCP_ENDPOINT`（600；禁入库）

### 实机清理（2026-07-26，用户点名后已执行）

已停并删除：

| 项 | 处置 |
|----|------|
| dlc-drawing + `/opt/dlc-drawing` | unit/目录删除；`chat.donglicao.com` nginx 配置删除 |
| qwen-gateway + `/opt/qwen-gateway` | 进程/目录删除；3002 已释放 |
| MySQL / Redis | 包 purge + 数据目录删除；3306/6379 已释放 |
| nginx new-api / newapi / newapi-managed | sites-available 删除；`/root/newapi-backups` 删除 |
| UFW | 仅保留 22/80/443 |

第二轮（建议可删，已执行）：Docker/containerd、lima 残留与 `/root` 杂目录（openclaw/prometheus/dlc-retire 等）。cloudflared 已重新安装用于 Cloudflare Tunnel。

仍保留：nginx、Tailscale、京东云 agent（jdog/ifrit/jcs）、hutuji 业务。

### 阶段 1 进度（2026-07-26）

| 项 | 状态 | 证据 |
|----|------|------|
| 代码 → `/opt/hutuji` | ✅ | `requirements.txt` 在位；venv IMPORT_OK |
| `mcp.env`（llm7） | ✅ | 本机 `generate_svg` 出 SVG_LEN≈1.4k |
| Cloudflare Tunnel HTTPS | ✅ | `https://hutuji.donglicao.com/files/…` 经隧道到 nginx；Let's Encrypt 证书 |
| `hutuji-mcp` | ✅ | `systemctl active`；`127.0.0.1:8300` |
| `hutuji-cloud-bridge` | ✅ | WSS 已连接；云端 `initialize` + `tools/list` 已进日志 |
| `preflight.sh` 全 PASS | ✅ 当前仓内脚本已部署；2026-08-04 生产复验 14/14 | `pass=14 fail=0 skip=0`；四个发布文件与 `f59c0a0` 提交对象哈希一致 |
| 小智控制台工具可见 | 🔬 | 控制台刷新后应见 `hutuji_draw` 等 |
| 小智知识库 | ✅ | 用户已上传 `docs/xiaozhi-agent/knowledge-base.md`（2026-07-26）；请确认智能体页已**关联** |

> TLS 已切 Cloudflare Tunnel + Let’s Encrypt（2026-07-27），ESP-IDF cert bundle 信任，S3 无需 `-k`。  
> `install-from-repo.sh` 默认清华 PyPI 源（京东云直连 PyPI 易卡住）。

### nginx 站点收敛（2026-07-31 14:51–14:53，已执行）

> 证据：`results/nginx-80-files-deploy-evidence-20260731.md`（`results/` 不入库）。
> **纠错**：此前 `release-readiness.md` / `worktree-inventory.md` 写「nginx :80 /files 反代未部署」是**误判**——
> 该反代自 **07-27 03:06** 起就在 `/etc/nginx/sites-enabled/hutuji` 里生效。

上线前实测三份配置**互不相同**，且 `sites-enabled/hutuji` 是**普通文件而非符号链接**：

| 副本 | 大小 / md5 | 内容 |
|------|-----------|------|
| 线上 `sites-enabled/hutuji`（生效） | 1804B `9f800105…` | 已含 `:80 location /files/`；`server_name` 含隧道域名 |
| 线上 `sites-available/hutuji`（不生效） | 1771B `e9f21b4c…` | 07-27 02:07 旧版，两个 server 块 `server_name` **只有裸 IP** |
| 本仓 `deploy/nginx-hutuji.conf` | LF 归一化 `5a424341…` | 与生效版仅差**两处注释** |

两个方向都是坑：`nginx.conf:65` 只 `include sites-enabled/*`，所以**改 `sites-available` 是静默空操作**；
反之谁 `ln -sf` 建链接，就会把生效版**回退**成缺隧道域名的旧版（`/files` 反代对隧道 Host 失效）。

```bash
# 已执行（可回滚：/root/nginx-hutuji-{enabled,available}.bak.20260731-145120）
scp deploy/nginx-hutuji.conf root@117.72.118.95:/tmp/hutuji-candidate.conf
ssh … 'sed -i "s/\r$//" /tmp/hutuji-candidate.conf
        install -m 644 /tmp/hutuji-candidate.conf /etc/nginx/sites-available/hutuji
        ln -sfn /etc/nginx/sites-available/hutuji /etc/nginx/sites-enabled/hutuji
        nginx -t && systemctl reload nginx'
```

历史复验（reload 前后逐项一致，无回归）：`:80` 两种 Host（裸 IP / `hutuji.donglicao.com`）下
`/files/nope`→404、`POST /files/nope`→403（`limit_except` 生效）、`/mcp`→404、`/`→301；
公网隧道 `/files/nope`→404、`/mcp`→404；`preflight.sh` 先 `pass=9 fail=0`。

**历史 9→14 项记录（已被下方第三轮安全契约 supersede）**：当时新增 nginx 层五项并得到 `pass=14 fail=0`，但“真下载”实际只比较了命令替换后的正文，没有检查 HTTP 状态、`Content-Length` 或 `X-Hutuji-CRC32`；符号链接项也只检查 `-L`，没有确认目标。旧 PASS 只能证明当时的旧判据，不证明当前安全契约。

**首次真 200 端到端**：在 `HUTUJI_OUTPUT_DIR=/opt/hutuji/mcp-server/output` 放一份合法命名产物
（`draw_20260731_145211_<22 位 token>.gcode`，31B），公网隧道与 `:80` 回源路径均 `HTTP=200 bytes=31`
且正文逐字回读，测完立即 `rm`。

### 第三轮传输安全加固(2026-07-31 21:20-21:22,**已部署并复验**)

2026-07-31 code review 发现旧 `:80` server 的 `/files/` 对任意公网来源开放,且 preflight 可能校验
与 systemd 实际生效值不同的首个重复赋值。本仓已修,**并已上线**:

1. `:80 /files/` 加 `allow 127.0.0.1`、`allow ::1`、`deny all`,只给本机 cloudflared 回源;
2. EnvironmentFile 不 `source`/`eval`,支持注释、空行、可选成对引号;重复键与 systemd 一致取最后赋值;
3. `HUTUJI_PUBLIC_BASE_URL` 只允许 HTTPS 默认 443 或显式 `:443`,且拒绝裸 `?`/`#` 与 loopback 主机;`OUTPUT_DIR` 先经 `realpath -m` 归一化,并要求字面路径与解析后的真实路径都位于 `/opt/` 下;
4. enabled 精确链接与 LF 哈希之外,`nginx -T` 还须证明活动配置含 loopback-only `:80 /files` ACL;
5. reload 后 localhost `:80` 活动探针核 GET=404、POST=403、`/mcp`=404;真下载同时核
   HTTP 200、`Content-Length`、IEEE CRC32 头和逐字节正文;解析值只经位置参数传入子 shell。

**部署前置核实**(必须先做,否则 ACL 会切断隧道):`/etc/cloudflared/config.yml` 的
`service: http://localhost:80`,而本机 `getent hosts localhost` **只返回 `::1`**,nginx 又只
`listen 80`(IPv4)。实测公网隧道请求在 access.log 里 `remote_addr=127.0.0.1`——cloudflared 的
Go dialer 在 `::1` 被拒后回落 IPv4,故 loopback ACL 安全;`allow ::1` 仅为冗余保险。

```bash
# 已执行(回滚副本:/root/hutuji-bak-20260731-212012/,含 nginx 两份 + preflight.sh + 三个 .py)
scp deploy/{nginx-hutuji.conf,preflight.sh,env.mcp.example} \
    mcp-server/hutuji_mcp/{cloud_bridge.py,config.py,server.py} root@…:/tmp/hutuji-deploy/
ssh … 'cd /tmp/hutuji-deploy && for f in *; do sed -i "s/\r$//" "$f"; done
        install -m 644 nginx-hutuji.conf /etc/nginx/sites-available/hutuji
        install -m 644 nginx-hutuji.conf /opt/hutuji/deploy/        # 漂移判据的比较基准,别漏
        install -m 755 preflight.sh /opt/hutuji/deploy/
        install -m 644 cloud_bridge.py config.py server.py /opt/hutuji/mcp-server/hutuji_mcp/
        ln -sfn /etc/nginx/sites-available/hutuji /etc/nginx/sites-enabled/hutuji
        nginx -t && systemctl reload nginx
        systemctl restart hutuji-mcp hutuji-cloud-bridge'
```

**复验证据**(2026-07-31 21:22):`preflight.sh` **`pass=14 fail=0`**;LF 归一化 md5 本仓 == 线上
`db3d5daed73c63e7684063efb65d09f2`;`readlink -f sites-enabled/hutuji` == `sites-available/hutuji`;
三服务 `active`。公网面:HTTPS `/mcp` GET/POST **均 404**、直连 `:80 /files/*` **403**、直连 `:80 /mcp` 404、
HTTPS `/files/__nonexistent__` 404。桥接重连后云端真实 `initialize`+`tools/list`+`ping` 已进日志,
且新脱敏 filter 生效(URL 记为 `<redacted-url>`、`mcp-session-id` 截断)。

> 首次部署踩坑:只 `install` 到 `/etc/nginx/sites-available/` 会让第 12 项
> 「线上 nginx conf 与本仓模板一致」**fail**——该项比较基准是 `/opt/hutuji/deploy/nginx-hutuji.conf`,
> 两处都要同步。

> **仓内 preflight 修复已部署（2026-08-04，supersede 2026-08-01“未部署”状态）**：此前 `f3ef14b`/`8e4b9b4` 引入的变量初始化顺序回归，以及随后补齐的 `[skip]`、`ss` 前置、curl 超时/禁代理、本机 5xx 不算可达等硬化，已随 hutuji `f59c0a0` scp 到 `/opt/hutuji/deploy/preflight.sh`。生产当场复验 `pass=14 fail=0 skip=0`，生产脚本 SHA-256 `7196aa9efa13ccc839bf4b2e59294aee14142e062659efb44fab9a669ae8dc97` 与提交对象一致；回滚副本 `/root/hutuji-bak-20260804-f59c0a0/`。第 12 项仍只核 nginx 模板，故每次更新 `preflight.sh` 仍必须显式上传并当场执行。


#### 漂移判据（必须 LF 归一化）

本仓 `core.autocrlf` 让工作副本是 CRLF，裸 `md5sum` 永远对不上线上：

```bash
tr -d '\r' < deploy/nginx-hutuji.conf | md5sum
ssh … 'readlink -f /etc/nginx/sites-enabled/hutuji; tr -d "\r" < /etc/nginx/sites-enabled/hutuji | md5sum'
# 目标必须是 /etc/nginx/sites-available/hutuji，两个动态计算的 md5 必须相同。
```

#### SSH 私钥名有坑

可用私钥是 `C:/Users/zhugu/.ssh/jdcloud_ed2519`（**少一个 `5`**）；同目录 `jdcloud_ed25519`
会被服务端拒 `Permission denied (publickey,password)`。`~/.ssh/config` 的 `Host jdcloud` 走密码认证，
非交互脚本请显式 `-i .../jdcloud_ed2519 -o IdentitiesOnly=yes`。

## 首次上线（阶段 1）剩余

1. 小智控制台：确认知识库已**关联到智能体**；刷新 MCP 工具列表；试 `hutuji_status` / 语音「画一只侧脸简笔小猫」。  
2. 角色提示若未贴：`docs/xiaozhi-agent/role-prompt.md`。  
3. 证据写入 `docs/templates/release-manifest.md`。

## 环境变量

| 文件 | 变量 | 说明 |
|------|------|------|
| `mcp.env` | `HUTUJI_HOST/PORT` | 服务内默认仍绑 127.0.0.1；unit 已写死 loopback |
| `mcp.env` | `HUTUJI_PUBLIC_BASE_URL` | HTTPS 公网前缀（S3 下载） |
| `mcp.env` | `HUTUJI_LLM_*` | LLM |
| `mcp.env` | `HUTUJI_OUTPUT_*` | 产出目录与 TTL |
| `bridge.env` | `MCP_ENDPOINT` | 仅桥接；小智控制台复制 |
| `bridge.env` | `HUTUJI_LOCAL_MCP_URL` | 默认 `http://127.0.0.1:8300/mcp` |

## 回滚

1. `systemctl stop hutuji-cloud-bridge hutuji-mcp`  
2. 检出上一发布 commit / 恢复上一 venv  
3. 恢复对应 EnvironmentFile 与 nginx conf  
4. 再 `start`；跑 `preflight.sh` + 一次 `hutuji_status`  
5. 在 `docs/templates/release-manifest.md` 记回滚原因与时间  

详细命令见 `../deploy/README.md`。运维日常见 `operations.md`。
