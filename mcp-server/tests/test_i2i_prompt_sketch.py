"""i2i 提示词必须导向艺术家素描（笔式可画），而非上色本描边或灰阶照片。"""
from __future__ import annotations

from hutuji_mcp import modelscope_client

PROMPT = modelscope_client.I2I_LINE_ART_PROMPT
FALLBACK = modelscope_client.I2I_CONTOUR_FALLBACK_PROMPT


def test_i2i_prompt_demands_sketch_language() -> None:
    # 须显式要素描/排线/结构（中英任一稳定子串）
    sketch_markers = ("素描", "排线", "结构线", "cross-hatch", "hatching", "sketch")
    assert any(m in PROMPT for m in sketch_markers), PROMPT


def test_i2i_prompt_forbids_photo_gray_wash() -> None:
    # 必须禁止灰阶/照片感（现网已有「不要阴影」族；须保留或加强）
    forbid_markers = ("不要阴影", "不要灰度", "不要颜色")
    assert all(m in PROMPT for m in forbid_markers), PROMPT


def test_i2i_prompt_not_flat_coloring_book_only() -> None:
    # 旧 v2「只用均匀的黑色单线条勾勒」会把模型锁死上色本；不得保留该整句
    assert "只用均匀的黑色单线条勾勒" not in PROMPT, PROMPT


def test_i2i_prompt_v4_prefers_sparse_not_dense_hatch() -> None:
    # v3「交叉排线表现体积」在生产把描摹抬到 166+；v4 须禁密网、要稀疏
    assert "密交叉排线" in PROMPT or "不要交叉排线" in PROMPT, PROMPT
    assert any(m in PROMPT for m in ("稀疏", "少", "短促")), PROMPT
    assert "交叉排线表现体积" not in PROMPT, PROMPT


def test_i2i_prompt_subject_agnostic_and_likeness() -> None:
    # 人像要像本人；同时覆盖非人像物体/场景（不限死只处理人物）
    assert "本人" in PROMPT or "五官" in PROMPT or "identity" in PROMPT.lower()
    assert any(x in PROMPT for x in ("物体", "场景", "主体", "subject")), PROMPT


def test_i2i_contour_fallback_is_plotter_safer() -> None:
    assert "不要排线" in FALLBACK
    assert "本人" in FALLBACK or "五官" in FALLBACK
    assert FALLBACK != PROMPT
