# 设备身份与一次扫码绑定

## 范围与当前状态

用户于 2026-09-12 要求完成扫码配网、自动认领、恢复、多设备，并明确要求修改 S3 固件。本地软件与三板候选构建的证据见 `../release-readiness.md`；生产部署、固件刷写与手机实测单独登记，不能从本设计推导已完成。

本文件 supersede 早期混用 P-256/Ed25519 的未发布草稿。设备身份扩展采用 **identity_version=2、ECDSA P-256/SHA-256**。既有 DRAW-BIND v1 路由及手动短码入口保留；身份扩展版本与板间 Telnet v0.5 无关。

## 密钥与配网会话

- S3 使用 ESP-IDF PSA Crypto 生成独立 P-256 私钥，NVS `hutuji_identity` 存储；私钥不进入二维码、HTTP、日志或仓库。密钥损坏/写入失败停止身份流程，不能重新生成后静默冒充旧设备。
- 公钥是 65 字节 SEC1 非压缩点；`device_id` 为其 SHA-256 前 16 字节的小写十六进制。公钥、16 字节随机 nonce 和 64 字节 `r||s` 签名均使用不带填充的 base64url。
- 每次本地进入配网或打开绑定页生成新 nonce 与绑定码，并持久化为一个记录。网络切换或重启后的 announce 复用该记录；完成后只清会话，Wi-Fi 重配保留设备密钥。
- 配网二维码增加 `v=2&d=<device_id>&k=<public_key>&n=<nonce>`，保持 `s/m`；绑定二维码除 `c` 外也带同一组身份字段。小程序授权返回、切网、重试必须保留同一份扫码信息，扫描另一码时全部替换，不能混用旧 MAC/nonce。

## 服务端挑战与签名

设备在 HTTPS `POST /api/device/bind/challenge` 提交 `mac/bind_code/device_id/identity_version/public_key/nonce`，取得随机 `challenge` 与服务端 `expires_at`。同一设备 nonce 的重复请求返回同一挑战且不延长窗口；窗口最多 600 秒。这样首次配网无正确时钟也可校验新鲜度，不依赖设备本地时区。

announce 增加以上身份字段和 `challenge/signature`。签名的 UTF-8 文本严格为：

```text
hutuji-bind-v2|device_id|nonce|challenge|bind_code|mac|plotter_sku|sha256(device_token)
```

MAC 统一小写带冒号；code 大写；token 去首尾空白后按 UTF-8 求 SHA-256，摘要小写十六进制。签名覆盖 token 和机型，不能替换令牌后复用签名。服务端拒绝未知版本、部分字段、非规范编码、错误公钥、过期/不匹配挑战和错误签名。

## 所有权与防重放

- 手机 claim 携带二维码的 `device_id/public_key/nonce/identity_version`；它与设备签名会话逐项匹配，不能只按 MAC 撮合。claim/announce 任意先后到达均可完成。
- 首次成功绑定才登记 MAC 与公钥关系；未认证挑战请求不能提前占用身份。已登记 MAC 即使关闭全局强制开关也不能回落无签名 announce。
- nonce、短码单次消费；重复 announce 不重开已消费会话、不刷新到期时间。完成响应丢失时可幂等读到成功，但不能据旧证明换绑或恢复已解绑设备。
- 跨账号转让须同一设备的新会话证明与 `force:true`。完整擦除 NVS 后的新密钥不能冒充旧身份；原绑定账号持新二维码可重新登记，其他账号须先由原账号解除绑定。普通换 Wi-Fi 不擦密钥。
- 一个账号可有多台设备；当前设备只决定默认控制对象。token 自愈、机型更新和推送时间回写绑定的具体 MAC；不能更新同账号的其他设备。解绑默认只解绑当前设备。
- 签名确认不等于连接就绪：上报 token、历史正常 token 或新铸 token 必须有当前探活成功；未就绪返回 `503 binding_pending`，不消费证明、不改原绑定。旧密文损坏时可凭新签名会话重新铸造。unsigned 老固件的兼容行为不用于解释新链路成功。

## 迁移与验收

先部署兼容版本 portal，再发布同版本小程序和 S3 固件。`DRAW_PORTAL_DEVICE_IDENTITY_REQUIRED=0` 只兼容尚未登记身份的旧设备；新设备始终校验签名。整批设备升级并过手机实测后才启用 `=1`。旧固件没有身份字段时，强制模式拒绝绑定，不能偷偷回落。

必要软件验证：真实 P-256 签名正反例、token/机型篡改、未知版本、二维码串台、nonce 到期与消费后重放、双向到达、换绑/擦 NVS 后恢复、旧库迁移和多设备 token 隔离；固件 host 测试及 lichuang/Waveshare/Freenove 受影响板型构建。iOS/Android 切网、重启、扫码密度、NVS 掉电及出厂保护配置另做实机记录。

