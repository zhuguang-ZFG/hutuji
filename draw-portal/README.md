# draw-portal（买家绑机 + 网页传图）

公网入口：`https://hutuji.donglicao.com/draw-upload/`

独立 FastAPI 服务，与 MCP 生图链解耦：描摹与 `validate_gcode` 复用 `hutuji_mcp`（`PYTHONPATH=/opt/hutuji/mcp-server`），推送走小智 `/messaging/device/tools/call` → 设备侧 `hutuji.draw`。

## 本地开发

```powershell
cd D:\Users\hutuji\draw-portal
# 复用 mcp-server venv
$env:PYTHONPATH="D:\Users\hutuji\mcp-server"
$env:DRAW_PORTAL_SESSION_SECRET="dev-secret-change-me"
$env:DRAW_PORTAL_TOKEN_CIPHER_KEY="<Fernet.generate_key()>"
D:\Users\hutuji\mcp-server\.venv\Scripts\python.exe -m uvicorn draw_portal.app:create_app --factory --host 127.0.0.1 --port 8302 --root-path /draw-upload
```

测试：

```powershell
$env:PYTHONPATH="D:\Users\hutuji\mcp-server"
D:\Users\hutuji\mcp-server\.venv\Scripts\python.exe -m pytest -q
```

## 生产部署（阿里云，一期）

| 项 | 值 |
|---|---|
| systemd | `hutuji-draw-portal`（模板 `deploy/hutuji-draw-portal.service`） |
| env | `/etc/hutuji/draw-portal.env`（模板 `deploy/env.draw-portal.example`） |
| 端口 | `127.0.0.1:8302`，nginx 前缀 `/draw-upload/` |
| venv | **共用** `/opt/hutuji/mcp-server/.venv`（含 vpype/描摹链） |
| 数据 | `/var/lib/hutuji/draw-portal/`（SQLite + 用户域预览/G-code 副本） |

更新代码：`scp` 到 `/opt/hutuji/draw-portal/`，`systemctl restart hutuji-draw-portal`。

## 用户流程要点

1. 设备维护页「绑定呼图账号」→ QR + 6 位码 → 网页登录并确认绑定。
2. 上传图片 → 转换 → 「发送到小派」→ 设备屏出预览。
3. **须在设备屏点「开始画」**（或说「开始画」）才落笔；`hutuji.draw` 不自动出图。

## messaging token 运维

绑定表内的 `device_token` 须为控制台「主题配置 / assets-generator」URL 的 `token=`（JWT `purpose=messaging`），**不是** NVS `websocket.token`。主题配置每次重开会作废旧 token；探活失败时可在设备上重新绑机，或运维脚本更新绑定表（值勿入库）。

契约与设计：`docs/protocol.md` §10.4、`docs/design/draw-portal-bind.md`。
