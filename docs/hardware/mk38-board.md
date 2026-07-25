# MK38 写字机主控板（Grbl / ESP32）资料

> **板名**：MK38（原理图标题「MK38-总图」）  
> **图纸日期**：2026-01-15（原理图栏）  
> **固件机型头**：`D:/Users/Grbl_Esp32/Grbl_Esp32/src/Machines/custom_3axis_hr4988.h`  
> **原理图归档**：[`MK38-总图.pdf`](./MK38-总图.pdf)（来源：`Downloads/MK38-总图(1).pdf`）  
> **链路角色**：写字机运动 + 换纸（Grbl_Esp32）；与 S3 的板间为方案 E WiFi Telnet，见 `../protocol.md` §1。

本文把原理图可核对事实与现役固件引脚对齐，防止 agent 按过时注释乱改脚位。

## 1. 板级组成（来自总图）

| 位号 | 器件 | 作用 |
|------|------|------|
| U1 | ESP32 模组（原理图脚位含 IO0–IO35 / TXD0 / RXD0） | 主控 |
| U2 / U3 / U4 | HR4988SQ | XYZ 步进驱动（XH 座 J1/J2/J3） |
| U7 / U8 / U9 / U10 | HR4988SQ | 纸路相关步进（XH 座 J12/J13/J14/J15） |
| U5 | 74HC595D | 纸路 STEP/DIR/使能等扩展输出 |
| U11 | CH340C | USB-B（J11）↔ UART0 调试口 |
| Q1 | 78M05H | 5V |
| U6 | AMS1117-3.3 | 3.3V（VCC） |
| J7 | DC-012G | 电源输入 |
| J5 | 金属自锁/按钮 | 换纸按键相关（见 GPIO35） |
| J8 | 预留/网络丝印 N | 未在固件占用为必选 |

电机座均为 **XH2.54 4P 卧贴**。

## 2. 与现役固件一致的引脚表

**铁律：现役 `#define` 脚位视为正确，禁止轻易改脚。**  
本文只**摘录** `custom_3axis_hr4988.h` 里的宏，方便对照原理图；不得为「对齐文档/注释/PDF」去改固件脚位。  
文件头注释若与 `#define` 不一致，**以 `#define` 为准**——也不要擅自改注释去「纠正」。

### 2.1 主轴 XYZ（直连 ESP32 → HR4988）

| 功能 | GPIO | 原理图网名 |
|------|------|------------|
| X STEP | IO2 | `U1-IO2` |
| X DIR | IO15 | `U1-IO15` |
| Y STEP | IO13 | `U1-IO13` |
| Y DIR | IO12 | `U1-IO12` |
| Z STEP | IO14 | `U1-IO14` |
| Z DIR | IO27 | `U1-IO27` |
| XYZ 公共 nENABLE | IO4 | `U1-IO4`（低有效） |
| 驱动 REF（DAC） | IO25 | `U1-IO25`（纸路 + Z 共脚） |

### 2.2 74HC595（I2S 移位扩展，纸路）

| 595 脚 | ESP32 | 固件宏 |
|--------|-------|--------|
| SI (14) | IO21 | `I2S_OUT_DATA` |
| SRCLK (11) | IO16 | `I2S_OUT_BCK` |
| RCLK (12) | IO17 | `I2S_OUT_WS` |

| Qx | 功能 | 固件宏 |
|----|------|--------|
| Q0 | 换纸键 LED（高灭低亮） | `PAPER_LED_PIN` |
| Q1 | 纸路面板侧使能 | `PAPER_PANEL_ENABLE_PIN` |
| Q2/Q3 | 拾落电机 DIR/STEP | `CLAMP_MOTOR_*` |
| Q4/Q5 | 面板电机 DIR/STEP | `PANEL_MOTOR_*` |
| Q6/Q7 | 进纸电机 DIR/STEP | `FEEDER_MOTOR_*` |

另：`PAPER_DRIVER_ENABLE_PIN = GPIO26`（纸路驱动使能，非 595）。

### 2.3 传感器 / 按键 / 串口

| 功能 | GPIO | 电平约定（固件） |
|------|------|------------------|
| 纸传感器 | IO34 | HIGH=无纸，LOW=有纸 |
| 换纸按键 | IO35 | LOW=按下（`INVERT_CONTROL_PIN_MASK` 含 Macro0） |
| UART0 TX/RX | TXD0/RXD0 | CH340 → USB-B，115200；**全量诊断口** |

## 3. 电源与接口（联调注意）

- 逻辑 3.3V = `VCC`（AMS1117）；电机侧 `VBB` 来自 DC 座与大电容网络。  
- USB-B **只做串口调试**，不是方案 E 数据链；板间出图走 WiFi Telnet :23。  
- PC 用 CH340 旁观时，**不要**再占 Telnet 槽（`MAX_TLNT_CLIENTS=1`）。

## 4. Agent / 改脚禁令

- **默认禁止改任何 GPIO / I2S / 步进 / 595 相关 `#define`。** 商业机已按现役脚位跑通。  
- 即使用户给了新原理图，也只更新**本仓文档对照表**；改固件脚位须用户**逐脚点名批准** + fz `machine_pin_erc` + 实机复测，否则一律不做。  
- **禁止**为仿真/「好看」改换纸机械时序；§9 只允许消息路由/keepalive/`Changing`（见 `../protocol.md` §9）。

## 5. 资料索引

| 资料 | 位置 |
|------|------|
| 本板总图 PDF | `docs/hardware/MK38-总图.pdf` |
| 固件机型 | `Grbl_Esp32/.../Machines/custom_3axis_hr4988.h` |
| 换纸实现 | `Grbl_Esp32/.../PaperSystem.cpp`、`Custom/paper_system.cpp` |
| 板间协议 | `docs/protocol.md` |
| 备用 TTL / 否决方案 | `docs/hardware.md` |
| S3 开发板 | [`lichuang-szpi-esp32s3-board.md`](./lichuang-szpi-esp32s3-board.md) |
