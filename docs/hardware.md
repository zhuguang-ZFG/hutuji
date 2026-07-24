# hutuji 硬件清单与接线（备用方案留档）

> **当前链路为方案 E（WiFi Telnet），零额外硬件**，见 `protocol.md` §1。
> 本文保留备用方案 B（拆机 TTL）接线与已否决方案 D（CH9350）的否决依据。

## 备用方案 B：拆机 TTL（WiFi 实测抖动不可接受时启用）

| 写字机侧 | 实战派多功能外拓口（GH1.25） | 说明 |
|----------|------------------------------|------|
| ESP32 GPIO1（TX0，CH340 输出侧并接） | IO11（S3 RX） | 交叉 |
| ESP32 GPIO3（RX0） | IO10（S3 TX） | 交叉 |
| GND | GND | 共地 |

- 3.3V 电平直连；写字机 CH340 保留作调试口（TX 线并接只读监听安全）。
- 实战派外拓口丝印（资源标注图背面）：多功能口 = `5V / GND / IO10 / IO11`；I2C 口 = `5V / GND / SDA / SCL`。
- M1 固件的 UART 传输层实现已留档（git 历史），Pipe API 不变可切换。

## 已否决方案 D：CH9350 桥接（2026-07-24 否决）

- 原因：CH9350 仅支持 **HID 类**设备（键盘/鼠标/扫码枪，[WCH 手册](https://www.wch.cn/products/CH9350.html)），不枚举写字机的 CH340（vendor-specific 串口）。
- 照片留档：`images/ch9350-module.jpg`、`images/ch9350-dip.jpg`。

## 已否决：S3 直连 USB Host（2026-07-24 否决）

- 实战派 C 口 = CH334F HUB 上行；下行 D4=CH340K→UART0（调试），D3=S3 原生 USB（设备向）。**S3 不能经 C 口当 USB Host**，GPIO19/20 无排针引出。（[立创 wiki](https://wiki.lckfb.com/zh-hans/szpi-esp32s3/beginner/introduction.html)）

## 关键事实（2026-07-24 查证）

- 写字机 USB-B 口插 PC 显示 CH340（Grbl UART0，115200 8N1），串口为全量诊断日志口。
- Grbl_Esp32 内置 ESP3D WebUI：Telnet :23 / WebSocket :81，Config.h 已启用 WiFi。
- 写字机商业上位机现用 BT SPP 无线流式出图（`PAPER_AUTO_CHANGE_ON_BT_CONNECT`），无线流控经量产验证。
