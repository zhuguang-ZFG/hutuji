from pathlib import Path


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
