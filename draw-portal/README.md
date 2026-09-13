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
$env:PYTHONIOENCODING="utf-8"
$env:PYTHONUTF8="1"
D:\Users\hutuji\mcp-server\.venv\Scripts\python.exe -m pytest -q
node --test tests/weapp/*.test.js
```

小程序回归使用 Node 内置测试运行器和 wx 桩，覆盖登录/退出回包竞态、默认字体、扫码身份、UTF-8 TCP 分帧及切网/重扫/卸载恢复；测试放在 `tests/weapp/`，不进入小程序包。`test_device_identity.py` 用真实 P-256 签名验证正反例、挑战消费和 token 就绪；`test_device_bindings.py` 覆盖旧库迁移、多设备回写与事务回滚。`process_write_text` 的字体回归使用合成 gfont 和真实排版，验证显式字体对中文生效、未选字体仍沿用标准笔顺。完整交互与纸面效果另按真机清单验收。

## 量产机三轴调速

控制页分别设置 X/Y/Z；X/Y 默认 10000，Z 保留机器现值。`GET /api/settings/machine-speed` 从机器读三轴，POST 只提交 `{axis, rate, device_mac}` 指定轴；入口需手机号与设备归属校验。单轴写入前检查 S3 分轴能力，旧固件 409，避免忽略 axis 后误写 XY。确认必须核对 request_id、axis、rate 与三轴实际读回；断线或部分完成不提示成功。旧省略 axis 的 rate 接口仍调整 XY。普通重启保留 Grbl NVS 设置。

范围与事务见 [`protocol.md §10.4.21`](../docs/protocol.md)，量产部署与手机目视证据见 [`release-readiness.md`](../docs/release-readiness.md)。

## 生产部署（阿里云，一期）

照片精修的 `process_upload_sketch` 必须在 SVG 和 PNG 两处同步使用 `smooth=False`，保留渲染器给出的稀疏排线/波纹拐点；`test_photo_refine.py` 用真实 vpype 覆盖两种风格的导出几何。画质证据与未完成项见 [`../docs/design/photo-sketch-quality-20260912.md`](../docs/design/photo-sketch-quality-20260912.md)。

| 项 | 值 |
|---|---|
| systemd | `hutuji-draw-portal`（模板 `deploy/hutuji-draw-portal.service`） |
| env | `/etc/hutuji/draw-portal.env`（模板 `deploy/env.draw-portal.example`） |
| 端口 | `127.0.0.1:8302`，nginx 前缀 `/draw-upload/` |
| venv | **共用** `/opt/hutuji/mcp-server/.venv`（含 vpype/描摹链） |
| 数据 | `/var/lib/hutuji/draw-portal/`（SQLite + 用户域预览/G-code 副本） |

更新代码：`scp` 到 `/opt/hutuji/draw-portal/`，`systemctl restart hutuji-draw-portal`。

身份 v2 批次必须整体部署 `app/config/models/store/firmware_ota/device_identity` 模块及对应小程序工具/页面，先备份 SQLite，不可只拷接口而漏 Store/模型依赖。保持 `DRAW_PORTAL_DEVICE_IDENTITY_REQUIRED=0` 兼容尚未升级设备；已登记身份不允许降级。迁移与回滚顺序见 [`../docs/design/device-identity-rollout.md`](../docs/design/device-identity-rollout.md)。本地构建不代表已部署，状态只看 [`../docs/release-readiness.md`](../docs/release-readiness.md)。

## 用户流程要点

1. 登录后**默认进图库网格**（个人作品墙）；绑机收进顶栏「绑机」面板。
2. 右下角「+」打开面板（两个页签）：「上传图片」转换（>4MB 位图本地自动压缩，支持拖拽/粘贴），或「文字生图」——描述一句话转发本机 MCP `/internal/draw`（loopback + 共享密钥），AI 生成单线稿后走同一预览/发送/保存流。
3. 顶栏四色圆点可切换主题（墨 / 天 / 苔 / 暮），偏好存 `localStorage`。
4. **须在设备屏点「开始画」**（或说「开始画」）才落笔；`hutuji.draw` 不自动出图。
5. 新版小程序：扫设备配网码 → 选家庭 Wi-Fi、输入一次密码 → 系统切网、认领、校验连接并确认绑定；授权/重试保留整份二维码，密码不落本地存储。设备维护页 QR + 6 位码入口仍保留。
6. 「我的」可列出并切换本账号设备；切换只改变默认控制目标，解绑只影响目标设备，跨账号转让须设备的新证明与确认。

## messaging token 运维

绑定表内的 `device_token` 须为控制台「主题配置 / assets-generator」URL 的 `token=`（JWT `purpose=messaging`），**不是** NVS `websocket.token`。主题配置每次重开会作废旧 token；探活失败时可在设备上重新绑机，或运维脚本更新绑定表（值勿入库）。

身份 v2 不要求消费者提供 token：签名可覆盖空 token，由服务端自动铸造。复用旧 token 或铸新后均须探活，未就绪回 `503 binding_pending` 自动重试；历史探活标记不能作为成功依据。服务端控制台 JWT 仍须运维续期，不能宣称完全脱离小智平台凭据。

契约与设计：`docs/protocol.md` §10.4、`docs/design/draw-portal-bind.md`、`docs/design/conversation-capture.md`。

## 对话采集 + usage 埋点（双链）

| 链 | 入口 | 存储 |
|---|---|---|
| 对话 | 固件 `POST /api/device/conversation/report`（须 OTA） | `conversation_events` |
| usage | portal 页面动作 + MCP `POST /api/internal/usage` | `usage_events` |

环境：`DRAW_PORTAL_USAGE_INGEST_SECRET` 与 `HUTUJI_USAGE_INGEST_SECRET` **须同值**；MCP 默认 URL `http://127.0.0.1:8302/api/internal/usage`。运营查看：`https://hutuji.donglicao.com/draw-admin/`（8303，阿里云已启）。

## 网页文字生图（2026-08-31）

`POST /api/draw/generate {description}`（登录态）→ portal 持共享密钥调 MCP `POST /internal/draw`（仅 loopback + `X-Hutuji-Usage-Secret`，公网不可达）→ 按文件名契约从 loopback 基址取回 gcode/png 物化进 job 目录 → 与上传流同形发布。配额：成功单按 24h 窗口计 `web_generate` 事件，上限 `DRAW_PORTAL_GENERATE_DAILY_LIMIT`（默认 10），失败不占配额；错误码 400/429 透传白话，504/502 固定文案不回显内部细节。未配密钥该端点 503。

## 文档导入（2026-09-04）

「上传图片」页签同时接受 **Word（.docx）/ Excel（.xlsx）/ PPT（.pptx）/ CSV / TXT**（同 4MB 上限）：

- docx / pptx / txt → 抽文字走 `write_text` 文章链**写出**（与语音 `hutuji_write` 同参数同字库）；标题段（docx 标题样式 / pptx 标题占位符）18mm 放大 + 居中 + 免缩进 + 段前后空行，列表编号（1. / • / 一、等）自动还原；docx 内嵌表格只抽文字不画框线（v1 取舍）
- xlsx / csv → `table_layout` 表格链**画出**：格线 + 格内字同页笔画；列宽优先用文件自报 `<col width>`（奎享 `getColumnWidthInPixels` 同源），无则按内容测量；支持横向合并格、跨页自动重复表头、超宽自动缩字号（地板 3.5mm，再不行明确报错）；隐藏工作表跳过
- 多页：上限 20 页（设备 `kArticleMaxPages`），超限截断并在响应带 `truncated: true`；发送时整篇经 `hutuji.draw` 的 `pages` 参数下发（protocol §4.2，设备零改动），TTL 按页行数错峰（与语音写字链同一 `article_pages.page_ttl_offsets`）
- 解析/排版在 `hutuji_mcp`（`doc_ingest`/`table_layout`，纯 stdlib 解析：DOCTYPE/实体/加密/超限一律 400 具体原因）；需要 `DRAW_PORTAL_GFONT_DIR`（或复用 `HUTUJI_GFONT_DIR`）指向 .gfont 字库目录，**未配置时文档上传回 503**（图片/SVG 上传不受影响）
- 防滥用：ZIP 条目数/单部件 32MB/加密/DOCTYPE 拒绝；总文字 5000 字符、表格 500 行 × 24 列、单元格 40 字符封顶
