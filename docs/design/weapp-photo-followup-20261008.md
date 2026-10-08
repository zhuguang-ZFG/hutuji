# 2026-10-08 小程序功能与照片续作

用户选择「继续小程序功能改善」，追加「关于照片生图的问题也要处理」并明确「画质和生成体验都要改善」。起点 `13a812c`；已上传的0.3.24保持原发布记录，本批是后续工作树候选。

## 范围与验收

1. 照片画质：用既有真实照片对比暗部结构与排线遮挡，修复有对照证据的问题。保留排线素描感、既有几何容差、纸张快照和§5闸。原图只在本机评估，不上传新增第三方；对照包含全尺寸线图与最终G-code回放，不能仅凭墨量宣称更像或纸面验收。
2. 照片体验：本机原图与选中结果对照；精修等待使用绝对时限，切页不重置等待；畸形ready回包不能成为可发送结果；网络失败有可见提示并可只读核对，不重新上传或自动生成。
3. 图库：同账号详情返回保留分类与已加载页，翻页失败从原游标重试，主动下拉刷新仍重新读取；旧请求、旧账号和隐藏页不得回写或续派预览。
4. 聊天：失败原文可恢复到输入框，不自动发送，不覆盖新草稿；失败轮次和错误提示不混入下一轮模型历史，登录切换作废恢复入口。
5. 控制：透出设备last_error及完整操作失败原因；速度读写等待覆盖既有40秒后台事务，失败不自动重复写，不把未确认状态当作已完成。

白名单：`draw-portal/weapp/**`、`draw-portal/tests/weapp/**`、`mcp-server/hutuji_mcp/sketch_render.py`、`mcp-server/tests/test_sketch_render.py`、必要的Portal照片管线与对应测试、`docs/**`及既有计数/约定文件。依据protocol §5/§10.4.9/§10.4.13–14/§10.4.21/§10.4.23。本批不改固件。

验证：新增行为回归先红后绿；Node全套、照片专项、Portal全套及hub门禁；微信原生编译和模拟器检查。证据留`results/local-only/2026-10-08-weapp-photo-followup/`，临时实验留`tmp/weapp-photo-followup-20261008/`。实现、测试、上传、部署、纸面验收分别记实。

## 照片画质：实现与证据（第1项）

对照样片为既有两张真人像（`results/local-only/2026-09-25/saliency-ab/sample-01/source.png`、`ack-photo/user-source.jpg`），只在本机评估，未上传任何第三方。对照链是真实管线：`render_hatch` → `polylines_to_svg(smooth=False)` → `_validate_svg` → `svg_to_gcode`（nopaper 与 mk38 两档）→ `validate_gcode` → G-code 回放栅格，由 `mcp-server/scripts/sketch_eval.py --baseline-ref HEAD` 产出（`final-nopaper/`、`final-mk38/`，`metrics.json` 记录渲染器 SHA 与基线 ref）。

**用户可见缺陷与归因**：放大对照（`experiment/*-zoom*.png`、`halo-ab2/*-face-compare.png`）显示两处——① 眼镜框、眼睛、鼻翼等结构线被同区排线整片盖住，纸面上读不出五官；② 软阴影边缘的结构线沿等高线来回抖动，RDP 后留下锯齿顶点。暗部结构本身（阴影里的轮廓）并未缺失，先前三种「暗部结构」候选（暗区权重保底 / 阴影对比归一 / 两者合并，`experiment/*-compare.png`）只是在暗区多出噪线，**全部否决**；显著性阈值上调（C 方案）收益边际且有切掉主体风险，同样不采用。

**采用的修复**（仅 `sketch_render.py` hatch 路径，squiggle 不动）：
- 结构线沿等高线高斯平滑 `_STRUCTURE_SMOOTH_SIGMA_PX = 3.0`（闭合环 wrap、开链 nearest），再进原有 RDP 1.2px；不放松任何几何容差，RDP 顶点数降 10–17%。
- 结构线留白带 `_HATCH_HALO_RADIUS_PX = 2.0`（工作像素×密度比，A4 上≈0.7mm）：先算结构线，排线各层与串链 mask 一律剔除留白带。半径 2.5 开始掏空头发/眉毛（`halo-ab2/`），故定 2.0。

**全链指标**（两档 profile 数值一致，§5 全部通过）：

| 样片 | 变体 | 折线 | 顶点 | 抬笔 | G-code 字节 | 墨覆盖 |
|---|---|---|---|---|---|---|
| sample-01（女孩） | baseline-hatch | 981 | 4081 | 924 | 127631 | 4.59% |
| sample-01 | current-hatch | 821 | 3303 | 752 | 103405 | 4.00% |
| sample-02（男士） | baseline-hatch | 845 | 2881 | 836 | 99446 | 3.84% |
| sample-02 | current-hatch | 730 | 2375 | 715 | 82699 | 3.37% |
| 两样片 | squiggle | 逐字节相同 | | | | |

抬笔 −14%/−14%、字节 −19%/−17%，墨量略降来自留白带，不是靠减墨「变像」；判读依据是 G-code 回放面部放大图 `sample-0{1,2}-face-gcode-compare.png`：眼镜框、眼、鼻与下颌线从排线里分离出来，等高线锯齿消失。回归 `test_sketch_render.py` +2（留白带零墨、平滑后闭合/端点/半径与 RDP 顶点减少），42→44，RED→GREEN。

