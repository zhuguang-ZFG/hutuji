"""hutuji ③ MCP 生成服务入口（FastAPI + FastMCP，端口 8300）。

端点：
- GET /files/{name}   G-code 静态文件源（output 目录，S3 板后续从这里下载）
- /mcp                MCP 工具端点（streamable-http，接小智云需经桥接器，见 README）

工具面（对齐 plotter-studio-mcp 的 draw/status 划分，代码全自研）：
- hutuji_draw(description)        自然语言 → SVG → G-code（§5 校验）→ URL
- hutuji_status()                 服务状态
- hutuji_preview_svg(description) 只生成 SVG（调试 LLM 用）
"""
from __future__ import annotations

import contextlib
import json
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from mcp.server.fastmcp import FastMCP

from .config import settings
from .gcode_gen import svg_to_gcode
from .llm_svg import generate_svg

# 保持默认 streamable_http_path="/mcp"，子应用挂到根（/files 先注册优先匹配），
# 避免 Mount("/mcp") 对裸 /mcp 产生 307 跳转（POST 桥接器不一定跟随重定向）
mcp = FastMCP("hutuji")


def _new_basename() -> str:
    return "draw_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")


@mcp.tool()
async def hutuji_draw(description: str) -> str:
    """让写字机画一幅画：输入自然语言描述（如"一只站在树枝上的小鸟"），
    生成单线笔画 G-code 并返回下载 URL，供设备侧 hutuji.draw 拉取执行。"""
    import asyncio

    svg = await generate_svg(description)

    settings.output_dir.mkdir(parents=True, exist_ok=True)
    base = _new_basename()
    svg_path = settings.output_dir / f"{base}.svg"
    gcode_path = settings.output_dir / f"{base}.gcode"
    svg_path.write_text(svg, encoding="utf-8")

    # vpype 转换是阻塞子进程，扔到线程池避免卡住事件循环
    stats = await asyncio.to_thread(svg_to_gcode, svg_path, gcode_path)

    url = f"{settings.public_base_url.rstrip('/')}/files/{gcode_path.name}"
    return json.dumps({"url": url, "lines": stats.lines, "bytes": stats.bytes},
                      ensure_ascii=False)


@mcp.tool()
async def hutuji_status() -> str:
    """查询 hutuji MCP 生成服务状态（输出目录、最近文件、LLM 是否配置）。"""
    out = settings.output_dir
    files = sorted(out.glob("*.gcode"), key=lambda p: p.stat().st_mtime) if out.is_dir() else []
    return json.dumps({
        "output_dir": str(out),
        "public_base_url": settings.public_base_url,
        "llm_configured": settings.llm_configured,
        "llm_model": settings.llm_model,
        "gcode_files": len(files),
        "latest": files[-1].name if files else None,
    }, ensure_ascii=False)


@mcp.tool()
async def hutuji_preview_svg(description: str) -> str:
    """只生成 SVG 不转 G-code（调试用）：返回 SVG 文本前 2000 字符。"""
    svg = await generate_svg(description)
    return svg[:2000]


# ----- FastAPI 应用 -----

_mcp_app = mcp.streamable_http_app()


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # 挂载的子应用 lifespan 不会自动运行，必须显式拉起 MCP session manager
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="hutuji MCP 生成服务", lifespan=lifespan)

# 静态文件源必须在根挂载之前注册，路由按注册顺序匹配
app.mount("/files", StaticFiles(directory=settings.output_dir, check_dir=False), name="files")
app.mount("/", _mcp_app)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.port)
