# hutuji ③ MCP 生成服务

语音 → 小智官方云 LLM → 调 MCP 工具 `hutuji_draw(自然语言)` → 本服务：
LLM 生成单线笔画 SVG → vpype/vpype-gcode 转 G-code（§5 契约校验）→ 存本地并经 HTTP 暴露 URL
→ 返回 URL 给云端 →（后续）S3 板经 `hutuji.draw` 下载转发给写字机。

契约见 `../docs/protocol.md` v0.4 §3/§4/§5。部署目标：阿里云 VPS（本期 Windows PC 跑通）。

## 架构

```
小智云 LLM ──MCP──► 本服务 :8300
                      ├─ /mcp    MCP 工具端点（streamable-http）
                      │            ├─ hutuji_draw(description)      自然语言→SVG→G-code→URL
                      │            ├─ hutuji_status()               服务状态
                      │            └─ hutuji_preview_svg(description) 只出 SVG（调试）
                      └─ /files/{name}.gcode   G-code 静态文件源（S3 板下载入口）

LLM（OpenAI 兼容）→ SVG ──vpype(read→linesort→gwrite --profile hutuji)──► G-code
                                                                    └─ validate_gcode() §5 逐行断言

无硬件闭环：scripts/e2e_local.py = grbl_sim(假 Grbl:2323) ← pipe_sim(模拟 S3 转发) ← 测试 SVG
```

## 环境变量（禁止 .env，一律走环境变量）

| 变量 | 默认 | 说明 |
|------|------|------|
| `HUTUJI_LLM_BASE_URL` | `https://api.freemodel.dev/v1` | OpenAI 兼容接口地址 |
| `HUTUJI_LLM_API_KEY` | （空） | LLM key；未配置时 `hutuji_draw` 返回清晰错误 |
| `HUTUJI_LLM_MODEL` | `auto` | 模型名 |
| `HUTUJI_OUTPUT_DIR` | `./output` | G-code/SVG 产出目录 |
| `HUTUJI_PUBLIC_BASE_URL` | `http://127.0.0.1:8300` | 对外 URL 前缀（VPS 上改成公网地址） |
| `HUTUJI_PORT` | `8300` | HTTP 端口 |

Windows PowerShell 示例：

```powershell
$env:HUTUJI_LLM_API_KEY = "sk-xxx"
$env:HUTUJI_LLM_MODEL   = "gpt-4o-mini"
```

## 本地跑法

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt      # Linux: .venv/bin/pip

# 1) 无硬件闭环（不依赖 LLM key）：应输出 PASS
.venv/Scripts/python scripts/e2e_local.py

# 2) 起服务
.venv/Scripts/python -m uvicorn hutuji_mcp.server:app --port 8300

# 3) 单独跑假 Grbl（调转发用）：python -m hutuji_mcp.grbl_sim
```

## 小智云 MCP 接入点接法（待实机验证）

查证结论：小智官方云的 MCP 接入点是 **WebSocket**（`wss://api.xiaozhi.me/mcp/?token=xxxx`，
小智控制台 → 智能体 → MCP 接入点获取），外部工具服务需经**桥接器**接入：

```
小智云 (wss 接入点) ◄── mcp_pipe.py（ws↔stdio）◄── mcp_proxy（stdio↔http）◄── 本服务 :8300/mcp
```

- 桥接组件：小智官方 `mcp_pipe.py`（xiaozhi-esp32-server 仓）+ `mcp-proxy`（streamable-http→stdio），
  或一体化替代品（如社区 xiaozhi-mcp-bridge / xiaozhi-mcphub）。
- 本服务侧不用改代码：`/mcp` 就是标准 streamable-http 端点。
- 来源：[xiaohongshu-mcp 接入 xiaozhi 文档](https://blog.csdn.net/Extreme_wei/article/details/161058979)、
  [包会！小智接入MCP！只需4步](https://cloud.tencent.com/developer/article/2645010)。
  **具体桥接命令与 token 传参以实机联调为准（待验证）。**

## 部署 VPS 要点

- Linux + Python ≥3.10；`pip install -r requirements.txt`，systemd 托管 uvicorn。
- `HUTUJI_PUBLIC_BASE_URL` 改成 `http://<VPS公网IP>:8300`（S3 板经家用路由器出网下载，走公网）。
- 防火墙/安全组放行 8300；后续可加反代 + HTTPS。
- 产出目录 `output/` 定期清理（M2 再加 TTL）。

## G-code 契约（§5，validate_gcode 强制）

文件头 `G21`/`G90`/`F1500`；落笔 `M3`、抬笔 `M5`+`G4 P0.2`；快移 F2500 / 画线 F1500；
禁 `G28`/`G30`、禁任何 `G0` 落点 X0Y0（触发自动换纸）、首行禁 `G0 X0Y0Z0`、行 ≤255 字符、
坐标限幅 190×190mm（机器 200×200 软限位关）。profile 见 `hutuji_mcp/hutuji_gwrite.toml`。

## 目录

```
hutuji_mcp/  config.py llm_svg.py gcode_gen.py(+hutuji_gwrite.toml) grbl_sim.py pipe_sim.py server.py
scripts/     e2e_local.py
tests/       test_star.svg（五角星单线笔画，e2e 用）
output/      生成产物（运行时创建）
```
