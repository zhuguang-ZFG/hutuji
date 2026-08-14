# deploy/ — 京东云部署模板（已上线，模板可重建）

> 目标机：`117.72.118.95`（2核4G / 5Mbps，专机只跑 ③）。  
> 真实密钥、密码、`MCP_ENDPOINT` **禁止**写入本目录已跟踪文件。  
> 规范：`../docs/deployment.md`；值守：`../docs/operations.md`。

## 状态

| 项 | 状态 |
|----|------|
| 本目录模板 / 脚本 | ✅ 已预置（空机重建从 `prepare-host.sh` 起） |
| VPS 实机部署与验收 | ✅ **2026-08-14 应用基线推进 `5104b93`→`1082400`**（第十二轮 R12 修复，8 文件 `hutuji_mcp/{bitmap_svg,cloud_bridge,gcode_gen,grbl_sim,llm_svg,pipe_sim,server}.py` + `preflight.sh` 经 `/tmp/hutuji-deploy-1082400/` LF scp、落盘 md5 与 blob 逐一相符 0 CR、`bash -n`+生产 venv `py_compile` ×7 过、清 `__pycache__`、unit 未变故未 daemon-reload、显式重启两服务，主进程 2017026/2017028 `2026-08-14 13:57:52 CST`、两条 `ExecStartPre` `status=0`），生产 preflight 升至 **`pass=26 fail=0 expected=26`**（新增第 26 项 is-enabled 开机自启判据 + 8300 监听者 `ss -lntp` 比对 MainPID 身份核验 + `requirements.txt` 入两个 mtime 比较集，三判据首次真跑全绿）；生产 e2e PASS（14/14 行 ok）；真 draw「五角星」全链 228 行/5418B/4 抬笔，公网 URL `HTTP=200 bytes=5418` 复验；journald `Received session ID:` 已 `<redacted>`。回滚副本 `/root/hutuji-bak-20260814-135642-pre-1082400/`。<br>2026-08-13 应用基线 `26d220a`→`5104b93`（第九+十轮 11 产物 LF scp 上机、`daemon-reload` + 显式重启两服务，生产 `pass=25 fail=0 expected=25`；回滚副本 `/root/hutuji-bak-20260813-133527-pre-5104b93/`；详见 `../docs/release-readiness.md` 文首 2026-08-13 部署条）。<br>2026-08-12 同日另有三次发布当时未登记本表（第五轮产物 18:00 / 第六轮 `f3a5b94` 19:01 / 第七轮 `bb2529a` 21:38），证据见 `../docs/release-readiness.md` 文首各 2026-08-12 条与 `../docs/deployment.md` 补认条。<br>**2026-08-12 应用基线推进 `05786ce`→`e533f70`**（R3 位图取消不泄名额 / R4 桥接在途看门狗 / R15 pip 索引白名单 / R16 preflight 公网面探针 / 圆弧 R 弧 fail-closed；5 文件 `bitmap_svg.py`/`cloud_bridge.py`/`gcode_gen.py`/`preflight.sh`/`install-from-repo.sh` 经 `/tmp/hutuji-deploy-HEAD/` LF scp、落盘 md5 与 blob 逐一相符 0 CR、`bash -n`+`py_compile` 过、清 `__pycache__`、仅代码未动 unit 故只显式重启两服务，主进程 `2026-08-12 16:51:59 CST`、两条 `ExecStartPre` `status=0`），生产 preflight 升至 **`pass=25 fail=0 expected=25`**（含新增两项公网 HTTPS:443 `/files` 200 + `/mcp` 404），生产 16 个可执行/模板产物按 LF-md5 对齐 HEAD（16/16）。回滚副本 `/root/hutuji-bak-20260812-165011-pre-deploy-HEAD/`。<br>2026-08-11 已发布应用基线 `05786ce`（第二轮 11 条 P2 + 第三轮 5 条修复 + `server_tokens off`；本轮 6 文件：`bitmap_svg.py`/`llm_svg.py`/`cloud_bridge.py`/`preflight.sh`/`nginx-hutuji.conf`/`install-from-repo.sh`，LF 归一化 scp 经 `/tmp/hutuji-deploy-05786ce/`，落盘 md5 与 git blob 逐一相符、0 CR，`py_compile`/`bash -n` 过，清 `__pycache__`，`nginx -t`+reload 后 `daemon-reload`+显式重启两服务，主进程 `23:51:12 CST`），生产 **`pass=23 fail=0 expected=23`**；生产 18 个可执行产物按 LF-md5 逐一对齐 HEAD（18/18）。回滚副本 `/root/hutuji-bak-20260811-234834-pre-05786ce/`。**前一基线 `a5ae774`（2026-08-11 早些，22 项）**：第三轮评审两项 + 内存兜底，3 文件；`/proc/<MainPID>/cmdline` 含 `--no-proxy-headers`、`MemoryMax=1073741824` 生效，主进程 `02:43:45 CST`（详见 `../docs/deployment.md`）。真实基线序列 `f25d8d1`→`0db62f5`→`1f8ec57`→`a5ae774`→`05786ce`→`e533f70`→`5104b93`→`1082400`。 |