部署前对 SQLite 做一致性备份，整批同步 `app/config/models/store/firmware_ota/device_identity` 和小程序页面/工具；禁止只发布接口而漏 Store 方法。数据库迁移先补旧列，再按列名 `INSERT OR IGNORE` 回填，保留现有主表归属。禁止新旧 Store 进程同时写同库。已登记身份后回滚 UI 可以，服务端必须保留验签、消费台账与多设备主表；直接回滚到无身份版会失去防降级保障。恢复备份必须停写并评估备份后新绑定，不能无声丢掉所有权事务。

## 可执行契约与验收

### 1. 范围 / 触发

本批跨 S3、HTTP、SQLite 和小程序，新增身份与多设备模型；不改 Grbl、§5 G-code、Telnet/换纸、出厂 eFuse。候选固件版本 `hutuji.1.0.11`，不因此更新生产 OTA 推荐目录。

### 2. 签名与接口

- `parse_identity(*, device_id, public_key, nonce, identity_version) -> DeviceIdentity`；`verify_announce(identity, *, challenge, signature, bind_code, mac, plotter_sku, device_token) -> None`。
- `POST /api/device/bind/challenge`、`POST /api/device/bind/announce`；用户侧 `POST /api/user/bind/claim`、`GET /api/user/bind/claim/status`、`POST /api/user/bind/complete`。
- `Store.complete_binding(..., user_id, force, device_token_enc, token_probe_ok, now, require_claim)` 在一个事务内消费会话/nonce、登记身份并转移所有权；HTTP 完成路径按 MAC 分段互斥，避免同进程并发重复铸 token。
- `Store.select_binding_for_user(user_id, device_mac)` 只选本人设备；`delete_binding_for_mac(device_mac, *, user_id)` 同事务核验所有权；各回写函数携带 `device_mac=`。

### 3. 请求 / 响应 / 配置

完整字段及字节序见本文「服务端挑战与签名」和 `../protocol.md` §10.4.2–4。匿名设备请求不能直接登记所有者，用户请求仍经登录+手机号闸。QR 不含 token/private key；客户端可保存的只有整份扫码信息、阶段和原始保存时间（10 分钟）。旧 MAC-only claim 默认 1800 秒，新身份 claim 受最多 600 秒挑战窗限制。配置是 `DRAW_PORTAL_DEVICE_IDENTITY_REQUIRED`、`DRAW_PORTAL_DEVICE_IDENTITY_NONCE_TTL`、`DRAW_PORTAL_BIND_CLAIM_TTL_SECONDS`；服务端 token 自动铸造依赖 `DRAW_PORTAL_XIAOZHI_TOKEN` 的既有续期机制。

### 4. 校验与错误矩阵

| 条件 | 结果 / 副作用 |
|---|---|
| 非规范字段、未知版本、错误签名/挑战 | announce 401；不登记身份、不消费证明 |
| claim/complete 身份串台或非原所有者重置密钥 | 409 `identity_mismatch`；不铸 token、不弹自动转让 |
| 他人设备、新证明但未确认转让 | 409 `binding_conflict`；保留会话，确认后 `force:true` |
| 过期/已消费证明且不存在原完成事务 | 410 `binding_expired`；不得复活 |
| token 缺失、失效或新铸后仍不可调用 | 签名流 503 `binding_pending`；保持原绑定与未消费证明 |
| 5 分钟 MAC/IP 新挑战达到 20 个 | 429 `binding_rate_limit`；重复获取原挑战不扣额 |
| 旧镜像与主表归属冲突 | 主表优先；选择旧镜像不得覆盖新所有者 |
| 未拥有的目标设备选择/解绑 | 404；不改他人绑定 |

### 5. 正常 / 基础 / 异常案例

- 正常：claim 先到或 announce 先到，P-256 验签+token 当前探活通过 → 同事务落绑定，返回本次 `bound`；丢响应后的同 nonce 重试可恢复成功。
- 基础：兼容开关为 0、MAC 尚无身份、旧二维码和旧 announce → 保留短码/旧 claim 行为；不能把此兼容例套用到已登记身份。
- 异常：AP 发完断开只进入等待；用户中途退出、重扫、切换当前设备，旧回包不能恢复旧用户、显示假成功或写另一台 token。

### 6. 必要测试与断言

