"""一键配置 Grbl 写字机 WiFi（通过串口）。

用法：
    python scripts/grbl_wifi_setup.py --port COM3 --ssid YOUR_SSID --dry-run
    set GRBL_WIFI_PASSWORD=YOUR_PASSWORD
    python scripts/grbl_wifi_setup.py --port COM3 --ssid YOUR_SSID --yes

安全约定（AGENTS.md「凭据禁入库」）：
- 本文件**不得**出现任何真实 SSID / 密码，示例一律用占位符。
- 密码优先从环境变量 `GRBL_WIFI_PASSWORD` 读；`--password` 仅供临时调试，
  它会进入 shell 历史与进程列表，不推荐。

危险性：本脚本会向写字机写 NVS 并发 `[ESP444]RESTART` **重启设备**，
出图期间执行会中断任务。默认需要交互确认；`--dry-run` 只打印将发送的命令、
不开串口，`--yes` 跳过确认（仅供工厂预烧脚本串联）。

量产时建议改用 AP 模式 WebUI 配网（手机浏览器 192.168.0.1），
本脚本仅供开发/工厂预烧阶段使用。
"""
import argparse
import getpass
import os
import sys
import time


# 串口写超时（秒）。配置命令只有十来个字节，115200bps 下写入本是毫秒级；
# 不设上限时 ser.write 可长时间挂住（与 bringup_fault_capture.py 的
# SERIAL_WRITE_TIMEOUT_S 同口径，那边 2.0s 是采集场景，这里交互场景取 3s）。
SERIAL_WRITE_TIMEOUT_S = 3.0

# 应答读取的静默窗口与总截止时间（秒）。pyserial 的 read(N) 只返回**当前可用**
# 字节（可小于 N），in_waiting 是瞬时值：应答跨 TCP/USB 分片时单次 read 会截断
# （如只读到 'erro'）被 classify_response 判失败，合法配置被中止（F-TOOL-06）。
# 配置命令的应答是几行文本，115200bps 下毫秒级到齐，静默窗口不必大；截止时间
# 兜底慢设备——宁可多等，也不许把合法配置误判失败。
READ_QUIET_S = 0.2
READ_DEADLINE_S = 3.0


def _read_response(s, *, quiet_s: float = READ_QUIET_S, deadline_s: float = READ_DEADLINE_S) -> str:
    """循环拼读一条命令的完整应答（F-TOOL-06），返回解码后的文本。

    pyserial 经典坑：`s.read(N)` 返回当前可用字节（可小于 N），应答跨 TCP/USB
    分片时单次读会截断（如只读到 'erro'）；固定 `sleep(1)` 再单次读既等不齐分片、
    也等不了慢设备。这里轮询 `in_waiting` 拼字节，直到「静默 quiet_s 无新数据」
    （应答行以 CRLF 结束，静默即行已完整）或截止时间。返回空串 = 设备无应答，
    由调用方按 fail-closed 处置。
    """
    deadline = time.monotonic() + deadline_s
    buf = bytearray()
    last_data_at = time.monotonic()
    while time.monotonic() - last_data_at < quiet_s:
        if time.monotonic() >= deadline:
            break
        n = s.in_waiting
        if n:
            chunk = s.read(n)
            if chunk:
                buf += chunk
                last_data_at = time.monotonic()
                continue
        time.sleep(0.02)
    return buf.decode("ascii", errors="replace").strip()


def _validate_serial_value(value: str, label: str, *, allow_empty: bool = False) -> None:
    """串口共享实时控制字节，不能传输与控制码冲突的配置值。"""
    if not value and not allow_empty:
        raise ValueError(f"{label} 不能为空")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise ValueError(f"{label} 不得包含控制字符（含换行）")
    if not value.isascii() or any(char in '?!~' for char in value):
        raise ValueError(f"{label} 含串口实时控制保留字符或非 ASCII 字符，请使用小派 HTTP 配网")


def _resolve_password(args: argparse.Namespace) -> str:
    """按 --password → 环境变量 GRBL_WIFI_PASSWORD → 交互输入 的顺序取密码。"""
    if args.password:
        print("  [WARN] --password 会进入 shell 历史与进程列表，建议改用 GRBL_WIFI_PASSWORD")
        return args.password
    env = os.environ.get("GRBL_WIFI_PASSWORD")
    if env:
        return env
    if args.dry_run:
        return ""
    return getpass.getpass("WiFi 密码（不回显）：")


