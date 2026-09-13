# 小程序控制故障、代码与合规审查（2026-09-13）

用户追加：「小程序上速度拖动不了啊，好好审查一下小程序的问题，是否合规合法；审查代码问题；优化用户体验，解决bug」。保留前段三仓任务及 X/Y=10000、Z=1200 的设置；本批优先修复实际控制页，再审查其他页面与隐私/审核面。

## 已有证据与修复范围

1. 截图已显示三轴界面，但均为「未读取」。生产公网与本机接口一致：`/api/auth/me.binding.device_mac` 是展示用打码串；控制页把它当完整 MAC 传给调速接口，触发设备切换保护，随后静默吞掉错误。只用完整 MAC 的测试桩遗漏了实际返回格式。
2. `/api/auth/me.devices` 已向本人返回完整设备 MAC；增加明确的 `active_device_mac` 指向当前控制目标，保留展示打码字段。前端不得按打码前后缀猜目标，也不通过省略目标字段绕过设备切换检查。
3. 控制页轮询可能重叠，较晚发起的请求使前一轮结果一直过期；速度读取失败缺少解释与重试。改为串行轮询、可见错误及重试，补登录冷启动和设备切换回归。
4. 进一步审查主要页面的会话隔离、私有图片、录音结束、重复提交、隐私协议入口、AI 标识、实际数据共享与官方审核要求。确认问题后逐项登记；没有运行证据的资质或法务项目不写成合规已通过。
5. 收藏页 `KINDS` 未传入 data；下载只更新 jobs、渲染却读 filtered；长按删除把筛选索引用于原列表。按稳定 savedId 更新并重新筛选，补失败提示与会话/列表代次隔离。
6. 聊天页快速松手早于录音 onStart 时不会 stop；离页未停止录音，旧 ASR 可借新会话继续 voice/turn。记录每次按住的归属，松手/离页/退出后迟到的 start 必须 stop，已作废录音不得上传或调用工具；聊天、个人资料回包与登录代次隔离。
7. 资料页旧回包会复活退出的资料，当前设备尾号使用不存在的字段；增加可见失败与重新登录、隐私指引和微信客服入口。头像当前为半公开能力路径，不能误判为缺认证头导致白图。
8. 体验版页脚仅显示「体验版」，无法定位版本；统一编译版本兜底。发布配置恢复域名校验，实际 MP 域名声明仍需开发者工具/手机复核。
9. 创作页精修轮询调用未定义的 `refineDelayMs`，应为页面方法；分享令牌晚到可覆盖新作品，AI 文本编辑一次就移除来源标识。补实际轮询与旧回包测试，标识保留到文本被清空。创作、画板、小派设置、固件页同样补页面/会话边界，升级页离开后的旧回包不得重启轮询。
10. 生产进程与配置确认 `HUTUJI_TG_UPLOAD_ENABLED=1`，数据库有 1 件上传态收藏和 2 条归档台账（只查聚合数，没有查看用户图片）；源码无单独同意记录，文档只有联系运营退出。第三方副本可能含用户个人信息，不能以 caption 不含账号就推定匿名。当前审查授权下先将双机新增上传开关关闭，保留原副本、台账和删除能力；不自动删历史用户数据。注册监控移除完整设备 MAC。再次启用前须补真实共享告知、适用处理依据、单独同意与相应评估证据，不能以规避审核措辞代替。

本批初始代码白名单：portal `app.py`（本人当前设备标识与控制目标校验）；小程序 control 三文件、`utils/phone_gate.js`、profile 三文件；对应 tests；本设计、协议、readiness、inventory 和审核指引。其他文件只有查明问题并补入清单后才修改。

## 验证要求

- 回归必须从真实形状的 `/api/auth/me` 开始，覆盖打码展示与完整控制标识、未读到/失败/重试、慢网、离开页面、退出登录和设备切换。
- 本机与公网分别验证；小程序以真实开发者工具组件及正常页面链路检查，不能把直接 API 调用当成手机拖动已通过。
- 运行 portal 全量、Node 回归、适用 hub 门禁；部署从提交 LF blob 导出并核对 SHA。S3/Grbl 固件本批不因前端 bug 重刷。

