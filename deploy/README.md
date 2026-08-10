# deploy/ — 京东云部署模板（已上线，模板可重建）

> 目标机：`117.72.118.95`（2核4G / 5Mbps，专机只跑 ③）。  
> 真实密钥、密码、`MCP_ENDPOINT` **禁止**写入本目录已跟踪文件。  
> 规范：`../docs/deployment.md`；值守：`../docs/operations.md`。

## 状态

| 项 | 状态 |
|----|------|
| 本目录模板 / 脚本 | ✅ 已预置（空机重建从 `prepare-host.sh` 起） |
| VPS 实机部署与验收 | ✅ 2026-08-11 已发布应用基线 `a5ae774`（第三轮评审两项 + 内存兜底，本轮 3 文件：`bitmap_svg.py`/`server.py` + `hutuji-mcp.service`；LF 归一化 scp 经 `/tmp/hutuji-deploy-a5ae774/`，四处落盘 md5 与 git blob 逐一对齐 `8c61321854f0e0c3aa7be9476fff7ec2`/`c1450bbb77f1b86c0f76a31cc6c7074e`/`ef084dc43c55544482575b2907d68805`×2，清 `__pycache__` 防旧 `.pyc` 复活，`daemon-reload` + 显式重启两服务，主进程 `02:43:45 CST`，两条 `ExecStartPre` self-test `status=0`，无 drop-in），生产 **`pass=22 fail=0 expected=22`**。本轮三项运行态判据另行实证：① `/proc/<MainPID>/cmdline` 确含 `--no-proxy-headers`（uvicorn 默认 `proxy_headers=True` 会用 `X-Forwarded-For` 改写 ASGI `scope["client"]`，而那是 `LoopbackOnlyMcpGuard` 的唯一判据；其信任面默认值取自环境变量，本 unit 带 `EnvironmentFile=`，故必须由命令行钉死）；② `systemctl show -p MemoryMax` 生效 `1073741824`（原 `infinity`；实测总内存 3911MB 无 swap、本服务 cgroup 历史峰值 174.5MB）；③ 公网伪造 `X-Forwarded-For: 127.0.0.1` 打 `/mcp` 仍 **404**，直连 `:8300` 连接被拒，而 loopback 真对端 `initialize` 回 **200**（桥接未误伤）。公网复验 `/mcp`、`/docs`、随机路径全 404。回滚副本 `/root/hutuji-bak-20260811-024307-pre-a5ae774-full/`（两份同名 service 已改名分存：`opt-deploy--`/`etc-systemd--`，md5 同为 `c60725e410af6460d29a4a4d832c0a80`）。同日追加 `2e63b3e`：bridge unit 加 `MemoryMax=256M`（实测峰值 59.7MB 的 4.3 倍；进程树 bridge 29.6MB + spawn 的 mcp-proxy 57.3MB 同属本 cgroup），并补上其**首个**形状测试（此前连 2026-08-11 加的 `ProcSubset=pid` 也无钉子）；落盘 md5 `175acabc98400995f3dcd5162081c702`×2，运行态 `MemoryMax` 生效 `268435456`，回滚副本 `/root/hutuji-bak-20260811-025206-pre-2e63b3e-bridge/`。**本次踩到一个坑并已写入上方注意事项**：只重启 bridge 导致 preflight `pass=21 fail=1`，因 `daemon-reload` 清空了 hutuji-mcp 的 `ExecStartPre` 记录（`code=(null)` 重置态）；补重启 hutuji-mcp 后记录恢复 `code=exited ; status=0`×2，回到 **`pass=22 fail=0 expected=22`**。上一基线 2026-08-11 `8c2e276`（评审修复六项，4 文件；生产 `pass=22 fail=0 expected=22`，主进程 `00:50:30 CST`，回滚副本 `/root/hutuji-bak-20260811-005007-pre-8c2e276/`）。


> 已上线机器**更新代码用 `scp`，不能 `git pull`**（`/opt/hutuji` 无 `.git`）；
> 更新顺序固定为：同步模板 → 重建 enabled 精确链接 → `nginx -t` → reload（**动了 `limit_req` 的 key/zone 必须改成 `restart`**）→ 当前 22 项 preflight 活动探针。旧 PASS 不能证明新脚本/配置。**scp 前必须 LF 归一化**（本地是 CRLF 检出，直传 `preflight.sh` 上机即 `set: pipefail: invalid option name`）。
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
5. `nginx -t && systemctl reload nginx`（**若本次改了 `limit_req` 的 key 或 zone，这里必须 `systemctl restart nginx`**——见上方「nginx reload 陷阱」，reload 会被 `[emerg]` 静默丢弃而 `systemctl` 仍报成功），成功后再执行 `bash /opt/hutuji/deploy/preflight.sh`（当前 22 项）。「生成链」项（`check_generate_chain`）会用生产 venv 真跑一次 `svg_to_gcode()`，「位图描摹链」项（`check_bitmap_chain`）会用生产 venv 以与 unit 相同的 `-m hutuji_mcp.bitmap_svg --self-test` 入口真跑一次离线描摹（skimage/sknw/imageio 是延迟 import，缺失时服务照样 active 而真实描摹才炸——必须单独验），故**必须先装好 venv 依赖**（vpype/vpype-gcode + scikit-image/sknw/imageio）并同步 `hutuji_mcp/hutuji_gwrite.toml`，否则对应项判 fail。它们运行在 systemd 服务外，不能覆盖 unit 的 syscall 沙箱；`hutuji-mcp.service` 的两条 `ExecStartPre=...gcode_gen/bitmap_svg --self-test` 必须在重启时另行通过，且 `SystemCallFilter` 只能按模板为 vpype 数值依赖放行 `mbind`。另：`check_units_match_templates` 除要求 unit 模板一致、已 `daemon-reload`、无 drop-in 外，还要求**两条** `ExecStartPre` 均已成功退出（逐条判 `status=0`，一条绿盖不住另一条红），且 MCP 主进程不早于本次部署的 unit、包内 Python 代码和 profile；只 reload 不 restart 会判 fail。`check_catchall_behavior` 要求 `:80` 兜底按 `$http_x_forwarded_proto` 分流（隧道回源 404、明文 301），无条件 301 即自指死循环、无条件 404 则丢掉明文强制 HTTPS。`check_nginx_running_config_is_live` 与 `check_nginx_workers_fresh` 再验「在跑的 nginx 真受理了这份配置」：前者要求带唯一标记的 loopback 探针出现在 `hutuji-access.log` 且行首为 `127.0.0.1 via=127.0.0.1`，后者要求所有 worker 不早于配置 mtime（`stat -L`）且启动后无 `[emerg]`。
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
