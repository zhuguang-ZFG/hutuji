import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PORTAL = ROOT / "draw_portal" / "static" / "index.html"
ADMIN = ROOT / "draw_admin" / "static" / "index.html"
KB_ADMIN = ROOT.parent / "kb-admin" / "kb_admin" / "static" / "index.html"


def test_public_portal_defaults_to_fh031_cream_xiaopai_brand():
    html = PORTAL.read_text(encoding="utf-8")
    assert '<html lang="zh-CN" data-theme="cream">' in html
    assert "HandRaw FH-031" in html
    assert "--bg0: #fff7ed" in html.lower()
    assert 'class="logo pai-logo"' in html
    assert "丹青 · 小派画画屋" in html
    assert 'data-theme-set="cream"' in html
    assert "const THEMES = ['cream', 'ink', 'sky', 'moss', 'dusk'];" in html
    assert "const THEME_KEY = 'hutuji-draw-theme-v2';" in html
    assert "localStorage.getItem(THEME_KEY) || 'cream'" in html
    assert "HandRaw Style · 原作者 yang0" in html


def test_admin_uses_same_cream_tokens_and_xiaopai_mark():
    html = ADMIN.read_text(encoding="utf-8")
    assert "HandRaw FH-031" in html
    assert "--bg0: #fff7ed" in html.lower()
    assert "--accent: #f3a3b5" in html.lower()
    assert html.count('class="logo pai-logo"') == 2
    assert "丹青 · 小派运营台" in html
    assert "HandRaw Style · yang0" in html


def test_knowledge_admin_joins_the_same_handraw_system():
    html = KB_ADMIN.read_text(encoding="utf-8")
    assert "HandRaw FH-031" in html
    assert "--bg: #fff7ed" in html.lower()
    assert "--primary: #f3a3b5" in html.lower()
    assert html.count('class="brand-icon pai-logo"') == 2
    assert "小派知识画册" in html
    assert "HandRaw Style · yang0" in html


def _luminance(hex_color: str) -> float:
    channels = [int(hex_color.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(fg: str, bg: str) -> float:
    high, low = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _tokens(html: str, opener: str) -> dict[str, str]:
    block = html.split(opener, 1)[1].split("}", 1)[0]
    return dict(re.findall(r"(--[a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\s*;", block))


# 奶油底上所有承载文字的令牌须达 WCAG AA 4.5:1；珊瑚粉 #f3a3b5 只作填充（≈1.7–1.9:1）。
CREAM_SURFACES = ("#fffdf8", "#fff7ed", "#fbeee4", "#ffe5ec", "#fbe2e8", "#f8e7d9")


@pytest.mark.parametrize(
    ("path", "opener", "text_tokens"),
    [
        (PORTAL, ':root, [data-theme="cream"] {', ("--text", "--muted", "--accent-dark", "--ok", "--err", "--warn")),
        (ADMIN, ":root {", ("--text", "--muted", "--soft", "--accent-dark", "--ok", "--err", "--warn")),
        (KB_ADMIN, ":root {", ("--text", "--text-muted", "--text-soft", "--primary-ink", "--success", "--danger", "--warning")),
    ],
)
def test_cream_text_tokens_meet_wcag_aa_on_every_surface(path, opener, text_tokens):
    tokens = _tokens(path.read_text(encoding="utf-8"), opener)
    for name in text_tokens:
        for surface in CREAM_SURFACES:
            ratio = _contrast(tokens[name], surface)
            assert ratio >= 4.5, f"{path.name} {name} {tokens[name]} on {surface} = {ratio:.2f}"


def test_fill_only_accent_is_never_used_as_text_color():
    portal = PORTAL.read_text(encoding="utf-8")
    admin = ADMIN.read_text(encoding="utf-8")
    kb = KB_ADMIN.read_text(encoding="utf-8")
    for name, html, fill in (("portal", portal, "--accent"), ("admin", admin, "--accent"), ("kb", kb, "--primary")):
        assert not re.search(rf"(?<![-\w])color:\s*var\({fill}\)", html), name
    # 隐私说明里的服务说明链接与「逐步说明」是绑定授权的阅读入口，必须用可读的深珊瑚。
    assert 'style="color:var(--accent-dark)">呼图云</a>' in portal
    assert '[data-theme="cream"] .tab.active { color: var(--accent-dark); }' in portal
    # 焦点环同属可见性：不得用 1.9:1 的填充粉。
    assert "outline: 3px solid var(--accent-dark)" in admin
    assert kb.count("outline: 3px solid var(--primary-ink)") == 2
    assert "rgba(37, 99, 235" not in kb, "知识库后台残留旧蓝色焦点/描边"