- `tests/test_device_identity.py`：真实 `cryptography` P-256 签名；部分字段/篡改/强制模式拒绝、nonce 固定窗口、换绑/重置/退役、防重放、空 token 自动铸造、当前探活与失败重试。
- `tests/test_device_bindings.py`：旧表缺列/列顺序、重复迁移、遗留镜像提升及冲突不夺权、user+MAC 回调隔离、转让后旧账号解绑、注入写后异常验证事务全回滚。
- `tests/weapp/wifi.test.js`：真实模块+可控时钟，QR 全入口、密码不存储、中文/emoji UTF-8 分片、chunked/截断/上限、单次完成、授权换代/重扫/卸载、AP 断连不假报成功。
- 固件 `scripts/tests/test_hutuji_bind_identity.py` 编译身份实现与 host PSA/NVS 替身，测密钥/会话生命周期及故障；替身**不做真实 PSA 密码学运算**，不能称设备密码学已实测。三板真实 SDK 构建另行留日志/哈希。

### 7. 错误写法与正确写法

```python
# 错：回调执行时再取当前设备，用户切换后会写错目标。
store.update_binding_token(user_id, device_token_enc=enc)
# 对：请求开始即捕获绑定，迟到回写也只能命中当时的 user + MAC。
store.update_binding_token(user_id, device_token_enc=enc, device_mac=binding.device_mac)
```

```javascript
// 错：在 ensureSession 前捕获登录代次，会误杀合法的新登录。
// 对：调用同步开启本轮登录，再捕获；异步回调额外校验页面代次。
const login = getApp().ensureSession();
this._sessionGeneration = getApp()._sessionGeneration;
login.then((me) => { if (!this._current(generation)) return; /* 继续授权 */ });
```

## 故障复盘：成功判定与兼容镜像

### 1. 根因分类

**B 跨层契约 + D 测试缺口 + E 隐含假设**：旧版允许 token 未就绪落绑定，被新签名流程继承后误报成功；缓存探活标记与铸 token 字符串均不等于当前可调用。手机号辅助闸漏了会话代次，迟到响应复活旧用户。旧镜像补入主表若用 REPLACE，会把历史行当作所有权真值。

### 2. 为什么初步修复不够

只挡空 token+铸新失败，仍漏历史 token 失效与新 token 尚未在线；只在页面末尾挡回包，挡不住辅助函数先写全局用户态。测试只 stub `ensureSession` 不换代，又会漏掉真实登录每轮同步递增 generation 的事实。新增真实登录链和两类 token 探活回归后逐一钉住。

### 3. 预防机制

| 优先级 | 机制 | 动作 | 状态 |
|---|---|---|---|
| P0 | 运行时 | 签名+当前探活才消费证明，失败 pending | 已实现并有回归 |
| P0 | 数据事务 | 主表冲突不被旧镜像替换，回写限定 user+MAC | 已实现并有回归 |
| P0 | 回调归属 | 手机号辅助闸与页面双代次、在途标记归属 | 已实现并有回归 |
| P1 | 测试 | 真实 P-256、真实小程序登录链、写后故障注入、三板 SDK 编译 | 证据见 readiness/inventory |

### 4. 同类问题范围

核对 token/探活/机型/推送回写调用面（含 OTA）时都检查捕获的设备；测试网络请求按 URL 匹配，不依赖「第一个请求」——订阅配置预取曾使字体测试误喂回包。host 桩不能替代真实 SDK 对 `Http::SetContent(std::string&&)` 的编译检查；本批真实构建检出了左值接口不匹配，已改 move 传参。

### 5. 知识落点

- [x] 本设计保留可执行签名、错误矩阵和回归断言。
- [x] `../protocol.md` §10.4 同步新旧模式、token 就绪、回调归属与多设备真值。
- [x] `../../draw-portal/README.md` 同步测试命令与整批部署要求。
- 本仓无 `.trellis/spec` 或配套模板，直接维护已有契约，不初始化额外工作流；提交由用户决定。

## SDK 依据与限制

2026-09-12 查阅官方 ESP-IDF v6.0.1 本地源码镜像（`D:/zhugu-home/.espressif/v6.0.1/v6.0.1/esp-idf`），来源 <https://github.com/espressif/esp-idf/tree/v6.0.1/components/mbedtls>。PSA `crypto_values.h` 定义 ECDSA 签名字节为大端 `r||s`；当前构建配置启用 `CONFIG_MBEDTLS_ECDSA_C`、`CONFIG_MBEDTLS_ECP_DP_SECP256R1_ENABLED`。已检索 builtin 源码与配置，Ed25519 只有通用标识/类型声明，不能据此假定有可调用实现。

此依据适用于本地 ESP32-S3/IDF 6.0.1 构建，不是板上实测。独立软件密钥可以证明请求持有者和已登记设备一致，但不提供厂商出厂证书链；未启用 flash/NVS 加密与 Secure Boot 的设备不能宣称抗物理提取。eFuse 写入与出厂签核不在本次自动操作内。