def _mask_secret(text: str, secret: str) -> str:
    """把应答回显的密码替换成 ***（固件可能把写入的命令原样回显到串口）。"""
    return text.replace(secret, "***") if secret else text


def classify_response(resp: str) -> tuple[bool, str]:
    """判定一条 ESP 配置命令的应答，返回 (是否成功, 用于展示的关键行)。

    不能用 `"ok" in resp` 子串判定：SSID/密码本身可能含 "ok"（BookCafe、
    Tokyo5G），应答里只要带状态文本就可能被读成成功，脚本继续发
    `[ESP444]RESTART`，击穿「任一命令失败即中止、不重启」的约定。

    固件真值（`Report.cpp:210/213/237/239`）：成功 = 独立一行 `ok`；
    失败 = `error:<N>` 或 `error: <文本>`。行尾的 `:ok`/`:error:` 形态是防御性
    兼容、保留（F-TEST-09：实机 ESP 配置命令走 system_execute_line 无
    `>{line}:` 命令回显，该形态仅 $N0/$N1 启动行产生）。
    """
    lines = [line.strip() for line in resp.splitlines() if line.strip()]
    # error 优先：固件可能先 webPrint 若干信息行再给状态，且失败必须压过任何
    # 看起来像成功的回显内容。
    for line in lines:
        low = line.lower()
        if low.startswith("error:") or ":error:" in low:
            return False, line
    for line in lines:
        low = line.lower()
        if low == "ok" or low.endswith(":ok"):
            return True, line
    return False, lines[0] if lines else "(无应答)"