> 已上线机器**更新代码用 `scp`，不能 `git pull`**（`/opt/hutuji` 无 `.git`）；
> 更新顺序固定为：同步模板 → 重建 enabled 精确链接 → `nginx -t` → reload（**动了 `limit_req` 的 key/zone 必须改成 `restart`**）→ 当前 **26 项** preflight 活动探针。旧 PASS 不能证明新脚本/配置。**scp 前必须 LF 归一化**（本地是 CRLF 检出，直传 `preflight.sh` 上机即 `set: pipefail: invalid option name`）。**传输禁用保留 mtime 的选项**（`scp -p` / `rsync -t` / `cp -p`，DEP-06）：preflight 的「主进程不早于本次部署」判据依赖 mtime 比较，保留旧时间戳会让漏 restart 也全绿。
> preflight **无 skip 状态**（曾有的 `OPTIONAL_SKIP` 与 pass 等价放行，已删）：输出 `pass=N fail=N expected=N`，须 `PASS==EXPECTED_CHECKS && FAIL==0`；取不到非 loopback IPv4 判 fail 而非跳过。
>
> **`daemon-reload` 会清空所有 unit 的 `ExecStartPre` 执行记录**（2026-08-11 生产实测）。
> 只改了 bridge unit、于是只 `systemctl restart hutuji-cloud-bridge` —— preflight 立刻
> 从 22 掉到 **`pass=21 fail=1`**，失败项是「两个 unit 与模板一致，且已重启并通过 ExecStartPre」，
> 理由行为 `{ … --self-test ; start_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }`：
> 那是 systemd 的**重置态**，不是失败态。`daemon-reload` 重读全部 unit 时把
> hutuji-mcp 的两条自检记录一并清了，而它没被重启，于是「运行中的主进程确实跑过
> 生成链与位图链自检」这个证明在 systemd 状态里消失。判据判红是**对的**：它要的就是
> 这个证明，而证明确已不在。故规则是——**只要跑了 `daemon-reload`，就必须重启
> `hutuji-mcp`，哪怕本次一个 mcp 侧文件都没改**；重启后记录重新变成
> `code=exited ; status=0` ×2，preflight 即回到 22/22。反过来说：看到该项失败时先看
> 理由行里是 `code=(null)`（漏了重启）还是 `status=` 非 0（自检真的炸了），两者处置完全不同。
> 下面的「首次上线」流程保留给**重建/换机**，不是待办。
>
> **scp `nginx-hutuji.conf` 有两个坑**（2026-07-31 实测，详见 `../docs/deployment.md`「nginx 站点收敛」）：
> ① 本仓工作副本是 **CRLF**，上机后先 `sed -i 's/\r$//'`，否则与线上 md5 永远对不上（漂移判据用
> `tr -d '\r' … | md5sum`，部署时动态比较本仓与 enabled 文件）；
> ② 只 `cp` 到 `sites-available` **可能什么都没生效**——`nginx.conf` 只 include `sites-enabled/*`，
> 而 `sites-enabled/hutuji` 曾是**普通文件**而非符号链接。改完务必
> `readlink -f /etc/nginx/sites-enabled/hutuji` 确认精确指向 `sites-available/hutuji`，先
> `nginx -t && systemctl reload nginx`，再运行 preflight；否则 localhost `:80` 探针验到的仍是旧配置。
>
> **nginx reload 陷阱：改 `limit_req` 的 key/zone 必须 `restart`**（2026-08-08 生产实测 + 隔离复现）。
> nginx 不允许热改共享内存区的 key，master 会写
> `[emerg] limit_req "hutuji_files" uses the "$hutuji_client_ip" key while previously it used the "$binary_remote_addr" key`
> 然后**继续用旧配置服务**。三道常规检查全部看不见：`nginx -t` 起的是新进程、没有继承的区，必过；
> `systemctl reload` 走 `nginx -s reload`，只发信号不等受理，恒 `status=0/SUCCESS`；`is-active` 仍 active。
> 实测后果是跑了 37 分钟旧配置而当时 19 项 preflight **全绿**（其余判据在旧配置下同样成立）。
> 现由第 16–17 项覆盖，手工核查则看两处：`ps -o lstart= -p <worker>` 是否新于配置 mtime（`stat -L`，
> 不解引用会读到 `ln -sfn` 的时刻），以及带唯一标记的 loopback 探针是否真的落进 `hutuji-access.log`
> （旧配置没有该 `access_log`，请求会落到 http 级默认 `/var/log/nginx/access.log` 的 combined 格式）。

