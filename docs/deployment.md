# 京东云部署

> ③ MCP 服务**唯一**生产落点：京东云 VPS。模板在 `../deploy/`。真实凭据不入库。  
> **当前**：生产服务已常驻并实机验收；最新 MCP 校验器发布为 `b50a10a`（2026-07-29），
> 证据见 `release-readiness.md`。
> **2026-08-09 layout 归一化发布（本仓 `3126a84`）**：`gcode_gen.py` scp（LF，md5 `a61be0bf1adec2076994aae495f4b615` 对账一致）→ 显式 `restart hutuji-mcp` → preflight **21/21 PASS**（含生产 venv 真实生成链）。生产真 draw「五角星」：`lines=19 bytes=364`，产物 bbox X[10.0,180.0]、Y 中心 138.5 = 画布正中——归一化在生产生效。回滚副本 `/root/hutuji-bak-20260809-182426-pre-layout/`。
> **2026-08-09 位图路线发布（本仓 `4be934b`）**：`hutuji_draw` 切到「agnes 文生图 → sknw 中心线描摹 → 单线 SVG」。scp `bitmap_svg.py`（新）/`config.py`/`server.py`/`requirements.txt`（LF），生产 venv 经清华镜像装 `scikit-image 0.26.0`/`networkx 3.6.1`/`sknw 0.15`/`imageio 2.37.4`（**直连 PyPI 会挂起 17 分钟无子进程，镜像 75s 装完**）→ 显式 `restart hutuji-mcp` → preflight **21/21 PASS** → 生产真 draw「一朵五瓣的花」：709 行/16840B/22 抬笔/23.4s，公网 URL 200 且字节数与工具上报一致。回滚副本 `/root/hutuji-bak-20260809-184140-pre-bitmap/`。注意：`/etc/hutuji/mcp.env` 未改（`HUTUJI_IMAGE_MODEL` 默认值即生产值）。
> **2026-08-10 位图自检封闭性收口发布（本仓 `f25d8d1`）**：`bitmap_svg.py`/`preflight.sh` 经 `git show HEAD:` 上传（md5 `cb18fb779084a7ef3d32666bf0772448`/`36e4823dedee94c564d88a3df7062acc` 逐一相符、0 个 CR、`py_compile`/`bash -n` 过）→ 显式 `restart hutuji-mcp`（01:59:14 CST，bridge 与 unit 未改不动）→ 两条 ExecStartPre 均 `status=0`（1024px ⊥ 探针在沙箱内真跑，restart 全程 ~9s，远在 `TimeoutStartSec=30` 内）→ 生产 **22/22 PASS**（改名后的第 3 项「mcp.env 无占位、LLM 完整、image_model 非空白且公网前缀为 HTTPS:443」在真实 mcp.env 下过）→ 公网 `/mcp`/`/files/nope`/`/docs`/随机路径全 404。回滚副本 `/root/hutuji-bak-20260810-015828-pre-f25d8d1/`。生产应用基线现为 `f25d8d1`。

## 目标机

| 项 | 值 |
|----|-----|
| 公网 | `117.72.118.95` |
| 规格 | 2 核 4G / 5Mbps |
| 上行实测 | **2026-07-26**：公网拉 512KiB 中位 **1.13s / ~3.72 Mbps**（本机→`http://117.72.118.95/…`，3 轮；测完文件已删） |
| 角色 | 专机只跑 ③（uvicorn + 小智桥接 + nginx） |
| 冗余 | 无；单点 |
| 实机业务 | `hutuji-mcp` + **Cloudflare Tunnel HTTPS**（Let's Encrypt） + `hutuji-cloud-bridge` 已连小智 WSS；`HUTUJI_PUBLIC_BASE_URL=https://hutuji.donglicao.com`；**2026-08-07 起绘图 LLM = Agnes `agnes-2.5-flash`**（此前 llm7 为历史配置） |

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
                                               │
                                               └─ `LoopbackOnlyMcpGuard` 按 socket 对端复核；非 loopback/未知对端 404
