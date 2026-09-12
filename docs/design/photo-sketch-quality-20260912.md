# 照片精修：结构线与稀疏路径保真（2026-09-12）

用户反馈「图生图服务……质量还不行，有解决方案吗」，随后连续要求继续。本批在 §10.4.9 的本地程序化链内修复，保留 `hatch` / `squiggle` 两档、Canny 先回与精修失败隔离。当前生产与硬件状态仍以 `../release-readiness.md` 为准。

## 已复现的问题

1. **把光照画成轮廓**：对均衡后的亮度取三个固定等值线，会把面颊和额头的缓慢阴影描成闭合色块；不跨阈值的亮色镜框反而漏掉。单调光照渐变与亮调细框的合成回归区分这两种情况。
2. **排线反向连接错端点**：`_chain_hatch_segments` 接入反向段后仍用原段 `ends[j]` 计算下一座连接桥，实际绘制的桥与检查的桥不同。三段小图可稳定复现白色缺口被横穿；修复前两个定向用例失败，修复后通过。
3. **连接桥漏检窄白带**：只检查 1/4、1/2、3/4 三点会漏掉中间的白像素。已改为半像素间隔检查完整连接，且从实际尾端开始。
4. **稀疏路径误用描摹平滑**：`polylines_to_svg` 与 `polylines_to_preview_png` 默认的五点均值适用于密集描摹点，程序化排线的点却是间隔很远的端点/回折。均值会把回折挪向中部；排线串链后再 RDP 也会切入刚避开的白色缺口。不能靠调亮预览解决这类几何失真。

## 当前实现

- `sketch_render.render_hatch` 将结构与明暗分开：原始灰度的两尺度高斯差零交叉提供结构线；梯度门过滤缓慢光照和弱纹理，保留连续强边缘。σ=2.0/3.6、梯度门 0.014 的依据是本批两张真实室内人像与合成图；没有使用人物分类或主体分割模型。
- 排线明暗沿用全局/CLAHE 0.65/0.35 混合，hatch gamma 改为 0.55，给面部中间调留白；squiggle 仍为 0.7。四层排线阈值/间距、最小段长、段数预算沿用现值。
- 结构线继续 RDP；已经是直段与回折的排线链不再 RDP。波纹仍用既有 0.6px 波形容差。
- 两个公共序列化/预览函数增加 `smooth=True` 关键字，既有描摹调用仍用默认值；`process_upload_sketch` 和 `render_sketch_preview` 显式传 `smooth=False`，预览与 SVG 同口径。
- SVG 白名单、vpype `linemerge` / `linesimplify`、短 G1 地板、弧拟合、§5 字节与坐标限幅均沿用原链。

## 对比方法与本机证据

脚本 `mcp-server/scripts/sketch_eval.py` 不联网、不推送设备；输出只允许在 `results/local-only/`。A/B 基线是 Git 快照 `4c1c25e`，不是凭当前工作树推测生产。基线采用原来的导出平滑，新版采用稀疏路径原样导出。

`fullres.png` 从**实际导出 SVG** 的坐标重新栅格化，避免拿平滑前的中间折线冒充旧版交付结果；`gcode.png` 从已通过 §5 的最终 G-code 回放（包含短段塌缩与弧拟合，4px/mm），后者为报告默认图。原图、输入 SHA-256、当前渲染器 SHA-256、SVG、G-code、尺寸/墨覆盖/抬笔统计都在对应报告目录。

| 样本与风格 | 旧 SVG 墨覆盖 | 新 SVG 墨覆盖 | 旧→新 G-code 字节 | 旧→新抬笔数 |
|---|---:|---:|---:|---:|
| 室内人像 A / hatch | 12.02% | 9.54% | 302426 → 275464 | 2331 → 2305 |
| 室内人像 B / hatch | 11.99% | 9.30% | 200800 → 205313 | 1442 → 1640 |
| 室内人像 A / squiggle | 17.60% | 18.22% | 318692 → 398044 | 2209 → 2209 |
| 室内人像 B / squiggle | 13.77% | 14.36% | 137207 → 192476 | 684 → 684 |