def main() -> int:
    """配置写字机 WiFi；全部命令应答 ok 且确认联网才返回 0。"""
    parser = argparse.ArgumentParser(description="配置 Grbl 写字机 WiFi")
    parser.add_argument("--port", required=True, help="串口（如 COM3）")
    parser.add_argument("--ssid", required=True, help="WiFi SSID")
    parser.add_argument("--password", help="WiFi 密码（不推荐；优先用 GRBL_WIFI_PASSWORD）")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--dry-run", action="store_true", help="只打印将发送的命令，不开串口")
    parser.add_argument("--yes", action="store_true", help="跳过交互确认（注意会重启写字机）")
    args = parser.parse_args()

    password = _resolve_password(args)

    try:
        _validate_serial_value(args.ssid, "SSID")
        if password:
            _validate_serial_value(password, "密码")
    except ValueError as exc:
        print(f"配置输入无效：{exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        print(f"[dry-run] 目标串口 {args.port} @ {args.baud}，将依次发送：")
        for cmd, desc in (
            (f"[ESP100]{args.ssid}", "设置 SSID"),
            ("[ESP101]<password>", "设置密码"),
            ("[ESP110]STA", "切换 STA 模式"),
            ("[ESP444]RESTART", "重启写字机"),
        ):
            print(f"  {desc}: {cmd}")
        print("[dry-run] 未打开串口，未发送任何命令。")
        return 0

    if not password:
        print("缺少密码：设置 GRBL_WIFI_PASSWORD 或传 --password", file=sys.stderr)
        return 2

    if not args.yes:
        print(f"即将向 {args.port} 写 WiFi 配置并**重启写字机**（出图中会中断任务）。")
        if input("确认继续？输入 yes 回车：").strip().lower() != "yes":
            print("已取消。")
            return 130

    try:
        import serial
    except ImportError:
        print("需要 pyserial：pip install pyserial", file=sys.stderr)
        return 1

    try:
        # 闭构造再 open：CH340 板 DTR/RTS 直连 IO0/EN，构造即开会拉起控制线
        # 触发复位、boot banner 冲掉首条命令应答（2026-08-31 新机 COM13 实测
        # ESP100 处中止）。与只读查询工具同口径：dtr/rts 恒 False。
        s = serial.Serial()
        s.port = args.port
        s.baudrate = args.baud
        s.timeout = 3
        s.write_timeout = SERIAL_WRITE_TIMEOUT_S
        s.dtr = False
        s.rts = False
        s.open()
        s.dtr = False
        s.rts = False
    except (serial.SerialException, OSError) as exc:
        # 坏端口/被占用/无权限：白话报错，退出码 1（与既有串口错误口径一致）。
        print(f"无法打开串口 {args.port}：{exc}", file=sys.stderr)
        return 1

    try:
        time.sleep(0.5)
        # 兜底清缓冲：即便控制线纪律失效吃了复位 banner，也不让它冒充首条命令应答。
        s.reset_input_buffer()
        failures: list[str] = []

        def send_cmd(cmd: str, desc: str) -> bool:
            """发一条 ESP 配置命令并判定应答；返回是否 ok。失败由调用方决定是否中止。"""
            s.write(f"{cmd}\r\n".encode())
            resp = _read_response(s)
            ok, key_line = classify_response(resp)
            # 展示 error 行而非首行：固件可能先回显命令再给状态，只印首行会把
            # 真实失败原因藏掉。设置密码的应答会回显完整命令（含密码），必须脱敏。
            print(f"  [{'OK' if ok else 'WARN'}] {desc}: {_mask_secret(key_line, password)}")
            return ok

        print(f"配置 Grbl WiFi（{args.port}）...")
        print(f"  SSID: {args.ssid}")
        print()

        # 任一配置命令失败即中止，不再发后续命令、不 RESTART：设备停在原配置，
        # 避免「SSID 已改密码没改」之类的半配置状态还被重启。
        for cmd, desc in (
            (f"[ESP100]{args.ssid}", "设置 SSID"),
            (f"[ESP101]{password}", "设置密码"),
            ("[ESP110]STA", "切换 STA 模式"),
        ):
            if not send_cmd(cmd, desc):
                print(f"  {desc} 失败，中止（未重启写字机，设备保持原配置）", file=sys.stderr)
                return 1
        print()
        print("重启写字机...")
        s.write(b"[ESP444]RESTART\r\n")
        time.sleep(5)
        boot = _read_response(s)

        s.write(b"[ESP420]\r\n")
        time.sleep(2)
        status = _read_response(s)

        report = f"{boot}\n{status}"
        for line in report.splitlines():
            # 展示与联网判据同口径（F-TOOL-08）：不展示 `Connected to: ` 行——固件
            # 在 STA 模式下无条件先印它，未连上时同样出现（拼接成 'Connected to:
            # Disabled Mode: …'），展示它会与「判失败却像已连上」自相矛盾；IP: 只在
            # isConnected() 分支内打印，是唯一可信的「已连上」证据行。
            if any(k in line for k in ["WiFi Mode", "IP:", "Signal"]):
                print(f"  {line.strip()}")

        # F-TOOL-07：联网确认以 [ESP420] 状态行读完后**设备自报**为准，不再按重启
        # 后前两次读取判定——STA 恰在旧判读之后、[ESP420] 之前连上时，旧实现会打出
        # Connected 状态行却 exit 1，自相矛盾。实机依据：WifiConfig.cpp:213 的
        # `[MSG:Connected with <IP>]` 与 WebSettings.cpp:438-440 [ESP420] 的
        # 'Connected to: ' 都由设备侧发出，后到者同样算数。
        # F-TOOL-08：只认 `IP:` 子串——WebSettings.cpp:460 在 STA 模式下**无条件**
        # 先印 `Connected to: `（未连上时该行拼接成 'Connected to: Disabled Mode: …'），
        # 裸 `Connected to: ` 命中会让「STA 未连接」假阳性 exit 0；而 `IP: ` 只在
        # WebSettings.cpp:491 的 `if (WiFi.isConnected())` 分支内打印，出现即连上。
        connected = "IP:" in report
        if not connected:
            failures.append("WiFi 未确认连上（未见 Connected/IP:）")
        if failures:
            print(f"\n失败：{failures}", file=sys.stderr)
            return 1
    except (serial.SerialException, OSError) as exc:
        # 运行中拔线/串口复位：白话报错，finally 关端口，退出码保持 1。
        print(f"串口通信失败（设备可能已断开）：{exc}", file=sys.stderr)
        return 1
    finally:
        s.close()

    print("\n完成。S3 会按 NVS 缓存 IP 直连、未命中再扫本机 /24 发现本机（候选须回 Grbl banner）；")
    print("建议在路由器上为本机 MAC 绑定静态 DHCP 租约，免去每次扫描（不依赖 mDNS）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