```

> 应用层守卫不替代 unit 的 `--host 127.0.0.1`、安全组不放行 8300、nginx `/mcp` 404：它只兜 `/mcp` 控制面，不能阻止 `/files` 绕开 nginx 限速与 ACL。该守卫已于 2026-08-09 随 `8184860`（守卫本体 `19d1df1`）部署生产，`server.py` md5 `60801395b0ba7d70bf0322fa173d8619`，21 项 preflight `pass=21 fail=0 expected=21`；行为面实测：loopback `initialize` 200；**非 loopback 向量必须在同一份已部署代码的临时实例上取证**——生产 unit 只绑 `127.0.0.1:8300`，非 loopback 对端连不上（`curl` 回 `000`，这是监听面而非守卫的功劳），故用 `--host 0.0.0.0 --port 8399` 起同一个 `server.py`、探完即杀（`ss` 复核 8399 已消失、`0.0.0.0` 监听面仍只有 80/443/22）：同机非 loopback 对端（`curl --interface 172.16.0.4`）伪造 `Host: 127.0.0.1:8399` 打 `/mcp` 得 404 且正文恰为 9 字节 `not found`（守卫路径）、同一对端打 `/files/nope.gcode` 得 404 但正文 `{"detail":"not found"}`（应用路径，未被守卫拦）、公网 HTTPS `/mcp` 仍 404。回滚副本 `/root/hutuji-bak-20260809-020732-pre-8184860/server.py.a939eb67`。

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
- [x] `mcp.env` 已填（Agnes `agnes-2.5-flash`；密钥仅在机上 600 文件）；TLS 自签已放 `/etc/nginx/ssl/hutuji/`
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
| `mcp.env`（Agnes `agnes-2.5-flash`） | ✅ 2026-08-07 | 本地完整链生成 1336B SVG、286 行/6891B G-code；生产真实链在“前两次 15s ReadTimeout、第三次成功”下总耗时 39.87s，生成 296 行/6870B G-code。请求限制 `max_tokens=1200`，网络错误及 `429/500/502/503/504` 最多重试两次（1s/3s）；当前协议消费 SVG 文本，故不用位图模型 |
| Cloudflare Tunnel HTTPS | ✅ | `https://hutuji.donglicao.com/files/…` 经隧道到 nginx；Let's Encrypt 证书 |
| `hutuji-mcp` | ✅ | `systemctl active`；`127.0.0.1:8300` |
| `hutuji-cloud-bridge` | ✅ | WSS 已连接；云端 `initialize` + `tools/list` 已进日志 |
| `preflight.sh` 全 PASS | ✅ 2026-08-09 生产 **21/21**（基线 `8184860`，含 `19d1df1` 的 `/mcp` loopback 守卫） | `pass=21 fail=0 expected=21`。本轮只需发 `server.py` 一个文件（安装后 md5 `60801395b0ba7d70bf0322fa173d8619`、mode 644、0 个 CR），清 `__pycache__` 后显式重启两个应用服务，`ExecStartPre` 自检 `status=0`，`ss -ltnp` 仍只有 `127.0.0.1:8300`；回滚副本 `/root/hutuji-bak-20260809-020732-pre-8184860/server.py.a939eb67`。第 16 项「nginx 活动配置真的在跑」（带 marker 的 loopback 探针必须落进 `hutuji-access.log` 的 `hutuji_client` 自定义格式，行首 `127.0.0.1 via=127.0.0.1 `）与第 17 项「worker 新鲜度」（无早于 `stat -L` 配置 mtime 的 worker；worker 启动后无 `[emerg]`）自 2026-08-08 起在生产通过；同批还首次验真了 `check_units_match_templates` 的 ExecStartPre 成功结果与主进程新鲜度判据。**历史 `pass=21`（2026-08-08 `25ff33d`）/ `pass=19`（2026-08-07）/ 17/17 / 15/15 均已 supersede，不得复用为放行依据**——19 项在「nginx reload 被静默丢弃、旧配置继续服务 37 分钟」时依然全绿。输出格式 `pass=N fail=N expected=N`，放行判据 `PASS==EXPECTED_CHECKS && FAIL==0`（脚本已删 skip 状态，禁止重新引入 `OPTIONAL_SKIP`）。**scp 前必须 LF 归一化**，否则 `preflight.sh` 上机即 `set: pipefail: invalid option name` |
| 小智控制台工具可见 | 🔬 | 控制台刷新后应见 `hutuji_draw` 等 |
| 小智知识库 | ✅ | 用户已上传 `docs/xiaozhi-agent/knowledge-base.md`（2026-07-26）；请确认智能体页已**关联** |

