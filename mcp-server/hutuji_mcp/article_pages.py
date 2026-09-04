"""多页文章（写字链）的共享常量与序列化——server 与 draw-portal 的唯一同源。

独立成叶子模块的原因：portal 进程没有 `mcp` 依赖，import 不了 `server.py`；
而设备侧 `ParseArticlePages` 是纯扫描器（键序 url→preview_url 固定，protocol §4.2），
幂等重入要求「完全相同参数」——序列化若在 server 与 portal 各写一份 json.dumps，
迟早漂移成不同字节，两步回落时云端透传的 pages 会被当成新任务 busy 拒绝。
"""
from __future__ import annotations

import json

# 写字页耗时折算（错峰 TTL 用，protocol §4.2）：2026-09-01 真机实测 1021 行
# 灌流 116.3s ≈ 8.8 行/s、面板换纸全 8 步 8s；12mm 满页实测 10597 行 ≈ 20 分钟。
# 三个数都钉实测证据，改动必须带新的实机计时，不许凭手感调。
WRITE_PAGE_LINES_PER_SECOND = 8.8
WRITE_PAGE_CHANGE_SECONDS = 8.0
# 估时余量 ×2：覆盖设备重试、用户暂停与单点实测外推方差。窗口过宽只是
# capability URL 活得久；过紧就是后续页 404（旧 124s/页模型的实测事故面）。
WRITE_PAGE_TIME_MARGIN = 2.0
# 确认等待一次性补贴（2026-09-03 advisory 复核实锤）：t0 是云端生成完成时刻，但第 1 页
# 要等人在屏幕上按「确认」才开画——这段等待无上界且与页大小无关（语音下单后走开是
# 本产品常态用法）。2× 余量按页耗时缩放，对稀疏页只有几分钟绝对量：稀疏两页
# est₁≈248s，确认延迟 >12 分钟第 2 页必 404。故每页（含第 1 页）统一再前移这一个
# 常量——只加一次不按页乘（按页乘会把窗口无限放大，且确认延迟本来就只发生一次）。
WRITE_PAGE_CONFIRM_ALLOWANCE_SECONDS = 600.0

# 与设备侧 kArticleMaxPages 同值（protocol §4.2）；write_text.layout_text 的
# max_pages 默认也钉这个数。
ARTICLE_MAX_PAGES = 20


def pages_argument(pages: list[dict]) -> str:
    """设备侧 `hutuji.draw` 的 pages 参数：紧凑 JSON 字符串，键序 url→preview_url。

    链式推送、`hutuji_write` 返回值的 `pages_arg`、portal 文档导入的整篇发送
    共用这一个序列化点（见模块 docstring）。
    """
    return json.dumps(
        [{"url": p["url"], "preview_url": p["preview_url"]} for p in pages],
        ensure_ascii=False, separators=(",", ":"),
    )


def page_ttl_offsets(page_lines: list[int]) -> list[float]:
    """每页产物 mtime 相对 t0 的前移量（错峰 TTL）。

    第 i 页 = 确认补贴 + Σ_{j<i} est_j（est_j = lines_j/8.8 + 8，×2 余量）；
    判定链（MCP `/files/`）只读磁盘 mtime，调用方负责 os.utime(now + offset)。
    """
    offsets: list[float] = []
    cumulative = WRITE_PAGE_CONFIRM_ALLOWANCE_SECONDS
    for lines in page_lines:
        offsets.append(cumulative)
        cumulative += (
            lines / WRITE_PAGE_LINES_PER_SECOND + WRITE_PAGE_CHANGE_SECONDS
        ) * WRITE_PAGE_TIME_MARGIN
    return offsets
