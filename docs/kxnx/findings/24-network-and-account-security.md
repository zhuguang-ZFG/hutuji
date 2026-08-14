# 24 · 网络/账号/升级面深审：明文 HTTP 默认 + 带内密钥「加密」+ 三个无认证控制面

> 结论先行：奎享桌面端的网络面在**传输、凭据、远程控制**三层全部失守——主 API 默认明文 HTTP（`net/a.java:71`）；自研「加密」把 16 字节密钥明文前置进每个请求体（带内密钥，零强度）；KAT 机型内置 Jetty `:90` 绑 0.0.0.0 零认证（可远程启动雕刻）；「共享机器」LAN 面零认证；云字库/模板/反馈全部沿用同一明文通道。**对 hutuji 全部为反面教材或既有基线印证，hutuji 侧零动作。**
>
> **行号基准树**：`D:/Users/kxnx/kx/tools/dump_agent/decompiled_full/`（A 混淆体系，920 文件）。本树与 `kx/windows_hutuji/decompiled/`（findings/23 基准）共享文件行号一致（实测 `machine/grbl/a.java` 的 `g()=100` 两树均 312-314）。
>
> 日期：2026-08-14（第十八轮）。方法：子代理深审 + 主代理对高载证据回源码复核。【已复核】= 主代理亲读确认。

---

## 1. 网络面地图

```
net.a（HTTP 客户端：base URL + 60s keepalive 换 token + 带内密钥「加密」a(JSONObject)）
  → 业务接口：/kvenjoyweb/cgi_bin（登录）、drawsoftdo（act=1..8 分发）、drawsoftapp（生成/反馈）、template、veriycode
  → BosUtils（实为阿里云 OSS：STS 凭据经 drawsoftdo act=4 下发 → 客户端直连 oss-cn-beijing）
  → t/g「server fix」线程：登录框启动即拉 OSS maintenance.properties，可远程重定向 base URL
  → KAT/CardWriter 机型：KenjoyDraw.main 无条件起 Jetty :90（六个 servlet，零认证）
  → 「共享机器」：UDP 5000-5009 发现 + 同端口 TCP ServerSocket（零认证 PrintPck 直启打印）
```

## 2. CRITICAL

### C1. 主 API 全链路默认明文 HTTP【已复核】

- 证据：`net/a.java:71` `public static String a = ...a("url", "http://www.kvenjoy.com");`；`net/a.java:148`（业务 POST 一律 `String.valueOf(a)+"/"+接口` 拼接）；登录 JSON 带 `uin/psw(口令MD5)/code`（`net/a.java:186`）；模板上传 `uin`+`token` 走 HTTP header（`s/a.java:302`）。
- 细节：base URL 仅可被 `default.properties` 的 `url` 键覆盖；用户不改配置即全链路明文。嗅探局域网/运营商链路即可拿到登录凭据与有效 token，并可中间人篡改应答。
- **hutuji 对照**：cloud_bridge 只连 `wss://`、token 全程脱敏。印证 hutuji 基线；任何外向连接强制 TLS、禁止降级明文。

### C2. 请求「加密」= 带内密钥混淆，零强度【已复核】

- 证据：`net/a.java:74`（密钥 `d` = 实例启动时随机 UUID 经 `ac_0.c` 转 16 个 int）；`net/a.java:285-294`（**d[8..15] 与 d[0..7] 共 16 个密钥字符明文写入消息体前部**，再 `ac_0.a(payload, this.d)` 加密、base64 发出）；`encrypt/KvenjoyEncrypt.java:18`（JNA 加载原生 DLL）。
- 细节：密钥随每个包带内传输，观察者取前 16 字节即可解密全部流量并伪造任意请求；原生 DLL 只藏算法，密钥生成与协议全在 Java 侧，反编译即复刻整个客户端。响应解密用同一把 `d`（`net/a.java:309+`）。
- **hutuji 对照**：客户端侧任何自研「加密」若密钥可逆向/带内传输即无鉴权强度；鉴权靠服务端秘密 + TLS。本条可作反对此类提议的存档证据。

### C3. KAT 机型 Jetty `:90` 绑 0.0.0.0，六端点零认证【已复核】

- 证据：`lib/e/a/a.java:58-68`（`new Server(90)` 不限 host；注册 `/drawsoft`、`/write_template`、`/write_text`、`/write_svg`、`/machine_status`、`/login`）；`a/b.java:30-56`（唯一 filter 是「同时只放行 1 个写请求」的并发计数器，**非认证**——`this.b++ < this.a` 限流，超限回 `error:-3`）；`a/g.java:166`（`start=1` 且非 Printing → `d.f()` 直接启动雕刻）；`a/c.java:26`（`/login` 收 `uin`/`passwd` GET 参数转发云登录，应答区分 -1..-5 = 凭据爆破 oracle）。
- 细节：车间 LAN 任意主机可远程写入内容并启动机器；配合路由器端口映射即公网暴露。
- **hutuji 对照**：hutuji `/mcp` loopback 守卫 + `HUTUJI_HOST` 非 loopback 直接抛错的反面教材。本地服务端点要么 loopback、要么强制认证；控制类端点须设备确认。

## 3. HIGH

### H1. 「共享机器」LAN 控制面零认证【子代理证据】

