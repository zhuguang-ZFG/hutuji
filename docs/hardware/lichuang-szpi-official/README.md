# 立创·实战派 ESP32-S3 官方资料补档包

> 用途：补齐枢纽仓此前缺的**资源标注图**与 **ES7210 / GC0308 等电路截图**。  
> 脚位真值仍以 `../lichuang-szpi-esp32s3-board.md` + xiaozhi `lichuang-dev/config.h` 为准，**禁止改脚**。  
> 抓取日期：2026-07-26；来源：立创 wiki（`wiki.lckfb.com`）公开图 + 已归档原理图 PDF。

## 包内结构

| 路径 | 内容 |
|------|------|
| `lichuang-szpi-esp32s3-sch-V1.0.1.pdf` | 原理图 PDF（V1.0.1，3 页；与上级目录同文件） |
| `named/` | 已命名 PNG（资源标注 / 结构尺寸 / 各模块原理图截图） |

## `named/` 索引

| 文件 | 说明 | 官方页 |
|------|------|--------|
| `resource-annotation-a.png` | 资源标注图 A（丝印/接口） | [总览](https://wiki.lckfb.com/zh-hans/szpi-esp32s3/) |
| `resource-annotation-b.png` | 资源标注图 B（背面/细节） | 同上 |
| `mechanical-dimensions.png` | 结构尺寸 | 同上 |
| `board-photo-overview.png` | 整机外观 | [电路介绍](https://wiki.lckfb.com/zh-hans/szpi-esp32s3/beginner/introduction.html) |
| `board-photo-detail.png` | 外观细节 | 同上 |
| `sch-module-esp32s3.png` | 模组原理图 | 同上 |
| `sch-usb-hub-ch334f.png` | USB-HUB CH334F | 同上 |
| `sch-usb-uart-ch340k.png` | CH340K UART0 | 同上 |
| `sch-buttons.png` | 复位/用户键 | 同上 |
| `sch-lcd-touch.png` | LCD + 触摸 | 同上 |
| `sch-camera-gc0308.png` | **摄像头 GC0308** | 同上 |
| `sch-audio-es7210-a.png` | **音频 ADC ES7210**（图 A） | 同上 |
| `sch-audio-es7210-b.png` | **音频 ADC ES7210**（图 B） | 同上 |
| `sch-audio-es8311.png` | 音频 DAC ES8311 | 同上 |
| `sch-audio-amp-ns4150b.png` | 功放 NS4150B | 同上 |
| `sch-dual-power.png` | 双路电源 | 同上 |
| `sch-imu-qmi8658.png` | 姿态 QMI8658 | 同上 |
| `sch-io-expander-pca9557.png` | IO 扩展 PCA9557 | 同上 |
| `sch-tf-card.png` | TF 1-SD | 同上 |
| `sch-expansion-gh125.png` | GH1.25 外拓（含 IO10/IO11） | 同上 |
| `sch-pdf-page1-preview.png` … `page3` | 开源硬件页原理图预览 | [开源硬件](https://wiki.lckfb.com/zh-hans/szpi-esp32s3/open-source-hardware/) |
| `oshwhub-project-card.png` | OSHWHUB 工程卡片截图 | 同上 |

## 官方完整资料包（网盘）

百度网盘仍提供整包（示例工程等，体积大）：  
https://pan.baidu.com/s/1Go4nKA6gJ14kPW9IwKRoPg?pwd=lckf  

本仓**已覆盖板级硬件闭环所需**的标注图 + 关键电路截图 + 原理图 PDF；网盘里的 SDK/例程**不入库**（非 hutuji 契约真值）。

## 版权

图片与原理图版权归立创开发板 / 嘉立创；本仓仅作工程对照归档，禁止再分发为独立商品资料包。
