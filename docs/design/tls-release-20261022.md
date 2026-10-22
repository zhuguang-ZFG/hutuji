# TLS下载失败排查与优化发布（2026-10-22）

用户明确要求处理尚未部署/上传的体验优化，找出固件TLS下载失败原因并修复改进。承接experience-optimization-20261022与C22；单代理执行，不创建不存在的.trellis任务。目标为当前Freenove MAC b8:1f:3f:c3:70:bc，量产Grbl只读机设/状态，另一S3继续排除。

已发现SDK对象所有权缺陷：受管EspSsl::Disconnect先close(sockfd)，等待接收线程后esp_tls_conn_destroy仍按相同sockfd执行close。目标ESP-IDF v6.0.1 esp_tls.c的destroy真实包含第二次close；首次close到destroy间如其他WiFi/MQTT/HTTP任务领取同一描述符，旧TLS析构会关闭新连接。这可导致NET_RECV_FAILED(-0x004C)，与内存分配失败(-0x0084)不同；现有关闭后日志不能一概当作本次下载失败。下一步真实函数宿主交错复现、修复短生命周期关闭所有权并追加errno/堆诊断、同目标SDK构建与实机下载验证。原始OTA失败窗口未抓到，未宣称唯一根因。

授权范围：已验证Hub pipeline/sketch_render、小程序wifi/firmware/job_progress优化与上传版本0.3.32；双机从精确commit的LF blob打包deploy+verify/离线生产烟测；S3受管TLS关闭补丁的tracked patches及对应宿主测试/应用工具、版本1.0.47、两交付板构建及目标板升级验证。生成目录仅通过明确补丁应用，不直接将受管源码纳入Git。所有固件分区/版本/SHA/OTA占位/机械运动/设置/身份纪律保持。

本机证据results/local-only/2026-10-22-tls-release/，临时工具tmp/tls-release-20261022/。未完成动作不预记成功，首次失败与后续验证分别保存。
