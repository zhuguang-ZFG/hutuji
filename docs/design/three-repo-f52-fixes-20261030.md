# F52 三仓代码审查修复（2026-10-30）

用户在三仓只读审查后批准修复、提交推送、部署验证。本批只闭合新确认的三项会话/资源生命周期缺陷，不改变换纸、运动、引脚、授权、OTA 镜像校验或 F50-02 延后决策。

## 修复

### Hub / Portal OTA 会话代次

`get_ota_status()` 在发起设备状态查询时冻结当时的 `session_id`。旧会话过期或条件更新期间若数据库已换成下一轮会话，本次旧设备正文不再参与新会话的 failed/progress/version 结算，只返回当前账本状态。

修复前的确定性交错：旧 uncertain A 查询得到 `ota.failed`；另一请求结算 A 并领取 upgrading B；A 的过期路径重读 B 后继续消费旧正文，最终把 B 的目标版本保留但 state/reason 写成 `failed/old_failed`。新回归覆盖真实 Store 与 `get_ota_status()`，旧码 RED、新码 GREEN。

### S3 WebSocket 回收资源上界

S3 `38367dd` / `hutuji.1.0.53`：`RetireWebSocketAsync()` 的 holder 使用 `new (std::nothrow)`，低内存不再让 `std::bad_alloc` 逃出关闭/析构路径；同时最多允许两个一次性 `ws_retire` 任务在途，第三个及 holder/任务创建失败回退到原有同步有界析构，避免断连风暴按每次 4 KiB 栈无界放大。

宿主回归延迟执行回收任务，三次并发退休精确断言“两异步、一同步”；另钉 holder 必须是 nonthrowing 分配。F50-02 的应用层通道代次修法仍延后，本批不借机改变协议层保守语义。

### 量产 Grbl Telnet 输出归属

量产 `59968a47`：普通 `CLIENT_ALL` 不再自动获得 stale 会话回退权，领取任务产生的 `$H`/调试/位置等输出继续受旧会话屏障约束；真正无主异步的 ALARM 改走显式 `client_write_broadcast()`，在旧会话失效时仍可收养并送达当前连接。

真码宿主用例覆盖：旧命令 `CLIENT_ALL` 不得进入新连接；随后 ALARM 必须进入；新连接自己的 `ok` 仍正常。该修法不改变串口/蓝牙/WebSocket 广播和 ALARM 内容。

## 软件验证

| 对象 | 结果 |
|---|---|
| Portal 全量 | 635 passed，2 个既有 warning |
| S3 `scripts/tests` unittest discover | 447 passed |
| 量产 Grbl host | 87 passed |
| S3 Freenove 构建 | 3,105,840B，SHA-256 `26887fae…1ee0` |
| S3 立创构建 | 3,140,512B，SHA-256 `8a384708…4bde` |
| 量产 Grbl release | 1,849,392B，SHA-256 `ba990bfd…3c73` |
| fz `product_host` / `qemu_startup` | PASS / PASS |

fz `standard` overall 仍为 fail：QWEN evidence 固定要求 Python 3.10、当前门禁解释器为 3.13；Wokwi 启动层缺少可靠成功回执；硬件仿真首轮 `plant_feed_hold` 时序窗口失败，随后同一单项确定性复跑 PASS。不得把这些环境/仿真边界改写成 standard 全绿，也不覆盖真机 HIL。

## 发布边界

- 外仓提交已推：S3 `38367dd`，量产 Grbl `59968a47`。
- Hub 提交、双机部署、S3 固件 catalog/设备 OTA、Grbl OTA/48 项参数复核与三仓 CI 在本文初次落盘时尚未执行；后续只按真实回执更新 readiness/deployment。
- 另一 S3、普通 Grbl、运动与机械面不在本批范围。实体纸面、老化、工厂配对与交付签核继续 Hold。