## 文件

| 文件 | 用途 |
|------|------|
| `prepare-host.sh` | **上线前**：建用户/目录/空 env 壳、打印安全组要求 |
| `install-from-repo.sh` | 代码到位后：venv + systemd + nginx 站点 |
| `preflight.sh` | 本机冒烟（HTTPS 前缀、loopback、活动 nginx、下载长度/CRC32、未公网听 8300） |
| `env.mcp.example` | → `/etc/hutuji/mcp.env`（LLM + `/files` 相关） |
| `env.bridge.example` | → `/etc/hutuji/bridge.env`（仅 `MCP_ENDPOINT`） |
| `hutuji-mcp.service` | uvicorn 固定 `127.0.0.1:8300` |
| `hutuji-cloud-bridge.service` | 小智 WSS 桥接 |
| `nginx-hutuji.conf` | 仅 `/files`；显式 404 `/mcp` |

## 阶段 0：主机准备（密钥未齐也可先做）

在空机或待清理机上（root）：

```bash
# 1) 把本仓同步到 /opt/hutuji（git clone / rsync，勿带本地 .venv）
# 2) 准备目录与用户
bash /opt/hutuji/deploy/prepare-host.sh
# 3) 京东云控制台：安全组只放 22/80/443，确认无 8300
```

做完即「为未来上线留好坑位」；此时**不要** `enable --now` 业务 unit（env 仍是 REPLACE_ME）。

## 首次上线

1. 填 `/etc/hutuji/mcp.env`、`bridge.env`（`chmod 600`）。  
2. 放 TLS 到 `/etc/nginx/ssl/hutuji/{fullchain,privkey}.pem`（或改 nginx 路径）。  
3. `bash /opt/hutuji/deploy/install-from-repo.sh`  
4. `systemctl enable hutuji-mcp hutuji-cloud-bridge nginx && systemctl restart hutuji-mcp hutuji-cloud-bridge && systemctl start nginx`。必须显式 `restart`：更新时服务通常已经 active，`enable --now` 不会重启，新增的 `ExecStartPre` 也就不会执行。
5. `nginx -t && systemctl reload nginx`（**若本次改了 `limit_req` 的 key 或 zone，这里必须 `systemctl restart nginx`**——见上方「nginx reload 陷阱」，reload 会被 `[emerg]` 静默丢弃而 `systemctl` 仍报成功），成功后再执行 `bash /opt/hutuji/deploy/preflight.sh`（当前 26 项）。「生成链」项（`check_generate_chain`）会用生产 venv 真跑一次 `svg_to_gcode()`，「位图描摹链」项（`check_bitmap_chain`）会用生产 venv 以与 unit 相同的入口真跑一次位图描摹自检。
6. 公网：`/mcp` 须 404；**`/docs`、`/redoc`、`/openapi.json` 与任意随机路径也须 404**（历史上它们是自指 301 环，见 `../docs/deployment.md`）；直连明文 `http://117.72.118.95/files/*` 须 403 而其他路径须 301；`/files/` 只经 HTTPS。
7. 小智控制台 `tools/list`（阶段 6）；证据写入 `docs/templates/release-manifest.md`。

## 清理旧环境（若机上曾装过杂物）

```bash
systemctl disable --now hutuji-mcp hutuji-cloud-bridge 2>/dev/null || true
# 确认无其它业务占用后再删；output/ 可清，密钥文件勿进 git
rm -rf /opt/hutuji/mcp-server/output/*
# 保留 /etc/hutuji/*.env（若已填好）；否则按 example 重建
```

验收铁律：公网不能访问 `/mcp`；8300 不进安全组；明文 `:80 /files/` 只允许 loopback Tunnel 回源。应用层另有 `server.LoopbackOnlyMcpGuard` 按 socket 对端兜底（非 loopback 对端访问 `/mcp` 回 404），但它**只兜控制面**——`/files` 一旦绕开 nginx 就没有限速与 ACL，故上面三条一条都不能省。
