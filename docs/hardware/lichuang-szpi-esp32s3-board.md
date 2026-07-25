# 立创·实战派 ESP32-S3（xiaozhi `lichuang-dev`）资料

> **板名**：立创开发板 · 实战派 ESP32-S3（原理图标题 ESP32S3-GMK2 / `ESP32-S3-V1_0_1`，V1.0.1）  
> **固件板型**：`D:/Users/xiaozhi-esp32/main/boards/lichuang-dev/`（`config.h` + `lichuang_dev_board.cc`）  
> **原理图归档**：[`lichuang-szpi-esp32s3-sch.pdf`](./lichuang-szpi-esp32s3-sch.pdf)（同内容别名 [`立创实战派-ESP32-S3-原理图-V1.0.1.pdf`](./立创实战派-ESP32-S3-原理图-V1.0.1.pdf)）  
> **官方资料补档包**：[`lichuang-szpi-official/`](./lichuang-szpi-official/)（资源标注图 + ES7210/GC0308 等电路截图；索引见包内 `README.md`）  
> **官方入口**：[wiki 总览](https://wiki.lckfb.com/zh-hans/szpi-esp32s3/) · [下载中心（百度网盘 pwd=`lckf`）](https://wiki.lckfb.com/zh-hans/szpi-esp32s3/download-center.html) · [OSHWHUB 工程](https://oshwhub.com/li-chuang-kai-fa-ban/li-chuang-shi-zhan-pai-esp32-s3-kai-fa-ban)  
> **hutuji 角色**：S3 跑 xiaozhi（AI/语音/WiFi），板间出图走方案 E WiFi Telnet（**不占** IO10/IO11）；IO10/IO11 仅备用方案 B。

**铁律：现役板级 `config.h` / `lichuang_dev_board.cc` 脚位视为正确，默认禁止改 GPIO。**  
本文只对照归档原理图与官方 wiki；不得为「对齐 PDF/注释」去改固件。

## 1. 板级组成

| 类别 | 型号 / 器件 | 说明 |
|------|-------------|------|
| 模组 | ESP32-S3-WROOM-1-N16R8 | Flash 16MB + PSRAM 8MB；IO35/36/37 给八线 PSRAM，**勿占用** |
| 显示 | ST7789 2.0" IPS 320×240 SPI | CS / 背光控制经 PCA9557 |
| 触摸 | FT6x36（固件驱动 FT5x06 兼容）I2C `0x38` | 与板载 I2C 总线共用 |
| 音频 | ES8311（DAC）+ ES7210（ADC）+ NS4150B | 双 MIC + 硬件 AEC；功放 `PA_EN` 在 PCA9557 |
| 摄像头 | GC0308 DVP | `DVP_PWDN` 在 PCA9557 bit2 |
| IO 扩展 | PCA9557PW I2C `0x19` | 至少用 3 路：LCD_CS / PA_EN / DVP_PWDN |
| USB | CH334F HUB + CH340K | C 口上行；D4→CH340→UART0；D3→S3 USB-OTG（IO19/20） |
| 外拓 | GH1.25 5P ×2 | I2C 口；多功能口 = `5V/GND/IO10/IO11` |
| 存储 | TF 1-bit SD | CMD=IO48，CLK=IO47，DAT0=IO21 |
| 其它 | QMI8658 姿态；可选 DHT11（原理图 IO5） | 见 §3 与固件差异 |

## 2. 现役固件脚位表（真值 = 代码宏）

来源：`lichuang-dev/config.h` + `lichuang_dev_board.cc`（摘录，**勿改**）。

### 2.1 I2C / 音频 / 按键

| 功能 | GPIO | 原理图网名（对照） |
|------|------|-------------------|
| I2C SDA | IO1 | `IO1_I2C_SDA` |
| I2C SCL | IO2 | `IO2_I2C_SCL` |
| I2S MCLK | IO38 | `IO38_I2S_MCK` |
| I2S BCLK | IO14 | `IO14_I2S_BCK` |
| I2S WS | IO13 | `IO13_I2S_WS` |
| I2S DIN | IO12 | `IO12_I2S_DI` |
| I2S DOUT | IO45 | `IO45_I2S_DO` |
| BOOT / 用户键 | IO0 | `IO0_BOOT` |
| 板载 LED | IO48 | 与 `IO48_SD_CMD` 同脚——**插 TF 时注意冲突**（现役宏如此） |

### 2.2 液晶（SPI3，CS 走扩展）

| 功能 | GPIO / 扩展 | 说明 |
|------|-------------|------|
| MOSI | IO40 | `IO40_LCD_MOSI` |
| SCK | IO41 | `IO41_LCD_SCK` |
| DC | IO39 | `IO39_LCD_DC` |
| 背光 | IO42 | `IO42_LCD_BL`（固件 PWM，极性倒置） |
| CS | PCA9557 bit0 | 原理图 `LCD_CS`；`SetOutputState(0, 0)` 片选有效 |
| PA_EN | PCA9557 bit1 | 功放使能 |
| DVP_PWDN | PCA9557 bit2 | 摄像头上电；`SetOutputState(2, 0)` |

### 2.3 摄像头 DVP（GC0308）

| 功能 | GPIO |
|------|------|
| XCLK | IO5 |
| PCLK | IO7 |
| VSYNC | IO3 |
| HREF | IO46 |
| D0…D7 | IO16,18,8,17,15,6,4,9 |
| SCCB | 复用板载 I2C（SDA=IO1，SCL=IO2） |

> 原理图另标 `IO5_DHTXX`（可选温湿度）。现役 xiaozhi 把 IO5 用作摄像头 XCLK——**以固件为准**；不要为 DHT 改固件脚。

### 2.4 TF / USB / 外拓（hutuji 相关）

| 功能 | GPIO / 接口 | hutuji 用法 |
|------|-------------|-------------|
| SD CMD/CLK/DAT0 | IO48 / IO47 / IO21 | 非出图链路 |
| USB-OTG D−/D+ | IO19 / IO20 | **无排针**；经 HUB 仅设备向 → USB Host 已否决 |
| UART0 | U0TXD / U0RXD → CH340K | Type-C 调试口 |
| 多功能外拓 | **IO10 / IO11**（GH1.25） | 仅备用方案 B TTL；方案 E **不用** |
| I2C 外拓 | 同板载 IO1/IO2 | 勿当通用 GPIO 乱复用 |

备用 TTL 交叉接法见 [`../hardware.md`](../hardware.md) §备用方案 B。

## 3. 与写字机 / 方案 E 的边界

- 现行板间：**WiFi Telnet :23**，S3 做 TCP 客户端 → 写字机固定 IP；**零额外接线**。  
- IO10/IO11 留给备用 TTL；未启用前保持空闲，禁止被其它功能占用（除非用户点名）。  
- 改 `lichuang-dev` 脚位 = 改硬件契约：须用户逐脚批准 + 更新本页 + 实机复测；**默认禁止**。

## 4. 资料完整度

| 项 | 状态 |
|----|------|
| 原理图 PDF V1.0.1（3 页）本仓归档 | ✅ |
| 固件脚位对照表（上表） | ✅ |
| 官方 wiki / OSHWHUB / 网盘链接 | ✅ |
| 资源标注图 / 结构尺寸本仓位图 | ✅ `lichuang-szpi-official/named/resource-annotation-*.png` |
| ES7210 / GC0308 等电路截图 | ✅ `lichuang-szpi-official/named/sch-audio-es7210-*.png`、`sch-camera-gc0308.png` 等 |
| 百度网盘整包（含 SDK/例程） | ⚪ 不入库；板级硬件闭环不依赖 |

## 5. Agent 禁令

- 禁止改 `config.h` / 板级 `.cc` 里的 GPIO 以「对齐原理图注释」。  
- 禁止重开 USB Host / CH9350；禁止把 hutuji 板级行为塞进 xiaozhi 核心模块。  
- 哑管道契约：`../protocol.md` v0.5；补丁说明：`../../firmware/m1-usb-pipe.md`。
