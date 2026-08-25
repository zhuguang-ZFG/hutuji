"""Quick Draw 本地缓存契约与集成测试（设计稿 docs/design/quickdraw-local-cache.md §10）。

约定同 test_contract.py：函数式、monkeypatch 模块全局、tmp_path、asyncio.run。
每条假数据目录只两类各三条，lookup 集成走真实 vpype 转换（~1s/次）。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hutuji_mcp import quickdraw, server
from hutuji_mcp.bitmap_svg import BitmapResult
from hutuji_mcp.gcode_gen import validate_gcode
from hutuji_mcp.llm_svg import _validate_svg

_ENTRY = {"strokes": [[[10, 20, 30, 40], [10, 25, 30, 45]], [[50, 60, 70], [200, 210, 205]]]}
_BASE_URL = "http://127.0.0.1:8300"


@pytest.fixture(autouse=True)
def _reset_caches():
    quickdraw._category_map.cache_clear()
    quickdraw._substring_keys.cache_clear()
    quickdraw._load_entries.cache_clear()
    quickdraw._warned.clear()
    yield
    quickdraw._category_map.cache_clear()
    quickdraw._substring_keys.cache_clear()
    quickdraw._load_entries.cache_clear()
    quickdraw._warned.clear()


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "qd"
    d.mkdir()
    (d / "cat.jsonl").write_text("\n".join(json.dumps(_ENTRY) for _ in range(3)), encoding="utf-8")
    (d / "dog.jsonl").write_text(json.dumps(_ENTRY), encoding="utf-8")
    return d


def _use_dir(monkeypatch: pytest.MonkeyPatch, data_dir: Path | None) -> None:
    monkeypatch.setattr(quickdraw, "settings", SimpleNamespace(quickdraw_dir=str(data_dir) if data_dir else ""))


# ---------- 映射与前缀 ----------

def test_match_exact(monkeypatch, data_dir):
    _use_dir(monkeypatch, data_dir)
    assert quickdraw._match_category("猫") == "cat"
    assert quickdraw._match_category("小狗") == "dog"


def test_match_strips_draw_prefix(monkeypatch, data_dir):
    _use_dir(monkeypatch, data_dir)
    assert quickdraw._match_category("画一只猫") == "cat"
    assert quickdraw._match_category("画只猫") == "cat"
    assert quickdraw._match_category("画猫") == "cat"
    assert quickdraw._match_category("一只猫") == "cat"


def test_match_strips_polite_prefixes(monkeypatch, data_dir):
    """语音口语礼貌前缀（2026-08-25 生产缺口：「请画树」全 miss 走 AI 生烂树）。"""
    _use_dir(monkeypatch, data_dir)
    assert quickdraw._match_category("请画树") == "tree"
    assert quickdraw._match_category("帮我画树") == "tree"
    assert quickdraw._match_category("请帮我画树") == "tree"
    assert quickdraw._match_category("麻烦你画一只猫") == "cat"
    assert quickdraw._match_category("我想画狗") == "dog"
    assert quickdraw._match_category("我要画太阳") == "sun"


def test_match_strips_stacked_prefixes_iteratively(monkeypatch, data_dir):
    """礼貌+量词叠加须循环剥离（「请画一棵树」两刀到「树」）。"""
    _use_dir(monkeypatch, data_dir)
    assert quickdraw._match_category("请画一棵树") == "tree"
    assert quickdraw._match_category("帮我画一只猫") == "cat"
    assert quickdraw._match_category("请画一只可爱的小猫") == "cat"


def test_match_polite_prefix_keeps_no_strip_guards(monkeypatch, data_dir):
    """礼貌前缀剥完后仍须过 _NO_STRIP 拦截（「请画画像」→「画像」不得误剥）。"""
    _use_dir(monkeypatch, data_dir)
    assert quickdraw._match_category("请画画像") is None
    assert quickdraw._match_category("帮我画画家") is None


def test_match_never_strips_mid_string(monkeypatch, data_dir):
    _use_dir(monkeypatch, data_dir)
    assert quickdraw._match_category("墙上画着猫") is None


def test_match_unknown_returns_none(monkeypatch, data_dir):
    _use_dir(monkeypatch, data_dir)
    assert quickdraw._match_category("未收录主题") is None
    assert quickdraw._match_category("") is None


# ---------- lookup 单元 ----------

def test_lookup_disabled_returns_none_without_disk(monkeypatch, tmp_path):
    _use_dir(monkeypatch, None)
    ghost = tmp_path / "ghost"
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: (_ for _ in ()).throw(AssertionError("触盘")))
    assert quickdraw.lookup("猫") is None


def test_lookup_hit_shape_and_svg_contract(monkeypatch, data_dir):
    _use_dir(monkeypatch, data_dir)
    result = quickdraw.lookup("猫")
    assert isinstance(result, BitmapResult)
    _validate_svg(result.svg)
    assert result.svg.count("<polyline") == len(_ENTRY["strokes"])
    assert 'fill="none"' in result.svg
    # 设计 §10.2 坐标透传抽查：< _SMOOTH_WINDOW(5) 点的笔画不平滑，
    # 夹具首笔 4 点须原样进 polyline points（格式 x:.1f,y:.1f 空格分隔）。
    assert 'points="10.0,10.0 20.0,25.0 30.0,30.0 40.0,45.0"' in result.svg
    assert result.preview_png.startswith(b"\x89PNG")


def test_lookup_missing_category_data_is_miss(monkeypatch, data_dir):
    _use_dir(monkeypatch, data_dir)
    assert quickdraw.lookup("熊猫") is None  # 映射在案但无 panda.jsonl


def test_lookup_corrupt_lines_tolerated(monkeypatch, data_dir):
    (data_dir / "cat.jsonl").write_text(
        json.dumps(_ENTRY) + "\n{坏json\n" + json.dumps({"strokes": "非列表"}) + "\n",
        encoding="utf-8",
    )
    _use_dir(monkeypatch, data_dir)
    assert quickdraw.lookup("猫") is not None  # 坏行跳过，好行仍可用


def test_lookup_all_corrupt_is_miss_and_warns_once(monkeypatch, data_dir, caplog):
    (data_dir / "cat.jsonl").write_text("{坏a\n{坏b\n", encoding="utf-8")
    _use_dir(monkeypatch, data_dir)
    with caplog.at_level("WARNING"):
        assert quickdraw.lookup("猫") is None
        first = sum("Quick Draw" in r.getMessage() for r in caplog.records)
        assert first >= 1
        assert quickdraw.lookup("猫") is None
        second = sum("Quick Draw" in r.getMessage() for r in caplog.records)
    assert second == first  # 同类失败重复查找不得重复告警


class _SlotSpy:
    """记录进入次数的异步上下文管理器；命中路径必须一次都不进。"""

    def __init__(self):
        self.entries = 0

    async def __aenter__(self):
        self.entries += 1

    async def __aexit__(self, *exc):
        return False


def _server_settings(output_dir: Path, **kw):
    base = dict(output_dir=output_dir, output_ttl_seconds=600, xiaozhi_device_token="")
    base.update(kw)
    return SimpleNamespace(**base)


def test_draw_job_hit_skips_paid_generation(monkeypatch, tmp_path, data_dir):
    _use_dir(monkeypatch, data_dir)
    monkeypatch.setattr(server, "settings", _server_settings(tmp_path))
    slot_spy = _SlotSpy()
    monkeypatch.setattr(server, "_draw_limiter", SimpleNamespace(slot=lambda: slot_spy))

    def _forbidden_generate(_desc):
        raise AssertionError("命中路径不得调用付费生图")

    monkeypatch.setattr(server, "generate_bitmap", _forbidden_generate)
    # 2026-08-25 C 组合后 QD 质量路由：普通词走 AI 链；用"灵魂画"确保 QD 命中。
    payload = json.loads(asyncio.run(server._run_draw_job("灵魂画一只猫", _BASE_URL)))
    assert slot_spy.entries == 0
    name = payload["url"].rsplit("/", 1)[-1]
    assert name.endswith(".gcode")
    stats = validate_gcode((tmp_path / name).read_text(encoding="utf-8"))
    assert stats.lines == payload["lines"] and stats.pen_lifts == payload["pen_lifts"]
    assert (tmp_path / name.replace(".gcode", ".svg")).exists()
    assert (tmp_path / name.replace(".gcode", ".png")).exists()
    assert payload["ttl_seconds"] == 600


def test_draw_job_miss_falls_back_to_bitmap(monkeypatch, tmp_path, data_dir):
    _use_dir(monkeypatch, data_dir)
    monkeypatch.setattr(server, "settings", _server_settings(tmp_path))
    slot_spy = _SlotSpy()
    monkeypatch.setattr(server, "_draw_limiter", SimpleNamespace(slot=lambda: slot_spy))
    calls = []
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="190mm" height="190mm" '
        'viewBox="0 0 190 190"><path d="M20 20 L30 30 L40 20" fill="none" '
        'stroke="black" stroke-width="0.5"/></svg>'
    )

    async def _fake_generate(desc):
        calls.append(desc)
        return BitmapResult(svg=svg, preview_png=b"\x89PNG-fake")

    monkeypatch.setattr(server, "generate_bitmap", _fake_generate)
    payload = json.loads(asyncio.run(server._run_draw_job("未收录主题", _BASE_URL)))
    assert calls == ["未收录主题"]
    assert slot_spy.entries == 1
    assert payload["url"].endswith(".gcode")


def test_draw_job_quality_routes_plain_description_to_ai(monkeypatch, tmp_path, data_dir):
    """C 组合质量路由：普通描述默认走 AI 位图链，QD 仅简笔画风词命中（2026-08-25
    用户实测 QD 涂鸦与 AI 线稿并出后「越来越不好看」）。"""
    _use_dir(monkeypatch, data_dir)
    monkeypatch.setattr(server, "settings", _server_settings(tmp_path))
    slot_spy = _SlotSpy()
    monkeypatch.setattr(server, "_draw_limiter", SimpleNamespace(slot=lambda: slot_spy))
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="190mm" height="190mm" '
        'viewBox="0 0 190 190"><path d="M20 20 L30 30" fill="none" '
        'stroke="black" stroke-width="0.5"/></svg>'
    )

    async def _fake_generate(desc):
        return BitmapResult(svg=svg, preview_png=b"\x89PNG-fake")

    monkeypatch.setattr(server, "generate_bitmap", _fake_generate)
    # 「画一只猫」在映射表内，但无简笔画风词 → 必须走 AI，不查缓存。
    payload = json.loads(asyncio.run(server._run_draw_job("画一只猫", _BASE_URL)))
    assert slot_spy.entries == 1
    assert payload["url"].endswith(".gcode")


def test_draw_quality_routing_keyword_variants(monkeypatch, tmp_path, data_dir):
    """简笔画风词表：命中任一变体即查 QD 缓存。"""
    for phrase in ("简笔画一只猫", "画个速写猫", "涂鸦一只猫", "快速画只猫"):
        assert server._QD_STYLE_RE.search(phrase), phrase
    for phrase in ("画一只猫", "画一棵树", "给我画个房子"):
        assert not server._QD_STYLE_RE.search(phrase), phrase


# ---------- 映射表自身卫生 ----------

def test_category_map_file_wellformed():
    mapping = quickdraw._category_map()
    assert len(mapping) >= 100
    assert all(k.strip() and v.strip() and " " not in k for k, v in mapping.items())

def test_match_noun_morpheme_blocked(monkeypatch, data_dir):
    """「画家/画板」等名词语素词不得被剥「画」误配（2026-08-24 review 发现）。"""
    _use_dir(monkeypatch, data_dir)
    assert quickdraw._match_category("画家") is None
    assert quickdraw._match_category("画板") is None
    assert quickdraw._match_category("画笔") == "paintbrush"  # 精确整词优先于拦截
    assert quickdraw._match_category("画猫") == "cat"  # 裸「画」动词前缀仍正常剥离


def test_lookup_nonfinite_entry_skipped(monkeypatch, data_dir):
    """含 NaN 坐标的条目在数据边界拒收，好条目仍可用。"""
    bad = {"strokes": [[[0, float("nan")], [0, 1]]]}
    (data_dir / "cat.jsonl").write_text(
        json.dumps(bad) + "\n" + json.dumps(_ENTRY) + "\n", encoding="utf-8"
    )
    _use_dir(monkeypatch, data_dir)
    assert quickdraw.lookup("猫") is not None


def test_match_substring_fallback_for_enriched_phrases(monkeypatch, data_dir):
    """生产实证回归：云端 role-prompt 增强短语必须子串兜底命中。"""
    _use_dir(monkeypatch, data_dir)
    assert quickdraw._match_category("单线简笔侧脸小猫") == "cat"
    assert quickdraw._match_category("给我画一只小狗好不好") == "dog"
    assert quickdraw._match_category("画一辆自行车") == "bicycle"  # 子串：自行车


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("单线简笔风格的皮卡车", "pickup truck"),
        ("单线简笔的笔记本电脑", "laptop"),
        ("单线简笔的面包车", "van"),
        ("单线简笔的棒球棍", "baseball bat"),
    ],
)
def test_match_substring_prefers_longest_alias(description, expected):
    """真实碰撞对必须由最长键裁决；字典序会把它们错配到短键类目。"""
    assert quickdraw._match_category(description) == expected


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("单线简笔的小蛇", "snake"),
        ("单线简笔的神龙", "dragon"),
        ("单线简笔的梨子", "pear"),
        ("单线简笔的小草", "grass"),
        ("单线简笔的山峰", "mountain"),
        ("单线简笔的桥梁", "bridge"),
        ("单线简笔的架子鼓", "drums"),
        ("单线简笔的床铺", "bed"),
        ("单线简笔的大门", "door"),
        ("单线简笔的人脸", "face"),
        ("单线简笔的手掌", "hand"),
        ("单线简笔的大腿", "leg"),
        ("单线简笔的脚掌", "foot"),
        ("单线简笔的宝剑", "sword"),
    ],
)
def test_match_substring_covers_single_character_categories(description, expected):
    """只有单字旧别名的类目也必须有 ≥2 字别名承接增强短语。"""
    assert quickdraw._match_category(description) == expected



def test_match_substring_excludes_single_char_keys(monkeypatch, data_dir):
    """单字键不参与子串兜底：「天安门」含「门」也不得误配 door（宁可 miss 走 AI）。"""
    _use_dir(monkeypatch, data_dir)
    assert quickdraw._match_category("画一座天安门") is None