> TLS 已切 Cloudflare Tunnel + Let’s Encrypt（2026-07-27），ESP-IDF cert bundle 信任，S3 无需 `-k`。  
> `install-from-repo.sh` 默认清华 HTTPS PyPI 源（京东云直连 PyPI 易卡住），不再使用 `--trusted-host` 绕过 TLS；私有 CA 场景显式传 `HUTUJI_PIP_CERT`。

> **systemd 生成链边界（2026-08-07 定案，2026-08-08 已部署验真；2026-08-09 扩到位图描摹链，已随 `7aab460` 部署）**：preflight 的 `check_generate_chain` 在服务外验证生产 venv、profile 与 §5 校验器，但不能证明同一程序能穿过 unit 的 seccomp。生产曾出现 preflight 19/19 与真实 vpype `SIGSYS` 同时成立；内核审计定位到 `mbind`。`hutuji-mcp.service` 用 `ExecStartPre=...python -m hutuji_mcp.gcode_gen --self-test` 在服务沙箱内真跑 vpype/profile/§5 自检，`SystemCallFilter` 仅从原 `@resources` 禁用组中放行 `mbind`。**2026-08-09 仓内再加第二条 `ExecStartPre=...python -m hutuji_mcp.bitmap_svg --self-test`**：位图描摹重依赖（skimage/sknw/imageio）是延迟 import，缺失时 import/active/生成链自检全绿而真实描摹才炸（本机拦掉 imageio 实测复现）；自检用纯 stdlib zlib 合成的 T 形探针 PNG 真跑描摹全链，不碰计费生图 API，同时验证 skimage/scipy 数值栈能穿过 seccomp（vpype mbind 同款风险面）。发布后必须 `daemon-reload` 并**显式 restart** 两个应用服务；`enable --now` 对 active 服务不会重启。当前 22 项中的 unit 检查会**逐条**读取两条 ExecStartPre 的实际成功结果（整段子串匹配会让一条绿盖住另一条红），并要求 MCP 主进程不早于已部署 unit、包内 Python 文件和 profile；只 reload 未 restart 会判 fail。gcode 自检部分已随 `25ff33d` 部署（2026-08-08 生产 `pass=21 fail=0 expected=21`）；**bitmap 自检部分已随 `7aab460` 于 2026-08-09 部署**：三产物 md5 与 HEAD blob 逐一相符、0 个 CR，回滚副本 `/root/hutuji-bak-20260809-225123-pre-7aab460/`，显式重启（MCP 主进程 22:52:26）后两条 ExecStartPre 均 `status=0`（bitmap 自检实跑 5 秒，证明 skimage 描摹在沙箱内真执行、无 SIGSYS），生产 `pass=22 fail=0 expected=22`，公网 `/mcp`/`/files/nope`/`/docs`/随机路径复验全 404。

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

