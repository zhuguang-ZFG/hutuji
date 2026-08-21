"""小派记忆持久化：云端 JSON 单文件 + 覆盖式增删查（窄异常，无日志正文）。

存储位置 `settings.output_dir / "memory.json"`：
- `_cleanup_expired_outputs` 只清 `draw_*` 能力产物，摸不到本文件；
- `/files` 只服务带 128-bit token 的能力文件名，本文件不可下载；
- 生产 output_dir 与 G-code 产物同目录、同权限，无需新增配置。

内容属用户个人数据：本模块任何分支都**不把 key/value 写进日志**（脱敏硬约定）。
记忆不是关键数据——文件缺失/损坏一律从空开始，绝不让工具调用因它 500。
"""
from __future__ import annotations

import json
import os
import threading
from collections import OrderedDict
from pathlib import Path

from .config import settings
from .llm_svg import _CONTROL_CHAR_RE

MEMORY_FILE_NAME = "memory.json"
MEMORY_MAX_ENTRIES = 200
MEMORY_KEY_MAX_CHARS = 50
MEMORY_VALUE_MAX_CHARS = 500
# 全量回忆上限：200 条全塞给 LLM 会挤占上下文，超出时只回最近写入的 100 条
# 并注明总数，让助手如实说「记了很多，这是最近的」。
MEMORY_RECALL_ALL_LIMIT = 100
# 全量回忆的字符总量预算：条数上限管不住「100 条 × 500 字符」≈ 50k tokens
# 挤爆 LLM 上下文的理论极端；从最新往回装，装满为止（条数上限仍先生效）。
MEMORY_RECALL_ALL_MAX_CHARS = 8000

# MCP 工具走 asyncio.to_thread，remember/forget 的读-改-写必须串行，
# 否则两个并发写各自 _load 后互相覆盖丢一条（_store 原子写只保不烂不保不丢）。
_LOCK = threading.Lock()


def memory_path() -> Path:
    return settings.output_dir / MEMORY_FILE_NAME


def _clean(text: str, limit: int) -> str:
    """与 description 同口径剥控制字符，再限长；非字符串入参按空处理。"""
    if not isinstance(text, str):
        return ""
    return _CONTROL_CHAR_RE.sub("", text).strip()[:limit]


def _load() -> "OrderedDict[str, str]":
    """读全量；缺失/损坏/结构不对一律从空开始（fail-open，理由见模块 docstring）。"""
    try:
        raw = json.loads(memory_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return OrderedDict()
    if not isinstance(raw, dict):
        return OrderedDict()
    return OrderedDict(
        (k, v) for k, v in raw.items() if isinstance(k, str) and isinstance(v, str)
    )


def _store(data: "OrderedDict[str, str]") -> None:
    """原子写：先写临时文件再 os.replace，崩溃/并发不会留下半个 JSON。"""
    path = memory_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def remember(key: str, value: str) -> str:
    """写入/覆盖一条记忆；超容量按 LRU 逐出最久未触碰的条目。"""
    key = _clean(key, MEMORY_KEY_MAX_CHARS)
    value = _clean(value, MEMORY_VALUE_MAX_CHARS)
    if not key or not value:
        return "没听清要记什么，这次没有记住。"
    with _LOCK:
        data = _load()
        if key in data:
            del data[key]
        data[key] = value
        while len(data) > MEMORY_MAX_ENTRIES:
            data.popitem(last=False)
        _store(data)
    return f"已记住「{key}」。"


def recall(key: str = "") -> str:
    """按主题子串回忆；key 为空返回全量（条数与字符总量双上限，注明总数）。"""
    key = _clean(key, MEMORY_KEY_MAX_CHARS)
    data = _load()
    if not key:
        total = len(data)
        items: list[tuple[str, str]] = []
        budget = MEMORY_RECALL_ALL_MAX_CHARS
        for item in reversed(list(data.items())):
            if len(items) >= MEMORY_RECALL_ALL_LIMIT:
                break
            cost = len(item[0]) + len(item[1])
            # 首条超预算也照收：宁可超一点也不能空着手回去
            if items and cost > budget:
                break
            budget -= cost
            items.append(item)
        items.reverse()
        return json.dumps(
            {"total": total, "returned": len(items), "memories": dict(items)},
            ensure_ascii=False,
        )
    hits = {k: v for k, v in data.items() if key in k}
    if not hits:
        return f"没有记住关于「{key}」的事。"
    return json.dumps({"memories": hits}, ensure_ascii=False)


def forget(key: str) -> str:
    """精确删除一条；传 "*" 清空全部。"""
    key = _clean(key, MEMORY_KEY_MAX_CHARS)
    if key == "*":
        with _LOCK:
            _store(OrderedDict())
        return "已经全部忘掉了。"
    if not key:
        return "没听清要忘掉什么。"
    with _LOCK:
        data = _load()
        if key not in data:
            return f"本来就没有记住「{key}」。"
        del data[key]
        _store(data)
    return f"已忘掉「{key}」。"