## 外部依据

- 微信官方 slider 文档：https://developers.weixin.qq.com/miniprogram/dev/component/slider.html ，访问 2026-09-13，适用于当前小程序基础库 3.7.12；`disabled` 控制可操作性，`bindchange` 在拖动结束触发，`bindchanging` 提供拖动中的值。Context7 官方站点快照；本地复核需以组件与网络请求结果为准。
- 微信官方隐私开发指引：https://developers.weixin.qq.com/miniprogram/dev/framework/user-privacy/PrivacyAuthorize ，以及 https://developers.weixin.qq.com/miniprogram/dev/api/open-api/privacy/wx.openPrivacyContract.html ，访问 2026-09-13。`getPrivacySetting` / `requirePrivacyAuthorize` 是辅助 API，不因代码未调用就单独判违法；MP 后台实际声明、调用时授权和协议入口需分别核实。
- 微信官方自动化组件接口：https://developers.weixin.qq.com/miniprogram/dev/devtools/auto/element ，访问 2026-09-13；`element.slideTo` 适用于开发者工具的 slider，不能替代 iOS/Android 手机手势验证。
- 国家网信办公开《中华人民共和国个人信息保护法》：https://www.cac.gov.cn/2021-08/20/c_1631050028355286.htm ，2021-11-01 施行，2026-09-13 下载官方正文；重点核对第 17/23/31/39/47/55 条的告知、共享、儿童、数据出境、删除与影响评估要求。适用范围是小程序及后端个人信息处理，不据源码单独作法律结论；运营主体、处理合同、平台已公布指引未取得证据。原政府网链接已返回不存在页面，不作为有效取证。
- 国家网信办等四部门《人工智能生成合成内容标识办法》：https://www.cac.gov.cn/2025-03/14/c_1743654684782215.htm ，国信办通字〔2025〕2号、2025-09-01 施行，2026-09-13 下载官方正文。第 3–6 条分别涉及显式/隐式标识及传播环节；本批仅修界面已查实的漏标/误去标识，不能以一行 UI 提示宣称已满足文件元数据、导出与分享全部要求。官方 HTML 在本批 results；无其他硬件适用性结论。

## 补充复核

- 原生相册/文件选择器临时hide/show不能当作用户放弃选择；同一次选择回屏再上传，模式切换/卸载/退出仍作废。
- 生成和文字请求一旦提交，暂时切页保留同账号结果与在途标志，防回屏重付费；状态、录音后续工具与提示仍按展示代次隔离，隐藏页不发分享或精修轮询。
- 首次读取和账号网络失败显示加载/重试，不误报未绑定；小派设置清理旧账号选择。
- 开发者工具安装包官方skill0.3.9与工具版本关系equal，CLI无需token，Codex本地授权已确认。早先旧automator0.12.1读取失败，后查明mock流程退出了测试会话；恢复request/login并点击真实微信登录按钮后，正常控制页与完整生产绑定恢复。官方currentPage仍报getCurrentPagesByDomain异常，switchTab、data、截图可用；SDK和可见模拟器导航状态分别确认，不能把另一运行时的截图当本步证据。本机app.asar中slider.slideTo仅调组件 `_onUserChangedValue`；实测只移动数值，未产生本页change，因此验收脚本使用slideTo后显式组件change事件，走WXML原绑定，未调用页面处理函数或setData绕闸。每次恢复先等待disabled=false，避免上次状态pending时测试自行抢跑。来源为官方安装目录 `resources/app.asar.unpacked/wechatide-skill/SKILL.md` 与 `resources/app.asar`，访问2026-09-13；线上接口文档见前文，结论限本机工具，手机手势仍另验。
- 实际终态done返回的elapsed_ms/eta_ms会继续增加（观测9247310ms）；前端只在进行中/暂停/换纸状态显示耗时与预计时间，完成/空闲/错误/停止/待确认隐藏。未修改固件计时，未把仍增长的值当最终绘制耗时；追加回归覆盖进行中保留与各终态隐藏。

