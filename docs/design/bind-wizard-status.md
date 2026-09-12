# 一次扫码配网绑定向导

## 目标

消费者只需扫描设备屏幕二维码、选择家庭 Wi‑Fi 并输入一次密码；设备重启、回连家庭网络、认领和绑定由系统自动完成。

## 状态接口

`GET /draw-upload/api/user/bind/claim/status`（登录且已绑定手机号），query 带 `mac`，新版还须完整 `device_id/public_key/nonce/identity_version=2`。身份与签名的规范见 [device-identity-rollout.md](device-identity-rollout.md)，本页不另立算法版本。

返回状态：

- `claimed`：认领已登记，等待设备 announce。
- `bound`：绑定完成，附带脱敏 `binding` 摘要。
- `expired`：认领窗口已过期，需要重新扫码。

MAC 只用于定位设备，不是身份证明；接口不返回其他用户信息。成功须匹配本次 nonce，不能拿其他设备或已解绑的历史成功顶替。小程序在提交配网后每 5 秒轮询一次，最长约 5 分钟并受服务端到期时间约束，超时显示“重试绑定”。

## 前端行为

二维码 URL 的查询参数按键名解析，支持 `options.q`、参数任意顺序，并兼容旧版只含 `s`、标准 `WIFI:T:` 及纯 SSID 码。先解析并短期保留完整扫码上下文，再完成登录/手机号闸门后连接设备；新码整体替换旧码。Wi-Fi 密码仅在页面内存，成功或卸载清空。

claim 网络/5xx/429 自动指数退避，最多额外 5 次；设备 AP 与家庭网络切换各有一次自动重试。提交后 AP 消失只进入等待，不显示成功；服务端连接探活未就绪回 `503 binding_pending` 保留证明。仍失败时保留扫码上下文并显示“重试绑定”，不要求用户理解 claim/announce 或复制 token。身份串台、认领过期和跨账号冲突分别提示，只有 `binding_conflict` 可弹强制换绑。

页面 generation 和登录 generation 双重守卫覆盖网络、TCP、系统 Wi-Fi 回调与定时器。`ensureSession()` 会同步开启登录代次，捕获时机必须在其调用之后；旧授权任务的 finally 只能清自己的在途标记。系统限制自动切网时提供设置入口，实际 iOS/Android 行为须真机验收。

## 多设备与恢复

portal 维护 `user_device_bindings`（设备 MAC 唯一、账号可多台）并保留旧
`device_bindings` 作为迁移镜像。`GET /api/user/devices` 返回当前账号设备列表，
`POST /api/user/devices/select` 切换当前设备；解绑接口可携带 `device_mac`，不影响
同账号其他设备。旧客户端继续使用 `/api/auth/me.binding`，服务端按 active 设备返回。

token 自愈、机型、探活和推送时间均回写发起时捕获的 user_id + MAC，只在目标为当前设备时同时更新旧镜像；不会因当前选择改变而回写另一台设备。跨账号换绑必须 `force:true` 且持有新会话证明。Wi-Fi 重配保留密钥；全擦 NVS 生成新密钥时，由原所有者恢复，或原所有者先解绑再转让；云端解绑不会伪造设备侧恢复出厂。
