# 精品图库数据根

设计：`docs/design/drawing-market-20260914.md`。契约：`docs/protocol.md` §10.4.23。

本目录进 git，是图库的单一真相源；部署时 scp 双机（阿里云 + 京东云各一份），
运行时只读、mtime 热重载（`draw_portal.market.MarketCatalog`）。

## 结构

```
market/
├── manifest.json          # 条目清单（schema 见下），入库管线生成
├── svg/<类目>/<id>.svg    # 单线 SVG 源（过 _validate_svg 白名单）
├── gcode/<类目>/<id>.gcode    # 预生成产物（curated_gate v4 已过）
├── preview/<类目>/<id>.png    # 预生成预览 PNG（viewBox 坐标框）
└── licenses/<source>.txt  # 各图源的许可文件全文
```

## manifest 条目 schema

```json
{
  "id": "twemoji-dog-1f415",
  "title": "小狗",
  "category": "动物",
  "license": "CC-BY-4.0",
  "attribution": "Twemoji (https://twemoji.twitter.com/), CC-BY 4.0",
  "source": "twemoji",
  "source_url": "https://github.com/jdecked/twemoji/...",
  "path": "svg/动物/twemoji-dog-1f415.svg",
  "gcode_path": "gcode/动物/twemoji-dog-1f415.gcode",
  "preview_path": "preview/动物/twemoji-dog-1f415.png",
  "viewBox": "0 0 36 36",
  "bytes": 4211,
  "quality_score": 0.92,
  "ingested_at": "2026-09-14",
  "gate_version": "4"
}
```

## 入库红线（运行时在加载期也强制）

- `license` 必填且 ∈ {CC0-1.0, Apache-2.0, MIT, CC-BY-4.0}；CC-BY-SA / CC-NC 一律不收
- CC-BY-4.0 条目 `attribution` 必填（缺署名拒收）
- 每个 SVG 必须真过 `curated_gate` v4（注入白名单、vpype/§5、过碎、无墨、行数）
- 图源许可文件全文落 `licenses/<source>.txt`，禁止照抄示例名称

## 状态

入库采集管线（M1）未开工——首批约 20 类目 400-600 条精品的选品、清洗、
人工审核是工作量大头。本目录当前为空，M2 服务端已可用手工夹具联调。
