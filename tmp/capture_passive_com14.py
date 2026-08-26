import serial, time

# Passive HIL capture: open COM14 with dtr/rts CLEARED (never reset the chip —
# EN/RTS resets can half-kill the USJ port), record everything for 30 min.
OUT = r"D:\Users\hutuji\results\local-only\2026-08-26\hil-touch4-com14.log"
ser = serial.Serial()
ser.port = "COM14"
ser.baudrate = 115200
ser.timeout = 1
ser.dtr = False
ser.rts = False
ser.open()
print("passive capture open (dtr/rts False, no reset)", flush=True)
buf = []
t0 = time.time()
while time.time() - t0 < 1800:
    line = ser.readline()
    if line:
        buf.append(time.strftime("[%H:%M:%S] ") + line.decode("utf-8", "replace").rstrip())
        if len(buf) % 20 == 0:
            open(OUT, "w", encoding="utf-8").write("\n".join(buf) + "\n")
open(OUT, "w", encoding="utf-8").write("\n".join(buf) + "\n")
hits = [b for b in buf if "ui machine action" in b or "Home buttons geom" in b
        or "EnterWifiConfigMode" in b or "TOUCH-DIAG" in b]
print(f"captured {len(buf)} lines -> {OUT}", flush=True)
print("key events:", len(hits), flush=True)
