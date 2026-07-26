"""一键配置 Grbl 写字机 WiFi（通过串口）。

用法：python scripts/grbl_wifi_setup.py --port COM3 --ssid ChinaNet-jJmz --password ymwnktgp

量产时建议改用 AP 模式 WebUI 配网（手机浏览器 192.168.0.1），
本脚本仅供开发/工厂预烧阶段使用。
"""
import argparse
import sys
import time


def main():
    parser = argparse.ArgumentParser(description="配置 Grbl 写字机 WiFi")
    parser.add_argument("--port", required=True, help="串口（如 COM3）")
    parser.add_argument("--ssid", required=True, help="WiFi SSID")
    parser.add_argument("--password", required=True, help="WiFi 密码")
    parser.add_argument("--baud", type=int, default=115200)
    args = parser.parse_args()

    try:
        import serial
    except ImportError:
        print("需要 pyserial：pip install pyserial")
        sys.exit(1)

    s = serial.Serial(args.port, args.baud, timeout=3)
    time.sleep(0.5)

    def send_cmd(cmd: str, desc: str) -> str:
        s.write(f"{cmd}\r\n".encode())
        time.sleep(1)
        resp = s.read(s.in_waiting or 512).decode("ascii", errors="replace").strip()
        status = "OK" if "ok" in resp.lower() else "WARN"
        print(f"  [{status}] {desc}: {resp.splitlines()[0] if resp else '(无应答)'}")
        return resp

    print(f"配置 Grbl WiFi（{args.port}）...")
    print(f"  SSID: {args.ssid}")
    print()

    send_cmd(f"[ESP100]{args.ssid}", "设置 SSID")
    send_cmd(f"[ESP101]{args.password}", "设置密码")
    send_cmd("[ESP110]STA", "切换 STA 模式")
    print()
    print("重启写字机...")
    s.write(b"[ESP444]RESTART\r\n")
    time.sleep(5)

    data = s.read(s.in_waiting or 2048).decode("ascii", errors="replace")
    if "Connected" in data or "IP:" in data:
        print("WiFi 连接成功！")
    else:
        print("等待 WiFi 连接...")
        time.sleep(5)
        data = s.read(s.in_waiting or 2048).decode("ascii", errors="replace")

    s.write(b"[ESP420]\r\n")
    time.sleep(2)
    status = s.read(s.in_waiting or 2048).decode("ascii", errors="replace")
    for line in status.splitlines():
        if any(k in line for k in ["WiFi Mode", "Connected", "IP:", "Signal"]):
            print(f"  {line.strip()}")

    s.close()
    print("\n完成。S3 将通过 mDNS (grblesp.local) 自动发现写字机。")


if __name__ == "__main__":
    main()
