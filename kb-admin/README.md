# kb-admin — 小派售后知识库编辑后台（一期）

Loopback FastAPI 服务：SQLite 编辑 FAQ → 拼装 Markdown → 调用 `scripts/xiaozhi_console.py` 的 `update-kb` 推小智官方云知识库。规格见 `docs/superpowers/specs/2026-08-29-xiaopai-support-kb-admin-design.md`。

## 本地开发

```powershell
cd D:\Users\hutuji\kb-admin
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt

$env:KB_ADMIN_PASSWORD = "dev-secret"
$env:KB_ADMIN_SESSION_SECRET = "dev-session-secret"
$env:KB_ADMIN_DB = "data\kb_admin.db"
$env:KB_ADMIN_KB_ID = "71153"
$env:KB_ADMIN_PREFACE_PATH = "..\docs\xiaozhi-agent\kb-preface.md"
$env:KB_ADMIN_APPENDIX_PATH = "..\docs\xiaozhi-agent\kb-appendix.md"
$env:KB_ADMIN_IMPORT_MD = "..\docs\xiaozhi-agent\knowledge-base.md"
# 发布需要控制台 JWT（勿入库）
$env:XIAOZHI_TOKEN = "<console-jwt>"

.\.venv\Scripts\python -m uvicorn kb_admin.app:create_app --factory --host 127.0.0.1 --port 8301 --reload
```

浏览器打开 `http://127.0.0.1:8301/`。本地无前缀，**不要**加 `--root-path`。

测试（可用 mcp-server venv）：

```powershell
cd kb-admin
$env:PYTHONPATH = (Get-Location)
..\mcp-server\.venv\Scripts\python.exe -m pytest -q
```

## 环境变量

| 变量 | 必须 | 说明 |
|------|------|------|
| `KB_ADMIN_PASSWORD` | 是 | 登录密码 |
| `KB_ADMIN_SESSION_SECRET` | **生产必须** | HMAC 会话密钥；留空则每次重启随机生成、全员掉线 |
| `KB_ADMIN_DB` | 否 | SQLite 路径，默认 `data/kb_admin.db` |
| `KB_ADMIN_KB_ID` | 是 | 小智知识库 id |
| `KB_ADMIN_PREFACE_PATH` | 否 | 发布前言 Markdown |
| `KB_ADMIN_APPENDIX_PATH` | 否 | 发布只读附录 |
| `KB_ADMIN_IMPORT_MD` | 否 | 导入源 md |
| `XIAOZHI_TOKEN` | 发布时需要 | 控制台 JWT，**不是**设备消息 token |
| `KB_ADMIN_HOST` | 否 | 默认 `127.0.0.1`；**只允许 loopback** |
| `KB_ADMIN_PORT` | 否 | 默认 `8301` |

模板：`deploy/env.kb-admin.example`。

## 发布语义

`update_kb_from_path` 先上传再 `POST /knowledge-bases/document`。**内容未变**时小智会回 HTTP 400「文档已存在」——这是幂等成功，不是失败；控制台 JWT 过期才是 401。库路径失败抛 `XiaozhiConsoleError`（不再 `sys.exit`），后台发布记录应记下脱敏后的 `error_summary`。

## 生产部署（阿里云已启用；京东云一期不跑）

1. 将 `kb-admin/` 同步到 `/opt/hutuji/kb-admin/`，建 venv 并 `pip install -r requirements.txt`。
2. `cp deploy/env.kb-admin.example /etc/hutuji/kb-admin.env`，填实值，`chmod 600`。
3. `install -d -o hutuji -g hutuji /var/lib/hutuji/kb-admin`
4. `cp deploy/hutuji-kb-admin.service /etc/systemd/system/`，`daemon-reload`，`enable --now hutuji-kb-admin`。
5. 将 `deploy/nginx-kb-admin-snippet.conf` 并入 nginx 443 块，`nginx -t` 后 reload/restart。
6. 登录 `https://hutuji.donglicao.com/kb-admin/` → 导入 → 启用条目 → 发布 → 控制台 `recall` 抽测。

**路径前缀**：生产 unit 使用 `--root-path /kb-admin`；静态页 `fetch("api/…")` 为相对路径，本地 `/` 与 nginx `/kb-admin/` 反代均可工作。

## 回滚

```bash
systemctl stop hutuji-kb-admin
# 从备份还原 /opt/hutuji/kb-admin 与 /etc/hutuji/kb-admin.env
systemctl start hutuji-kb-admin
```

知识库正文回滚：用小智控制台或 `python scripts/xiaozhi_console.py update-kb <kb_id> <旧 md>` 推回上一版。

## SQLite 备份

编辑真源是 `KB_ADMIN_DB` 指向的 SQLite 文件。建议 cron 每日复制（示例）：

```bash
install -d -o hutuji -g hutuji /var/backups/hutuji-kb-admin
cp -a /var/lib/hutuji/kb-admin/data.db "/var/backups/hutuji-kb-admin/data-$(date +%Y%m%d).db"
```

保留 7–14 天；恢复时停服务 → 覆盖 db 文件 → 启动。
