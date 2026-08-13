"""假 Grbl Telnet 服务器（PC 仿真，无硬件闭环用）。

模拟 docs/protocol.md 的字节流行为：
- 客户端连上即发 banner "\\r\\nGrbl 1.3a ['$' for help]\\r\\n"
- 逐行收 G-code 回 "ok"
- "$I" 先回 "[VER:...]" 再回 "ok"（探活路径）
- "M30" 延迟后回 "[MSG:[PaperStatus] 0]\\r\\nok"（模拟换纸阻塞，真机 ≥60s、
  仿真压到 GRBL_SIM_PAPER_DELAY_S 秒）；GRBL_SIM_NO_PAPER=1 时回 "error:90"
  （模拟缺纸，§7）
- 换纸窗口内到达的运动行回 "error:8"（§7：该行被 empty_line() 丢弃，须重发）
- 含 "BADLINE" 的行回 "error:20"（错误路径测试触发词）
- §5/§3 设备边界建模（_contract_violation）：协议禁语/越界行回 error。
  这是「协议回归网」，不是逐字节真机仿真——真机对部分禁语会正常执行
  （G92/M3/G28）或触发自动换纸（G0 落点 XY≈0），但 §5 要求这些行永不
  到达设备；仿真在设备边界拒绝，使 e2e 能在 validate_gcode 回归时暴露失败。
- MAX_TLNT_CLIENTS=1 建模：同时只服务一个客户端，第二客户端立即断开（§1.1）
- 空闲超时：GRBL_SIM_IDLE_TIMEOUT_S（默认 60s）。真机 ~20s keepalive 回收
  半开连接；仿真取宽松值防慢调试误杀，差异有意。

独立运行：python -m hutuji_mcp.grbl_sim（默认端口 2323）
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import os
import re

logger = logging.getLogger("grbl_sim")

DEFAULT_PORT = 2323
BANNER = "\r\nGrbl 1.3a ['$' for help]\r\n"
VERSION_LINE = "[VER:1.3a-hutuji-sim.20260724:]\r\n[OPT:V,15,128]\r\n"

# §5 契约值（真机软限位 $130=210/$131=297 更宽；仿真按契约值拒绝，见模块 docstring）
_MAX_X_MM = 190.0
_MAX_Y_MM = 277.0
_ORIGIN_EPS_MM = 0.01
_ALLOWED_G_CODES = frozenset({"0", "1", "2", "3", "21", "90"})
_ARC_WORDS = frozenset({"I", "J", "R"})


def _env_float(name: str, default: float) -> float:
    """环境变量浮点参数；非法值回落默认（仿真进程不该因配置字符崩溃）。"""
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("%s 非数值 %r，回落默认 %s", name, raw, default)
        return default


PAPER_CHANGE_DELAY_S = _env_float("GRBL_SIM_PAPER_DELAY_S", 0.5)
IDLE_TIMEOUT_S = _env_float("GRBL_SIM_IDLE_TIMEOUT_S", 60.0)
NO_PAPER = os.environ.get("GRBL_SIM_NO_PAPER", "") == "1"


_WORD_RE = re.compile(r"([A-Za-z]-?\d*\.?\d+(?:[eE][+-]?\d+)?)")


def _parse_words(line: str) -> list[tuple[str, str]]:
    """拆出 (字母大写, 数值字串) 序列；畸形数值（如 `X1.5.5`）会被正则切碎，
    后续按实际匹配值处理——校验口径以 validate_gcode 为准，这里只是回归网。"""
    return [(word[0].upper(), word[1:]) for word in _WORD_RE.findall(line)]


def _looks_like_motion(line: str) -> bool:
    """G0–G3 运动行判定（error:8 建模用）。"""
    return any(
        letter == "G" and value in ("0", "1", "2", "3")
        for letter, value in _parse_words(line)
    )


def _contract_violation(line: str) -> str | None:
    """§5/§3 设备边界建模：返回错误应答（如 "error:11"），None = 放行。

    逐条对照（真机行为 → 仿真选择）：
    - 非 ASCII 或 payload > 254 字节 → error:11（真机同：§3 溢出判定 + empty_line 丢行）
    - 非白名单词法（G92/M*/G28/G30/G4/G53/G20/其他字母）→ error:20
      （真机对 G92/M3/G28 会执行；§5 文件契约禁，仿真拒绝使 e2e 立即失败）
    - X/Y 越出 [0,190]/[0,277] → error:20（真机软限位在 210/297 且报 ALARM；
      仿真按 §5 契约值拒绝）
    - Z 非 0/5 → error:20（真机执行；§5 文件契约禁）
    - F ≤0 或 >10000 → error:20（真机 F0 回 error:15、>12000 回 error:15，
      10000–12000 真机放行；仿真按契约值拒绝）
    - G0 且本行 X/Y 双字齐全、落点 XY≈0 → error:20（真机：触发自动换纸 + 失能
      电机，是最高危的文件缺陷；仿真拒绝使 e2e 立即失败）
    局限（有意不做）：无模态状态——`G0 X0`（缺 Y）不判落点；G2/G3 不做包围盒
    （validate_gcode 已覆盖，仿真不复刻）。"""
    stripped = line.strip()
    body = stripped.split(";", 1)[0].strip()
    if not body:
        return None
    # 设备按「收到的整行」判 ASCII 与 254 字节上限（注释字节也算 payload）
    if not stripped.isascii():
        return "error:11"
    if len(stripped.encode("ascii")) > 254:
        return "error:11"
    words = _parse_words(body)
    if not words:
        return None  # 纯注释或无可识别词法：与调用方过滤口径一致
    # 词法收口：已匹配词从正文剥掉后剩余必须全空白——`X1.5.5` 的尾巴 `.5`、
    # `XNAN` 这类畸形片段会残留。与 validate_gcode「畸形坐标不得静默跳过」同向。
    residue = _WORD_RE.sub(" ", body).strip()
    if residue:
        return "error:20"
    g0 = False
    x = y = z = f = None
    for letter, value in words:
        if letter == "G":
            if value not in _ALLOWED_G_CODES:
                return "error:20"
            if value == "0":
                g0 = True
            continue
        if letter in _ARC_WORDS:
            continue
        if letter not in ("X", "Y", "Z", "F"):
            return "error:20"
        try:
            num = float(value)
        except ValueError:
            return "error:20"
        if not math.isfinite(num):
            return "error:20"
        if letter == "X":
            x = num
        elif letter == "Y":
            y = num
        elif letter == "Z":
            z = num
        else:
            f = num
    if x is not None and not (0.0 <= x <= _MAX_X_MM):
        return "error:20"
    if y is not None and not (0.0 <= y <= _MAX_Y_MM):
        return "error:20"
    if z is not None and z not in (0.0, 5.0):
        return "error:20"
    if f is not None and not (0.0 < f <= 10000.0):
        return "error:20"
    if g0 and x is not None and y is not None and abs(x) < _ORIGIN_EPS_MM and abs(y) < _ORIGIN_EPS_MM:
        return "error:20"
    return None


async def handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    paper_delay_s: float = PAPER_CHANGE_DELAY_S,
    idle_timeout_s: float = IDLE_TIMEOUT_S,
    no_paper: bool = NO_PAPER,
) -> None:
    peer = writer.get_extra_info("peername")
    logger.info("客户端接入: %s", peer)
    writer.write(BANNER.encode())
    await writer.drain()
    pending: bytes | None = None
    try:
        while True:
            if pending is not None:
                # 换纸窗口内已缓冲到达的行：运动行按 §7 回 error:8
                data, pending = pending, None
                deferred = True
            else:
                try:
                    data = await asyncio.wait_for(reader.readline(), timeout=idle_timeout_s)
                except asyncio.TimeoutError:
                    logger.warning(
                        "连接空闲超 %gs，断开（真机 keepalive ~20s 回收；仿真取宽松值）",
                        idle_timeout_s,
                    )
                    break
                deferred = False
            if not data:
                break
            line = data.decode(errors="replace").strip()
            if not line:
                continue
            logger.info("<- %s", line)
            if "BADLINE" in line:
                response = b"error:20\r\n"
            elif line == "$I":
                writer.write(VERSION_LINE.encode() + b"ok\r\n")
                await writer.drain()
                continue
            elif line.startswith("M30"):
                # 换纸阻塞期：该行的 ok 在整个换纸完成后才返回（真机 ≥60s）
                await asyncio.sleep(paper_delay_s)
                writer.write(
                    b"error:90\r\n" if no_paper
                    else b"[MSG:[PaperStatus] 0]\r\nok\r\n"
                )
                # 窗口内已缓冲到达的下一行：留给下轮（deferred=True）按 §7 error:8 处置。
                # wait_for(timeout=0) 会把 readline 包成没机会启动的 task 恒超时，
                # 改用「起 task → 让一轮事件循环 → 完成则取、否则取消」的确定性探测。
                probe = asyncio.ensure_future(reader.readline())
                await asyncio.sleep(0)
                if probe.done():
                    pending = probe.result()
                else:
                    probe.cancel()
                    # 必须等取消真正落地：Task.cancel 只排程，取消未处理完时
                    # reader._waiter 仍指向旧 future，下一轮 readline 会抛
                    # RuntimeError 使整个 handler 崩溃（R11-TQ-02 换纸窗口
                    # 「窗口后到达行被误判」的根因）。await 让取消完整走完
                    # _wait_for_data 的 finally 清场后再回到主循环。
                    with contextlib.suppress(asyncio.CancelledError):
                        await probe
                    pending = None
                continue
            elif deferred and _looks_like_motion(line):
                response = b"error:8\r\n"
            else:
                refusal = _contract_violation(line)
                response = (refusal + "\r\n").encode() if refusal else b"ok\r\n"
            writer.write(response)
            await writer.drain()
    finally:
        logger.info("客户端断开: %s", peer)
        writer.close()


_active_clients = 0


async def _on_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """MAX_TLNT_CLIENTS=1：真机拒第二客户端（§1.1）。"""
    global _active_clients
    if _active_clients >= 1:
        logger.warning("已有客户端在服，拒绝第二客户端（MAX_TLNT_CLIENTS=1 建模）")
        writer.close()
        return
    _active_clients += 1
    try:
        await handle_client(reader, writer)
    finally:
        _active_clients -= 1


async def serve(port: int = DEFAULT_PORT) -> asyncio.AbstractServer:
    server = await asyncio.start_server(_on_client, "127.0.0.1", port)
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