## 本地验收

| 检查 | 结果 | 本机证据（本批results根） |
|---|---|---|
| 页面JS语法与Node回归 | 21份JS语法通过，124 passed | `weapp-checks-144327.json`；先前123项见`weapp-checks-140325.json` |
| portal全量 | 357 passed | `web-checks-135200.json` |
| hub门禁 | 12层pass，MCP1453，e2e PASS | `hub-140952-hutuji_gate_last.json` |
| 文案/配置静态检查 | 产品源码未命中本批禁用服务描述词，urlCheck=true | `rg`全量扫描weapp；文案扫描不能替代实际隐私披露 |

第一轮hub仅行号指纹失败：新增说明导致7处纯移动与3处空锚点；按原证据内容重定位，人工确认387别名、COM13烧录与指纹抓包三行，再重登260处。未改产品行为、测试断言或门禁规则来通过。

## 部署与运行验收

- 后端提交 `a5fc773df3a2369bd84e01221329557ae20c56cd`，双机备份 `/root/hutuji-bak-20260913-weapp-a5fc773df3a2`。app.py SHA-256 `19d5839779780a51b96c32438f504e81eaa4b061bd03d2e1a18edb9183fbcc30`，telegram_archive.py `9baa1c95bcb762734b938c70a5504f0da8e581c8ace99130c7ae110960db1a48`，均CR=0。
- 阿里重启portal+admin，京东重启portal，健康200；主bridge PID4127741未变、热备0。双机env权限600，`HUTUJI_TG_UPLOAD_ENABLED=0`配置和进程均已核对，凭据保留。历史上传态作品阿里1件/台账2条、京东0，未自动删除。证据 `{ali,jd}-{install,verify,snapshot}.log`。
- 真实登录后正常进控制页，`component-live.json`覆盖组件设置值和change事件：X9500、Y9000、Z1100分别确认且其他轴不变，每轴恢复、重新进页读取仍10000/10000/1200。源码 `ade29f4f5742f430adbeef5609a71ca999eaaaec`，无接口mock，无setData绕过门控；不记为手机手势通过。
- `public-control-after.log`从真实me取active目标，分别跑本机与公网status/速度读取，均done/Idle/原点与10000/10000/1200；未登录me/速度均401。官方可见模拟器截图 `control-status-final.jpg`、`control-final.jpg`显示完成、三个实际速率、页脚v0.3.13；完成页耗时/ETA为空。console对error/exception/fail无匹配，不推定所有设备无错误。
- 本批没有重跑或修正既有生产preflight差异；上一批阿里24/33、京东25/33保持其原证据范围。本批没有固件更新。

## 上传结果

官方 `wechatide -c Codex upload` 的0.3.13候选来自前述已提交前端，描述「修复分轴调速，优化录音、作品管理与页面交互」。首次返回pending，任务 `confirmation_upload_de7e3280-a6f1-4f02-b8d3-4b6239e21d57`，原始脱敏日志 `145527-837469-upload.log`。继续时查询同一taskId，官方返回status=success、execution_success、result.success=true，包大小 **162480B**（TOTAL）；结果日志 `150457-482198-polling_task_result.log`。已上传开发版本，未代选体验版、提审或正式发布。机器可读终态为`upload-result.json`，原`pending-upload.json`也已标success，同任务不得重发。本地证据索引为同目录`verification-summary.json`。

## 合规未决事项

本批能修代码和暂停已查实的非必要外发，但没有取得公众平台已发布隐私指引、运营主体/服务商处理合同与资质、实际客服接待、完整注销删除流程、儿童个人信息规则和监护人同意的证据；专有字库商用授权也不能由“本机可解析”推定。文件隐式标识与分享/导出显式标识仍需专项验收。以上列为运营/专项待核项，不宣称法律或平台审核全部通过，不将内部核对稿直接作为提交材料。