未验证：纸面实拍、生产部署。以上是本机离线对照，不代表纸面验收。

## 照片体验：实现与证据（第2项）

`draw-portal/weapp/pages/index/index.js`（+wxml/wxss），不新增后端接口：
- **绝对时限**：`REFINE_WINDOW_MS = 300000` 从该 job 第一次开始等待起算（`_refineDeadlineFor`），onHide 停链、回屏 `resumeJobState` 续轮只续剩余时间；最后一轮贴着截止时刻排，到点判 `timeout`。超时后的回屏/「查看精修结果」仍只 GET。
- **畸形 ready**：`_applyRefineState` 统一落屏，ready 必须带 `job_id`（收藏/分享/发送都用它）；adoptJob、轮询、回屏三条路径的缺 `job_id` 回包都按仍在等待处理，不写 `job.refined`，发送仍用草图 id。
- **网络失败**：轮询 catch 置 `job.refineOffline`，等待卡显示「网络不稳定…仍在继续等待」与「重新核对」按钮（`onRecheckRefined`，只 GET 既有 job，核对中不重复发起，失败不弹窗）；链照常续到绝对时限，网络恢复拿到 pending 即清提示。无任何路径重新上传或重新生成。
- **原图对照**：拍照/相册上传把 `tempFilePath` 随结果落 `job.photoSource`（经 `adoptJob(data, extra)`，隐藏页交接也带上），预览卡「对照原图」用 `wx.previewImage` 原图在前、当前选中版本（草图/精修）在后；文件上传与文字生成无原图、入口不显示。

回归 `draw-portal/tests/weapp/photo_experience.test.js` 9 项（RED 9/9 → GREEN 9/9，`weapp/photo_experience-red.log`、`weapp/node-all.log`）：切页回屏总等待≈一个窗口而非重新起算、超时后补查只读、三路径畸形 ready、离线提示/只读核对/不重传、网络恢复清提示、原图对照随版本切换、非照片无入口。

## 验证汇总

MCP 全套 1613/1613（+2，`AGENTS.md`/`worktree-inventory.md` 计数同步）；Node 291/291（基线 282 +9）；Portal 487/487（`portal.log`）；hub 门禁 12/12 PASS（`hub-gate.log`，pytest 1613 passed、e2e PASS，工作树基于 `13a812c`）。当时未做：微信原生编译/模拟器截图、真机、提交、上传、部署、纸面验收；第 3–5 项当时未开始，**其软件状态由下方10月9日续作覆盖**。本批为未提交的工作树候选，不改固件。

## 2026-10-09 续完图库、聊天与控制（第3–5项）

用户「继续改进」。接手时三页已有部分实现与18项测试，实际跑出17通过/1失败；保留已有照片渲染与首页改动。本轮只继续完成清单中的小程序行为，不改固件和服务业务逻辑。

- 图库：同账号返回保留分类、已加载页和空分类；预览重建使用当前视口，不重新挑六张未缓存图片。失败请求记录目录/分类/原游标，目录刷新失败不会误走旧列表翻页，重试期间不叠加请求。旧列表应答与旧视口回调都按展示代次失效，换账号清除列表与重试状态；下拉刷新仍重新读取目录与首页。
- 聊天：失败用户轮次与错误气泡不进入下一轮文字历史；可把原文恢复到空输入框，不自动发送、不覆盖草稿。恢复时即时检查登录代次、页面可见性与处理状态；退出但尚未回屏的旧按钮也失效。页面文案使用「回复未确认」，不把网络超时说成请求未到服务端。
- 控制：error 态分别显示设备 `last_error`、`control_error`；操作完整失败原因保留在页内，下一次操作或回屏清除。速度读写显式等待60秒，覆盖门户 accepted 后40秒事务；超时不自动重写，未重新读回前不可继续提交滑块。

追加5项回归，加上接手已有18项，共23项专项；加照片9项后，Node从原282增至314。两组红证分别钉住预览多派/目录误重试/空分类重试/重复请求/退出原文恢复，以及迟到视口回调（`weapp/followup-resume-red.log`、`weapp/observer-resume-red.log`）。最终小程序全套314/314（`weapp/node-all-resume.log`），Portal487/487（96.75秒，`portal-resume.log`），hub12层PASS含MCP1613/1613（155.67秒）与e2e PASS（`hub-gate-resume.log`）；`git diff --check`通过。Portal警告为既有依赖弃用与本机pytest缓存写权限，不影响测试结果。

**原生验证阻塞**：两次编译调用均在执行编译前返回 `CONNECT_ERROR / wait WechatIDE authorization timeout`。用户已确认在工具内允许Codex；随后状态检查返回授权任务pending，但对同一taskId的两次查询均为`AUTH_TASK_ERROR / Failed to connect to WechatIDE`，无法确认任务最终状态，不据此重发。`pending-native-auth.json`保留任务、项目、最后已知状态与恢复步骤；原生编译/模拟器截图仍未验证。真机、提交、上传、部署与纸面验收均未执行。
