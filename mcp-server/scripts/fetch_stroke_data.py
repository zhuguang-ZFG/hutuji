"""抓取 hanzi-writer-data 全量 median 数据 → 紧凑 strokes.jsonl（不入库）。

数据源：https://cdn.jsdelivr.net/npm/hanzi-writer-data@2.0.1/all.json
（jsdelivr CDN，国内可达性好于 raw.githubusercontent.com；数据许可 Arphic Public
License——允许商用与再分发，随附 ARPHICPL.TXT 即可。本脚本同时拉取许可文本。）

产物（写进 --out 目录，生产走 HUTUJI_STROKE_DIR）：
- strokes.jsonl：每行 `字\t[[[x,y],…], …]`（medians，1024×1024 坐标、y 向上为正）
- ARPHICPL.TXT：许可文本（再分发义务）
- ATTRIBUTION.md：来源与日期说明

用法：python scripts/fetch_stroke_data.py --out <目录>
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

DATA_URL = "https://cdn.jsdelivr.net/npm/hanzi-writer-data@2.0.1/all.json"
LICENSE_URL = (
    "https://cdn.jsdelivr.net/npm/hanzi-writer-data@2.0.1/ARPHICPL.TXT"
)

ATTRIBUTION = """# 汉字笔顺中心线数据

- 来源：hanzi-writer-data@2.0.1（Make Me A Hanzi 项目，skishore/makemeahanzi）
- 字段：仅取 `medians`（每笔中心线折点，1024×1024 坐标系，y 向上为正，需翻转使用）
- 笔顺：PRC（中国大陆）规范笔顺，项目 README 声明按 PRC 人工校订
- 许可：Arphic Public License（ARPHICPL.TXT 随附）；允许商用与再分发
- 抓取日期与脚本：见本目录数据由 mcp-server/scripts/fetch_stroke_data.py 生成
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="输出目录（HUTUJI_STROKE_DIR 指向它）")
    ap.add_argument("--limit", type=int, default=0, help="调试：只取前 N 字")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print(f"下载 {DATA_URL} …", flush=True)
    with urllib.request.urlopen(DATA_URL, timeout=300) as resp:
        raw = resp.read()
    print(f"下载完成 {len(raw) / 1024 / 1024:.1f} MiB")
    data = json.loads(raw)

    n = 0
    lines: list[str] = []
    for ch, entry in data.items():
        meds = entry.get("medians")
        if not meds:
            continue
        # 紧凑化：坐标取整（数据本身即 int）
        flat = [[[int(x), int(y)] for x, y in stroke] for stroke in meds]
        lines.append(ch + "\t" + json.dumps(flat, separators=(",", ":")))
        n += 1
        if args.limit and n >= args.limit:
            break
    (out / "strokes.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"strokes.jsonl: {n} 字, {(out / 'strokes.jsonl').stat().st_size / 1024 / 1024:.1f} MiB")

    try:
        with urllib.request.urlopen(LICENSE_URL, timeout=60) as resp:
            (out / "ARPHICPL.TXT").write_bytes(resp.read())
        print("ARPHICPL.TXT 已随附")
    except Exception as exc:  # noqa: BLE001
        print(f"警告：许可文本下载失败（{type(exc).__name__}），再分发前必须补齐", file=sys.stderr)
    (out / "ATTRIBUTION.md").write_text(ATTRIBUTION, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
