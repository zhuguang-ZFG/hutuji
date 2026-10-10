# 量产工位

量产型号为nopaper/Freenove，当前参数选择为速度6000/6000/1200、加速度3000/3000/3000。`nopaper-freenove-v1.json`是版本化配方；首次建档后不得原地改配方，变更另起id并重新做首件。参数及4小时/30次/≤1mm/零漏笔失步试行门槛均已由用户批准，合格仍需实际测量。

## 1. 发布包

从实际SDK/PlatformIO构建取得每个分区镜像，编写本机inputs JSON（不要提交绝对路径、真实设备身份或凭据）：

```json
{"s3":{"commit":"40位提交","version":"hutuji.1.x.y","segments":[{"offset":0,"source":"bootloader.bin"},{"offset":32768,"source":"partition-table.bin"},{"offset":53248,"source":"ota_data_initial.bin"},{"offset":131072,"source":"xiaozhi.bin"},{"offset":8388608,"source":"generated_assets.bin"}]},"grbl":{"commit":"40位提交","segments":[{"offset":4096,"source":"bootloader_dio_40m.bin"},{"offset":32768,"source":"partitions.bin"},{"offset":57344,"source":"boot_app0.bin"},{"offset":65536,"source":"firmware.bin"}]}}
```

用实际路径替换示例。运行`python -m scripts.factory.package --recipe production/nopaper-freenove-v1.json --inputs <inputs.json> --output <全新发布目录>`。输出自包含目录，验证实际分区表、芯片、版本、DIO、大小、SHA与NVS空洞；校验失败产生INVALID，不得流转。不接受只给app的首烧包。

## 2. 建档与工位防串台

每台整机贴SN，读取S3 STA MAC、Grbl STA MAC和AP BSSID。不要根据COM编号、外观或“最后一个端口”猜身份。AP BSSID须由该台实际读回，不能简单推导STA+1。尚未写入工厂身份之前，每工位只上电一套；也可采用独立路由/AP和屏蔽工位。

`python -m scripts.factory --db <产线数据目录/line.db> register --sn <SN> --batch <批次> --s3-mac <MAC> --grbl-mac <MAC> --ap-mac <BSSID> --recipe <recipe.json> --manifest <发布目录/manifest.json>`。

数据库禁止重复SN/MAC。工厂身份不替代P-256用户绑定，私钥仍由设备生成；禁止从样机拷贝NVS。

## 3. 首烧与返修

执行`python -m scripts.factory --db <db> flash --sn <SN> --kind s3 --mode first --port <已核端口> --operator <工号> --recipe <recipe> --manifest <manifest> --evidence-dir <受控目录> --nvs-generator <IDF/nvs_partition_gen.py>`先看计划；加`--execute`才执行。Grbl换`--kind grbl`。两板均须由操作员正常进入下载模式；工具不自动拉DTR复位运动机。

首烧要求NVS全FF，S3生成每台独立factory NVS；已有配置的设备不能当空白机重烧。返修先`rework --sn <SN> --operator <工号> --reason <原因>`，然后用`--mode repair`保留NVS并备份整片；恢复启动槽后逐分区写入/验证。S3返修必须有本台工厂记录。原有工程机迁入产线需要专门人工身份迁移，禁止直接擦NVS。

失败留下pending/fail和备份，不能再次盲刷；查清原状态后新返修代次。备份可能含设备私钥/用户凭据，工位目录必须受限，禁止上传公开仓库。烧录后按SOP正常重启，重新确认原点。

## 4. 参数与测试

先设置`HUTUJI_FACTORY_SESSION_TOKEN`为正常登录并绑定本台的工位账号会话；`inspect --sn <SN> --operator <工号> --recipe <配方> --manifest <清单> --evidence-dir <目录>`通过既有Portal只读核对S3版本、SN、Grbl配对和授权，自动登记pairing/authorization。账号切换到其他设备会被原控制路由拒绝。

