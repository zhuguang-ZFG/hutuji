# deploy/ — 京东云部署模板

> 目标机：`117.72.118.95`（2核4G / 5Mbps）。真实密钥、密码、`MCP_ENDPOINT` **禁止**写入本目录已跟踪文件。  
> 操作说明：`../docs/deployment.md`；值守：`../docs/operations.md`。

## 文件

| 文件 | 用途 |
|------|------|
| `env.example` | EnvironmentFile 模板 → 复制到如 `/etc/hutuji/mcp.env`（`chmod 600`） |
| `hutuji-mcp.service` | uvicorn（仅 127.0.0.1:8300） |
| `hutuji-cloud-bridge.service` | 小智 WSS 桥接 |
| `nginx-hutuji.conf` | 仅反代 `/files`；其余 404；TLS 路径自填 |

## 安装提示

```bash
# 示例路径，按实机调整
sudo mkdir -p /etc/hutuji /opt/hutuji
# 同步代码到 /opt/hutuji，venv 装依赖
sudo cp env.example /etc/hutuji/mcp.env   # 再手工填密钥
sudo chmod 600 /etc/hutuji/mcp.env
sudo cp hutuji-mcp.service hutuji-cloud-bridge.service /etc/systemd/system/
sudo cp nginx-hutuji.conf /etc/nginx/sites-available/hutuji
# 启用 site、证书、daemon-reload、enable --now
```

验收：公网不能访问 `/mcp`；`/files` 经 HTTPS；本机 bridge 可连小智。