- 证据：`p/e.java:47`（`new ServerSocket(n2)`，UDP 发现端口 5000+n 同端口 TCP）；`p/e.java:144`（`PrintPck` → `d3.f()` 直接启动远端机器打印）；`drawsoft/lib/remote/f.java:72`（UDP 探测只需 4 字节魔数 `$V`+版本）。
- 细节：opt-in 功能（勾选「开始共享」），但零 PIN/零确认：`FilePck` 可投递 G-code、`SettingsPck` 可改机器设置、`MachineListPck`/`StatusPck` 泄露机器清单。
- **hutuji 对照**：hutuji 无 LAN 服务；若未来加局域网能力，必须内置配对认证（PIN/设备确认），不学「发现即控制」。

### H2. 记住密码 = 线上凭据 MD5 明文落盘 + autoLogin 静默登录；token 明文进注册表【子代理证据】

- 证据：`t/a.java:183`（口令 MD5 即为发送的 `psw` 字段——存储值=线上凭据）；`t/a.java:197`（勾选后经 `Storage.flashStore` 写 `LoginData.json`）；`data/Storage.java:206-208`（Gson 明文 JSON 落盘）；`net/a.java:235`（token 以 `token_<uin>` 存 `Preferences.userRoot()/kdraw`，Windows 即注册表 HKCU 明文）；`t/a.java:155`（autoLogin 启动静默登录）。
- **hutuji 对照**：生产凭据只落 `/etc/hutuji/*.env` chmod 600 的反面教材。不存可重放凭据；存可撤销凭证。

### H3. 阿里云 OSS STS 凭据经明文通道下发，可截获冒用【子代理证据】

- 证据：`net/BosUtils.java:47`（act=4 取 `{ak,id,sessionToken,expiration}`）、`:62`（`new OSSClient("oss-cn-beijing.aliyuncs.com", ...)` 客户端直连）；`C.java:423`（上传 `user_cloud/<uin>/gcode/*`，bucket 名来自配置默认 `drawsoft`）；`r.java:72`（列/下载 `config/*` 机器配置，下载后客户端应用）。
- 细节：STS 通道即 C1/C2 的明文+带内密钥协议；凭据有效期内可冒用执行客户端同权限操作——含 `config/` 与字体对象的读取，若 STS 策略过宽还可覆盖写（供应链投毒面：配置下发到车间机器）。
- **hutuji 对照**：第三方云凭据必须走 TLS 通道获取、不入日志；云访问应服务端代理而非客户端直连。

### H4. 远程配置通道：maintenance.properties 可重定向 base URL（仍 HTTP）【子代理证据】

- 证据：`t/g.java:36`（登录框构造即 GET `https://drawfont.oss-cn-beijing.aliyuncs.com/maintenance[_en].properties`）、`:50`（`enable=true` → `net.a.a = service_url`）；`t/f.java:72`（登录两连败后提示切「备用服务器」= 同一远程 URL）。
- 细节：厂商（或控制该 OSS bucket/DNS 者）可把全量用户流量（含凭据）重定向到任意主机。无内容签名、无目标白名单。
- **hutuji 对照**：hutuji 无远程配置通道，保持现状；若引入必须内容签名 + 目标白名单 + TLS。

## 4. MEDIUM / INFO

| # | 发现 | 证据 | hutuji 对照 |
|---|------|------|-------------|
| M1 | 反馈遥测 act=2 可附**整屏截图**（含其他窗口）+附件，明文追加在「加密」头后 | `as.java:142`（Robot 截主屏）、`:192-197`、`:213` | 上报最小化；不自动截屏；附件走 TLS 且先声明 |
| M2 | 用户内容上传面：act=3 设计区 2000×2000 JPEG、act=5 AI writer 自由文本、模板 GZIP+token header | `o/d.java:388`、`b.java:42`、`s/a.java:302` | 出网 payload 字段级脱敏 + 大小上限（hutuji description ≤200 字符先例） |
| M3 | 原生加密 DLL 解压到程序目录、无完整性校验，可本地替换执行（用户权限） | `KenjoyDraw.java:235`、`av.java:204`（REPLACE_EXISTING 无校验） | 可执行/原生库落盘须受保护目录或签名校验 |
| M4 | **设置导入 ZIP-slip**：entry 名直接 `new File(parent, name)` 无过滤，可 `../` 越界写（覆盖 default.properties 指 base URL 到攻击者） | `KenjoyDraw.java:1296-1300`【已复核】 | 解压归档必须校验规范化路径在目标目录内 |
| I1 | 遥测/许可面：act=7 公告轮询、act=8 机器 uuid 注册/到期、veriycode 验证码 | `w_0.java:37`、`machine/ui/f/l.java:113`、`net/a.java:429` | 遥测显式声明数据项；uuid 非用户名 |
| I2 | 无应用自更新；无 trust-all TLS 配置（但主链路本就无 TLS）；keepalive 60s 换 token；GET 参数拼接不 URL 编码 | `net/a.java:448`、`:93`、`:395` | hutuji 维持「无自更新/受信通道」；正面基线保留 |

## 5. 分诊

全部 12 条为**厂商侧**缺陷/观察；hutuji 对应面均有更严基线或干脆无该面（无 LAN 共享、无远程配置通道、无自更新、无本地 HTTP 服务）。**hutuji 代码/配置零改动。** 固件观察项新增 0 条。

## 6. 局限

- 服务端行为不可见（STS 策略宽度、bucket ACL、服务端是否另校验签名均未知，文中按客户端证据给出「若…则」条件句）。
- KAT Jetty 面与共享机器面仅静态证据，未实机触发。
- 「加密」算法本体在原生 DLL 内未逆向；结论不依赖算法强度（密钥带内即零强度）。