`parameters --sn <SN> --port <Grbl端口> --operator <工号> --recipe <recipe> --evidence-dir <目录>`先只读预演，加`--execute`才逐轴写配方，确认安全只读指纹、全表读回和其他参数不变。工具不写授权、步距、行程或轴反向。

`test-artifacts --output <新目录>`生成20mm标准G-code、SVG预览和测量模板。尺寸测量必须使用原G-code，不能把SVG上传后重新适配纸张的结果当20mm标尺；SVG仅供预览。固定测试件通过`publish_fixture`接入既有纸张快照/能力文件/设备确认链，不直接串口运动。记录边长、对角线、漏笔、失步和照片；老化记录hours/jobs/errors。`record --sn ... --check paper --result pass --operator ... --evidence <文件> --detail <测量JSON>`只接受带本台SN的资料。flash/parameters/pairing/authorization项只能由执行器产生pass。

测量JSON须填写`sn`与`status`显示的当前整数`generation`，旧代次记录不能在返修后复用。参数工具只认完整STA报告中的实际MAC，Wi-Fi名里的MAC文字或AP地址不能证明本台身份。

`status --sn`显示缺项；`export --sn --output <JSON>`导出完整档案；`batch --batch <批次> --output <CSV>`导出批次状态。每日`backup --output <全新备份.db>`，并复制证据目录至受控备份位置；仅数据库备份不能替代照片/原始日志。

推荐用`archive --output <全新档案目录>`一起保存台账和所有历史证据；事件证据创建时复制为独立快照，pending文件后续改写不会污染历史。整片烧录备份和放行报告作为附件带SHA/大小保存，失败台也保留已读取备份。归档副本用相对路径，可整目录转移到售后工位，执行`--db <档案目录/line.db> status/export`核验；恢复备份取导出的`attachments`路径，正文中的旧路径仅供历史追溯。若任一历史证据缺失/摘要变化，归档拒绝并保留未完成目录供排查。

## 5. 放行与客户资料

`delivery --details <真实资料JSON> --output <新目录>`生成客户说明，信息格式参考`delivery-details.example.json`；型号/主体/联系方式/保修/隐私/源码链接及真实签核报告缺一都拒绝生成。示例为待定，不能印刷。

`release --sn <SN> --operator <签核人> --recipe <已批准配方> --delivery <真实资料JSON>`核全部检验、证据SHA、老化/纸面阈值后才放行。法规报告、实际纸面与手机测试不能由脚本自动填“通过”。返修自动撤销放行并要求整表重验。

`released=1`表示本代曾签核，仍须检查`missing`。许可、电气、无线报告任一缺失、摘要/大小改变或附件登记缺项时，`status/export/batch`会列出`release:attachments`；不得按历史放行位单独判定当前档案完整。

## 6. 固件灰度

Portal目录的稳定board条目可附`rollout: {id, percent, devices, candidate}`。`devices`为MAC白名单；其他设备按id+MAC稳定分桶，percent为整数0–100。candidate是普通版本条目，须填写version/relpath/sha256/size。先percent=0+明确试点名单，记录实际成功率后扩大；删除rollout撤回候选。设备直接读取latest仍指稳定版本，灰度候选仅由已绑定Portal发起，不能提前把latest改成候选。灰度不自动刷机，仍经过原busy、板型和会话闸。

工具依赖：运行Python需pytest/pyserial；设置`HUTUJI_FACTORY_IDF_PYTHON`为已安装esptool及esp-idf-nvs-partition-gen的IDF Python。S3验写后hard-reset启动，Grbl正常断电重启。返修要求未改写的bootloader/分区/资源与包相同，否则转专门迁移。服务中断遗留操作锁时，确认机器停稳并停止旧进程后用`resolve --sn ... --operator ... --evidence ...`解除，再新建返修代次；不能按超时自动解锁。

中文SSID使用小派HTTP配网。串口工具会拒绝非ASCII及实时控制保留字符`? ! ~`，不能通过换编码绕过。HTTP配网保留SSID/密码首尾空格；手机实测矩阵仍按`docs/production-mobile-acceptance.md`单独验收。
