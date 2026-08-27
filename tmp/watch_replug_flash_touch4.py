import subprocess, sys, time

# Watchdog: wait for COM14 to reappear after physical replug, flash the
# build-touch4-ws35 image, then verify boot via "Home buttons geom:" anchor.
# Reuses reset_wdt_capture.py for the boot capture (WDT reset path, never RTS).
FLASH_PS1 = r"D:\Users\hutuji\tmp\flash_touch4_ws35.ps1"
CAPTURE_PY = r"D:\Users\hutuji\tmp\reset_wdt_capture.py"
OUT_LOG = r"D:\Users\hutuji\results\local-only\2026-08-26\touch4-boot-com14.log"
IDF_PY = r"C:\Espressif\tools\python\v6.0.1\venv\Scripts\python.exe"


def com14_present():
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "[System.IO.Ports.SerialPort]::GetPortNames()"],
        capture_output=True, text=True, timeout=30,
    )
    return "COM14" in r.stdout


deadline = time.time() + 15 * 60
while not com14_present():
    if time.time() > deadline:
        sys.exit("COM14 did not reappear within 15 minutes; replug aborted?")
    print("waiting for COM14 (replug the S3 USB cable)...", flush=True)
    time.sleep(5)

print("COM14 back; flashing build-touch4-ws35...", flush=True)
r = subprocess.run(
    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
     "-File", FLASH_PS1],
    capture_output=True, text=True, timeout=240,
)
print(r.stdout[-600:], flush=True)

# flash 的 watchdog-reset 会触发 USJ 重枚举窗口；立刻再开第二个 esptool
# 句柄正是 1 小时前打死 COM14 的模式。先落定 + 复查端口，不在场就退出
# 交回人工，绝不盲调 esptool。
time.sleep(10)
if not com14_present():
    sys.exit("COM14 not back 10s after flash; replug again, then rerun this script.")
# Boot capture: WDT reset path, miss first ~1-2s is accepted trade-off.
r = subprocess.run(
    [IDF_PY, CAPTURE_PY, OUT_LOG, "120"],
    capture_output=True, text=True, timeout=180,
)
print(r.stdout[-400:], r.stderr[-200:], flush=True)

log = open(OUT_LOG, encoding="utf-8", errors="replace").read()
for anchor in ("Home buttons geom:", "Touch registers:", "EnterWifiConfigMode"):
    print(f"anchor {anchor!r}:", "FOUND" if anchor in log else "missing", flush=True)