> **2026-08-08 `25ff33d` 已部署（supersede 上方全部「仓内未部署」与历史 pass 数）**：实际范围大于本批五修——生产 `server.py`/`gcode_gen.py`/`cloud_bridge.py` 都与 HEAD 有差、`sim_server.py` 缺失，等效 `69c2198` → `25ff33d`。`requirements.txt` 两端相同（`0b4629800c6f4bc7ea51f7f98028e8c6`），未装依赖。备份 `/root/hutuji-bak-20260808-204725-pre-25ff33d/`（`hutuji_mcp/`、`deploy/`、`nginx-hutuji.conf`、`units/`）。8 文件经 `git show HEAD:` 管道进 `/tmp/hutuji-stage/`，md5 与 HEAD blob 逐一相符、0 个 CR、`py_compile` + `bash -n` 通过；装后 md5：`config.py b3d70811cb7d630bc7dd0fc2da246bf2`、`llm_svg.py 1755860ae3c6ebb8d77914ca847b35ae`、`server.py a939eb676716923d47789ca813b4bd4c`、`gcode_gen.py 4211d365f68b25cf7fbed9c64bb4784e`、`cloud_bridge.py e2ab68d37cb03c9bfec026e47d990ed2`、`sim_server.py 35e175329b048154a1aa86d0a9d48201`、`preflight.sh 4f71860303268313bac36a2286be44ec`、nginx `9f71eacd21e85a4f19fcb1474bbfee53`。`daemon-reload` + 显式 restart（`hutuji-mcp` 20:54:41、`hutuji-cloud-bridge` 20:54:44）、ExecStartPre 自检 `status=0`、loopback `/mcp` 406（预期）。**首轮 preflight `pass=19 fail=0 expected=19` 是假绿**：同一时刻的 `nginx reload` 已被 master 静默丢弃（见下条），旧配置又继续服务了 37 分钟。修复后加固到 21 项，复跑 **`pass=21 fail=0 expected=21`**。公网复验 fix #2：真产物 CRC `775b4f31`/243B，HTTPS 200 且逐字节相符，日志 `123.202.***.*** via=127.0.0.1 … "GET /files/draw_….gcode" 200 243`（客户端 IP 末两段脱敏，实测值与 ipify 返回的本机公网 IP 一致）——`$hutuji_client_ip` 是真实公网客户端 IP、`via=$remote_addr` 是 cloudflared loopback。未改固件、未做硬件验证、未提升出货类判据。
>
> **`limit_req` 键变更无法 reload（2026-08-08 实测并已修）**：把 `/files` 限流键从 `$binary_remote_addr` 改成 `$hutuji_client_ip` 后 `nginx -t` 通过、`systemctl reload nginx` 退出 0、`is-active` 仍 active、19 项 preflight 全绿，但 `error.log` 里是 `[emerg] limit_req "hutuji_files" uses the "$hutuji_client_ip" key while previously it used the "$binary_remote_addr" key`——nginx 不能在 reload 时改共享内存 zone 的键，master **丢弃新配置、继续用旧配置服务**。旁证：worker 时间戳仍是前一天 17:31、没有 worker 持有 `hutuji-access.log` 的 fd、探针落进 http 层默认 `access.log`。已在独立 nginx 实例（`/tmp/nginx-repro`，用后清理）复现全部四个特征，含 reload 前后 PID 不变。处置：确认 `:80/:443` 无 established 连接、无在途 `/files`、`nginx -t` 通过、备份在位后 `systemctl restart nginx`（master `1055773`、worker `1055774`/`1055775`，`Sat Aug 8 21:31:57 2026`）。**规则**：改 `limit_req`/`limit_conn` 的键或 zone 名必须 `restart`，不能只 `reload`。新增第 16/17 项就是为了机器化这条：探针必须落进自定义日志格式，且 worker 不得早于 `stat -L` 读出的配置 mtime（`sites-enabled/hutuji` 是 `ln -sfn` 建的符号链接，不加 `-L` 会读到建链时刻——实测 `1786095075`=08-07 17:31:15 vs `1786193641`=08-08 20:54:01，整整差一天）；`[emerg]` 的比较基准取**当前最老 worker 启动时刻**而非配置 mtime，否则已被 restart 解决的历史 `[emerg]` 会永久红。
>
> **当时的仓内 preflight 修复已部署（2026-08-04，supersede 2026-08-01“未部署”状态）**：此前 `f3ef14b`/`8e4b9b4` 引入的变量初始化顺序回归，以及随后补齐的 `[skip]`、`ss` 前置、curl 超时/禁代理、本机 5xx 不算可达等硬化，已随 hutuji `f59c0a0` scp 到 `/opt/hutuji/deploy/preflight.sh`。生产当场复验 `pass=14 fail=0 skip=0`，生产脚本 SHA-256 `7196aa9efa13ccc839bf4b2e59294aee14142e062659efb44fab9a669ae8dc97` 与提交对象一致；回滚副本 `/root/hutuji-bak-20260804-f59c0a0/`。该证据只覆盖当时 14 项版本。
>
> **2026-08-05 `bdbb107` 已部署（supersede “仓内新增检查，尚未部署”）**：先备份到 `/root/hutuji-bak-20260805-bdbb107/`，再经 `/tmp/hutuji-deploy-bdbb107/` 统一 LF、`py_compile`/`bash -n` 后安装 `server.py`、`gcode_gen.py`、`preflight.sh`、`install-from-repo.sh`，重启 `hutuji-mcp` 与 `hutuji-cloud-bridge`。四文件 SHA-256 分别为 `c9fe792d…72ea8`、`9eb9d5d8…941a`、`2f26bd76…77cf`、`61e7bcf0…2327`，与本地候选一致。生产 preflight `pass=15 fail=0 skip=0`；生产 e2e `PASS`（14 行/242 字节）；四服务均 active；bridge 重连后 `initialize`、`tools/list`、`ping` 成功且 URL 脱敏；经运行中 `/mcp` 调用 `hutuji_status` 返回 `output_dir_exists=true`、公网基址正确、LLM 已配置、latest=`*.gcode`。开发机站外复验：HTTPS `/mcp` GET/POST 均 404、直连 `:80 /files` 403、`:8300` 为 `000`/连接超时。未改固件、未做硬件验证。
>
> **2026-08-05 深度审查修复（仓内，未部署）**：`deploy/preflight.sh` 五项硬化——①活动 ACL 判据原用 `grep -Fq 'deny all;'`，分不清 `/files/` 块内的兜底 deny 与 `limit_except GET HEAD { deny all; }` 行内那条；删掉兜底 deny 后 nginx 只剩两条 allow、默认放行、明文 `:80 /files` 对全公网可读，而 grep 命中 limit_except 那行仍报 pass（用脚本原样管道实测复现）。现先剔除 limit_except 行、只认独立成行的兜底 deny，并断言两条 allow 必须排在 deny 之前（nginx allow/deny 按出现顺序匹配，deny 提前会先拒 cloudflared 的 loopback 回源）。②**删除 `OPTIONAL_SKIP(125)` 机制**：放行算术曾是 `(PASS+SKIP)==EXPECTED_CHECKS`，skip 与 pass 完全等价，于是「公网访问 `:80 /files` 被拒」可以从未被验证过、脚本照样打印 15/15 全绿（实测 `pass=14 fail=0 skip=1` 输出 PASS）。取不到非 loopback IPv4 时现在**判 fail**，由人确认，不得由脚本自行豁免。③`HUTUJI_PUBLIC_BASE_URL` 拒 RFC1918 与单标签主机（`https://192.168.1.5`、`https://10.0.0.8`、`https://intranet` 原本全报 pass）。④`/mcp` 可达谓词从 `^[234]` 收紧——MCP 子应用挂载失败时裸 POST 返回 404，原判据会把「端点已不存在」报成 pass。⑤输出格式改为 `pass=N fail=N expected=N`，放行判据 `PASS==EXPECTED_CHECKS && FAIL==0`。另：`nginx-hutuji.conf` 的 `:80` 兜底 301 从 `$host` 改字面量（两个 listen 都无 `default_server`，任意 Host 头都落本块并被回显进 Location，构成开放重定向）；`install-from-repo.sh` 去掉 `bash -lc` 的 `-l` 并 `env -u` 清掉 `PIP_*`/代理变量（root 登录期配置导出的 `PIP_TRUSTED_HOST`/`PIP_EXTRA_INDEX_URL` 会静默盖过脚本只传的 `-i`/`--cert`，推翻「禁止绕过 TLS 校验」的承诺）；两个部署脚本的 chown 顺序改为先收代码树、不动 output 属主；两个 unit 补 `ProtectKernel*`/`RestrictAddressFamilies`/`SystemCallFilter`，bridge 的 `StartLimitIntervalSec`/`StartLimitBurst` 从 `[Service]` 移到 `[Unit]`（原位置被 systemd 静默忽略）；`requirements.txt` 五个无上界依赖补上界。本地 hub `overall=pass`（pytest **262 passed**、e2e `PASS`）；ACL 与 301 两处修复均做过变异验证（删兜底 deny / 改回 `$host` 后 preflight 判据与新增 pytest 断言都转 fail，改完即恢复）。**未部署生产、未改固件、未做硬件验证**——生产仍是 `bdbb107`。
>
> **2026-08-06 `69c2198` 已部署（supersede 下方两条「仓内未部署」）**：**全量对齐**，13 文件一次发齐——生产原为 `bdbb107`，落后一批「已提交未部署」的积压：`preflight.sh`(15→17)、`gcode_gen.py`、`cloud_bridge.py`、`llm_svg.py`、`grbl_sim.py`、`pipe_sim.py`、`hutuji_gwrite.toml`、两个 unit、`nginx-hutuji.conf`、两个安装脚本、`requirements.txt`。profile 的落笔 F1000→F10000 **会改变纸面笔迹**，已获用户授权。流程：备份 `/root/hutuji-bak-20260806-69c2198/`（36 文件）→ `/tmp/hutuji-deploy-69c2198/` 上传（md5 与本地 git blob 逐一相符，字节级复核 0 个 CR）→ `bash -n` ×3 + `py_compile` ×5 → 核对现装依赖满足新上界（未重装）→ `install`（`output` 属主始终未动）→ 装 unit 与 nginx conf → **`daemon-reload`** → `nginx -t` → `reload` + 重启两服务。验证：preflight **17/17**（「生成链」与「unit 一致性」两项**首次在生产通过**）、生产 e2e `PASS`（14 行/243 字节）、四服务 active、`/run/hutuji/cloud-bridge.ready` == MainPID、站外 404/404/403/000、活动 301 目标为字面量域名。已推送 `fa0ce13..69c2198`。**未改固件、未烧录、未做硬件验证**；新 profile 下的纸面笔迹未经目视验证。
>
> **2026-08-05 补修两项（同批，仓内未部署；16→17 项）**：①**§5 字节口径**：`_read_generated_gcode` 用 `read_text()` 读产物，CRLF 被 universal newlines 塌成 LF，每行少算 1 字节；S3 下载到的与 `Content-Length`/CRC32 算的都是磁盘原始字节。实测某 20846 行 CRLF 产物磁盘 524369 字节（超 512KiB 上限 81 字节）被判未超并放行，`bytes` 少报 20846。改为 `read_bytes()` + `validate_gcode(text, total_bytes=len(raw))`；另修一条原本正钉着错口径的既有测试。②**unit 一致性（`check_units_match_templates`）**：`is-active` 不证明跑的是哪一版 unit，加固项可全缺而照样 active；两个 unit 与 nginx 安装方式同构却只有 nginx 有一致性判据。现对两个 unit 各查 LF md5 一致、`NeedDaemonReload=no`、`FragmentPath` 指向 `/etc/systemd/system/`、无 drop-in（glob 判定；`DropInPaths` 在老版 systemd 返回空串，只信它是 fail-open）。**部署提醒**：本项要求 scp 后必须 `systemctl daemon-reload`，否则即便文件一致该项也判 fail——这正是它要抓的场景。`hub overall=pass`（pytest **292 passed**、e2e `PASS`）；变异验证 4/4（字节口径）+ 5/5（unit 判据）。**未部署，生产仍 `bdbb107`。**
>
> **2026-08-05 preflight 生成链盲区修复（仓内，未部署；14→15→16 项）**：此前 15 项**没有任何一条执行过部署的 `hutuji_mcp` 代码**——逐条核过每个 `python3` 调用，只用到 `urlsplit`/`secrets`/`zlib`。下载探针用 `printf "G21\nG90\nF8000\n"` 人工拼产物，而那份内容连 `validate_gcode` 自己都不接受（实测报「第 4 行缺少 §5 文件头 `G1G90 Z0.0F10000`」），所以下载项只证明了「nginx 能把一个文件按字节发出去」。三处实测复现：①把 `hutuji_mcp/hutuji_gwrite.toml` 移走后，`import hutuji_mcp.server` 依然成功、服务保持 active、`/mcp` 与 `/files` 照常响应，而真实 `svg_to_gcode()` 报 `vpype 转换失败（exit 2）: --config … does not exist`——即 `pass=15 fail=0` 与「一画就整单失败」可以同时成立；②把 profile 的 `document_start` 首行 `F8000` 改 `F6000`，生成链 fail-closed 于 `第 3 行不是精确的 §5 文件头 F8000（实际：F6000）`，但同样只在请求期暴露；③`vpype` 是 subprocess、**从不 import**，依赖装坏与 profile 缺失一样逃过 import 与 `systemctl active`。根因是 `_PROFILE_PATH` 只被请求期的 `_vpype_command()` 使用，import 期不加载不校验。修复：新增 `check_generate_chain()`，用**生产 venv 的解释器**（`/opt/hutuji/mcp-server/.venv/bin/python`，不能换系统 `python3`——`_vpype_cmd()` 按 `sys.executable` 同目录定位 vpype，换解释器即换掉被验对象）真跑一次最小 SVG 的 `svg_to_gcode()`，产物落 `mktemp -u`、不污染 `/files`，并要求 `lines>0 && bytes>0`（空产物也是坏产物）；原下载项改为先用同一条链生成**真产物**再验 200/长度/CRC32/正文全等，于是 profile、vpype 依赖、生成器、§5 校验器与下载链一次覆盖。`EXPECTED_CHECKS` 15→16。变异验证：移走 profile 与改 `document_start` 的 `F8000`→`F6000` 两种变异下，探针判据均从 exit=0 转 exit=1，还原后即恢复（profile blob 哈希已与 HEAD 逐字节核对一致）。本地 hub `overall=pass`（pytest **287 passed**、e2e `PASS`）。**未部署生产、未改固件、未做硬件验证**——生产仍是 `bdbb107`（15 项版本，不含生成链项）。


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
| `mcp.env` | `HUTUJI_HOST/PORT` | **不是**监听面真值（unit 的 `--host/--port` 才是）；`HUTUJI_HOST` 非 loopback 会在 import 期抛错，但 uvicorn CLI 可另绑 `0.0.0.0`。仓内候选另由 `LoopbackOnlyMcpGuard` 按 socket 对端把非 loopback/未知 `/mcp` 请求塌成 404；仍不可省略 unit loopback 监听 |
| `mcp.env` | `HUTUJI_PUBLIC_BASE_URL` | HTTPS 公网前缀（S3 下载） |
| `mcp.env` | `HUTUJI_LLM_*` | LLM |
| `mcp.env` | `HUTUJI_OUTPUT_*` | 产出目录与 TTL |
| `bridge.env` | `MCP_ENDPOINT` | 仅桥接；小智控制台复制 |
| `bridge.env` | `HUTUJI_LOCAL_MCP_URL` | 默认 `http://127.0.0.1:8300/mcp` |

## 回滚

1. `systemctl stop hutuji-cloud-bridge hutuji-mcp`  
2. 检出上一发布 commit / 恢复上一 venv  
3. 恢复对应 EnvironmentFile 与 nginx conf；若回滚会改回 `limit_req` 的键或 zone 名，必须 `systemctl restart nginx`，`reload` 会被静默丢弃
4. 再 `start`；跑 `preflight.sh`（须 `pass=22 fail=0 expected=22`）+ 一次 `hutuji_status`
5. 在 `docs/templates/release-manifest.md` 记回滚原因与时间  

详细命令见 `../deploy/README.md`。运维日常见 `operations.md`。
