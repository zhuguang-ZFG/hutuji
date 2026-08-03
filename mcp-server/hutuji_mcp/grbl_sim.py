"""假 Grbl Telnet 服务器（PC 仿真，无硬件闭环用）。

模拟 docs/protocol.md 的字节流行为：
- 客户端连上即发 banner "\\r\\nGrbl 1.3a ['$' for help]\\r\\n"
- 逐行收 G-code 回 "ok"
- "$I" 先回 "[VER:...]" 再回 "ok"（探活路径）
- "M30" 延迟 0.5s 回 ok（模拟换纸阻塞）
- 含 "BADLINE" 的行回 "error:20"（错误路径测试触发词）

独立运行：python -m hutuji_mcp.grbl_sim（默认端口 2323）
"""
from __future__ import annotations

import asyncio
import logging
import os
logger = logging.getLogger("grbl_sim")

DEFAULT_PORT = 2323
BANNER = "\r\nGrbl 1.3a ['$' for help]\r\n"
VERSION_LINE = "[VER:1.3a-hutuji-sim.20260724:]\r\n[OPT:V,15,128]\r\n"
PAPER_CHANGE_DELAY_S = 0.5


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("peername")
    logger.info("客户端接入: %s", peer)
    writer.write(BANNER.encode())
    await writer.drain()
    try:
        while True:
            data = await reader.readline()
            if not data:
                break
            line = data.decode(errors="replace").strip()
            if not line:
                continue
            logger.info("<- %s", line)
            if "BADLINE" in line:
                writer.write(b"error:20\r\n")  # 模拟"不支持或非法语句"
            elif line == "$I":
                writer.write(VERSION_LINE.encode() + b"ok\r\n")
            elif line.startswith("M30"):
                # 换纸阻塞期：该行的 ok 在整个换纸完成后才返回
                await asyncio.sleep(PAPER_CHANGE_DELAY_S)
                writer.write(b"[MSG:[PaperStatus] 0]\r\nok\r\n")
            else:
                writer.write(b"ok\r\n")
            await writer.drain()
    finally:
        logger.info("客户端断开: %s", peer)
        writer.close()


async def serve(port: int = DEFAULT_PORT) -> asyncio.AbstractServer:
    server = await asyncio.start_server(handle_client, "127.0.0.1", port)
    logger.info("grbl_sim 监听 :%d", port)
    return server


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[grbl_sim] %(message)s")
    raw_port = os.environ.get("GRBL_SIM_PORT", str(DEFAULT_PORT))
    try:
        port = int(raw_port)
    except ValueError:
        raise SystemExit(f"GRBL_SIM_PORT 非整数: {raw_port!r}") from None
    if not 1 <= port <= 65535:
        raise SystemExit(f"GRBL_SIM_PORT 越界: {port}")
    loop = asyncio.new_event_loop()
    server = loop.run_until_complete(serve(port))
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
        loop.run_until_complete(server.wait_closed())
        loop.close()


if __name__ == "__main__":
    main()
