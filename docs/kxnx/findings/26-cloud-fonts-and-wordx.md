# 26 · 云字库与 wordx：下载零校验 + XTEA 硬编码密钥 + 字体绝对路径内嵌

> 结论先行：云字库链路是 findings/24 明文面的延伸——账号密码**明文 JSON 落盘 + 明文 HTTP 传输**；字体下载**无哈希/无签名/无大小上限**，v5+ 的 XTEA「加密」密钥硬编码随二进制公开（混淆非信任边界）；wordx 文档把**字体文件绝对路径**写进格式并在加载时按该路径读任意文件，且加载后的文本框导出必 NPE。正面发现：`.gfont` 的 ZIP 读取只流式消费条目、从不按条目名落盘，**天然免疫 Zip Slip**。**对 hutuji 零动作（无字体/文档格式对应面）。**
>
> **行号基准树**：`D:/Users/kxnx/kx/tools/dump_agent/decompiled_full/`（A 混淆体系，920 文件）。
>
> 日期：2026-08-14（第十八轮）。方法：子代理深审 + 主代理复核高载证据。【已复核】= 主代理亲读确认。

---

## 1. 字体面地图

```
云字库（fonts/b 登录 → OSS STS → w/c/h 三路下载，无校验落 gcodeFonts/）
  → .gfont = XTEA(v5+) + ZIP 字形流（lib/b/e 加载；lib/b/a 反序列化，只查上限不查负值）
  → b.i 编排（缺字：skip + losechar 事件 / 方框占位 / 递归降级深度≤1）
  → word/ 文字引擎（wordx 内嵌字体绝对路径；缺字体替换 list.get(0) 无空检查）
```

## 2. CRITICAL / HIGH

### C1. 云字库凭据双重暴露：明文 HTTP + 明文 JSON 落盘【子代理证据】

- 证据：`net/a.java:71`（基址默认明文 HTTP，findings/24-C1 同源）；`fonts/b.java:113`（username/password POST `/kvenjoyweb/drawsoftdo` 换 ossid）；`data/Storage.java:206-208`（Gson 明文写 `<安装目录>/settings/CloudFontsData.json`）；`fonts/b.java:76-77`（对话框构造时密码原样回填 JPasswordField）。
- **hutuji 对照**：凭据只落 `/etc/hutuji/*.env` chmod 600 的反面教材。

### H1. 字体下载零完整性校验 + XTEA 硬编码密钥【已复核】

- 证据：`fonts/w.java:48`（OSS 对象读入缓冲直接 `FileOutputStream` 落盘，无大小上限/哈希/签名）；`lib/b/e.java:230-237`（v≥5 头部 `readInt`+`readFully` 后用静态表 `y` 解密【已复核】）；`lib/a/a/a.java:13`（自实现 XTEA，delta=2654435769）；`lib/b/e.java:414`（剩余 ZIP 字节 `IOUtils.toByteArray` 无上限读内存）。
- 细节：密钥随二进制公开 = 混淆非加密；v<5 明文直读，伪造 v4 字体零门槛；恶意字体可致内存/磁盘耗尽。
- **hutuji 对照**：下载内容须大小上限 + 解析前校验；「加密」定性为混淆。

### H2. wordx 内嵌字体绝对路径 + 加载按任意路径读文件 + 加载后导出必 NPE【子代理证据】

- 证据：`word/entites/c.java:52`（序列化写 `this.c.a.getAbsolutePath()`）；`:64`（加载按 readUTF 路径 `new FileInputStream(new File(path))` 读任意本地文件）；`:50`（`this.m.a()`——`m` 仅新建路径赋值，从 wordx 加载的实体 `m==null` → 导出必 NPE）。
- 细节：分享 wordx 泄露本机路径且换机即失效；恶意 wordx 可指向任意路径（内容按 GZIP 解析，失败则整文档拒载）；「打开含文本框的 wordx → 无法再次导出」可稳定复现（静态推断）。
- **hutuji 对照**：文档格式不内嵌绝对路径；序列化/反序列化字段完整性应列为验收项。

## 3. MEDIUM

| # | 发现 | 证据 | 要点 |
|---|------|------|------|
| M1 | wordx 缺字体自动替换 `list.get(0)` 无空检查：本地字体目录空 → IndexOutOfBounds → 文档加载失败内容不可见 | `word/g.java:1400`；启动时解压 6 内置字体（`KenjoyDraw.java:248-261`）是缓解但用户删光后可触发 | 资源替换前校验可用性；空列表走明确错误分支 |
| M2 | 字形反序列化只查上限 10000 不查负值 → `new float[负数]` NegativeArraySizeException；截断流 EOF 传播，单字损坏拖垮整字体 | `lib/b/a.java:143,149`；正向容错仅三处（宽度归零/EOF break/枚举截断） | 数值解析双向边界（下限+上限）；单字形失败应跳过而非整字体不可用 |
| M3 | 字体目录固定安装目录不可配：Program Files 需管理员权限；Linux 分支返回 null → NPE | `data/b.java:20,42`；`lib/c.java:56` | 资源目录固定且可配置 + 启动自检可写 |
| M4 | 字体名路径拼接：加载侧 `new File(f(), name)` 无 `..` 过滤（只读穿越面）；下载侧 basename 截取对无扩展名 key 越界必失败 | `lib/b/g.java:38`；`fonts/w.java:39`；`lib/c.java:67` | 资源名白名单（禁分隔符/`..`/绝对路径）；下载文件名由客户端生成 |

## 4. INFO（正面/降级链）

- **无 Zip Slip**【已复核方向】：`.gfont` ZIP 条目只经 `getEntry(变换名)+getInputStream` 流式消费（`lib/b/e.java:151-152`、`:329`），条目名从不落盘 → 天然免疫。代价：条目数/大小无上限。
- **缺字降级链**（参考模型）：生成链单字缺字 → 0.3×字号空距跳过 + `losechar` 事件（`lib/b/i.java:599-602`）；word 编辑器方框占位；wordx 打开弹 `lose_font` + 替换；excel 只提示第一个缺字名；多字串递归降级深度≤1 无爆炸面。

## 5. 分诊

全部 8 条为**厂商侧**缺陷/观察；hutuji 无字体引擎、无 wordx 类文档格式、无云字库——对应面不存在，**零改动、零新增固件观察项**。`.gfont` 懒加载 + 缺字降级链作为未来「手写体/文字」功能的参考模型存档（与 findings/23-I7 同口径：增强候选，未采纳）。

## 6. 局限

- XTEA 密钥表 `y` 的字节值未逐字转录（按约定不复述密钥材料）；定性结论不依赖具体值。
- wordx 导出 NPE 为静态推断（字段赋值路径分析），未动态复现。
