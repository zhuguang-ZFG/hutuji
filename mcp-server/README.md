# mcp-server/（③ 待建）

云端 MCP 生成服务：接收官方云 LLM 的工具调用 → 生成 G-code（字体笔画/SVG 转换）→ 存 HTTP 文件源 → 返回 URL 并触发设备侧 `hutuji.draw`。

契约见 `../docs/protocol.md` §3 §4。部署目标：阿里云 VPS。