这四份新产物在默认 MK38 与 `nopaper` 两档均通过 §5。新排线的人像镜框、眼睛和嘴边更易辨认，额头/衣服上的色块轮廓减少；**这是本机目检，不是纸面验收**。细节保真会增加部分样本的抬笔或波纹文件大小，不宣称整批提速。新波纹最大 398044 B，仍小于 512KiB；不能为更密照片放宽硬闸。

本机证据：

- `results/local-only/2026-09-12/sketch-quality-final/index.html`：两张不同真实室内人像，`nopaper` 最终回放；`metrics.json` 保存数值与哈希。
- `results/local-only/2026-09-12/sketch-quality-candidate/`：同两张人像、默认 MK38 档转换结果。
- `results/local-only/2026-09-12/sketch-quality-reference/`：随库咖啡、猫、摄影者照片的双风格回归，来源见下表。
- `sketch-quality-before/` 和 `sketch-structure-experiment/`：中间试验；前者的早期 `fullres.png` 是未导出的中间折线，只能用于排查，不能作为最终交付画质证据。

回归入口：MCP `test_sketch_render.py`（25 项，新增 8 项包括结构线/连接/导出几何）；portal `test_photo_refine.py`（13 项，新增双风格真实管线回归）。定向 MCP **75/75**、portal 全套 **287/287** 已通过。完整入口 `python scripts/agent_gate.py --profile hub` 的同树共享运行于 **2026-09-12T10:01:45Z** 完成：**overall=pass、exit=0**，MCP **1441/1441**（119.96s）、e2e **PASS**、内容指纹 **259/259**；HEAD 为 `cdfc6a68751af434133f2b6c4516ef4066de9d30`，包含本批未提交改动。本会话在门禁期间只读核对，复用该轮完整结果，不重复执行全量测试。

原始报告归档为 `results/local-only/2026-09-12/sketch-quality-gates/hub-20260912T100145Z.{json,md}`；同目录 `manifest.json` 登记运行来源、开始/完成时间、HEAD 与本批六个代码/测试/脚本文件 SHA-256。六文件最后写入均早于该轮开始，归档时门禁运行标记已清除；后续仅补文档，再跑 docs 门禁，不把单槽 `hutuji_gate_last.*` 的后续覆盖当成该批原始证据。

## 参考样张来源

只使用开发 venv 已安装的 scikit-image **0.26.0** 数据，2026-09-12 读取本地 wheel 的 `skimage/data/_fetchers.py` 和 PNG；没有联网下载或把当前上游当作实测。

| 样张 | 官方来源 / 版本 | 本地核验与范围 |
|---|---|---|
| coffee | [scikit-image v0.26.0 data](https://github.com/scikit-image/scikit-image/blob/v0.26.0/skimage/data/_fetchers.py)，`coffee()`，Rachel Michetti / CC0 | 600×400；杯沿、反光和木纹；`metrics.json` 登记输入 hash |
| chelsea | 同一官方版本，`chelsea()`，Stefan van der Walt / CC0 | 451×300；猫眼与毛发纹理 |
| camera | 同一官方版本，`camera()`，Lav Varshney / CC0 | 512×512；户外摄影者与器材；采用 0.18 之后替换的版本 |

复核命令：开发 venv `python -c "import skimage; print(skimage.__version__)"` 输出 `0.26.0`；`rg -n -A 32 '^def (camera|coffee|chelsea)\(' .../skimage/data/_fetchers.py` 核实样张说明。适用范围是服务器离线渲染，与 S3 板型无关；它们不能替代用户随手拍或纸面验收。

## 尚未完成的验收

本批没有人物分割能力，复杂背景的强结构仍会保留；对比不证明任意照片都达到艺术家手绘质量。尚未部署本批代码，尚未完成 ≥5 张用户随手拍与至少两张实机纸面目视；M1 不得标为完成。后续若需要彻底去背景，应另做可明确选用的主体分割方案并用遮挡、偏心主体、多人物和非人像样本验证，不能用固定中央蒙版冒充识别。
