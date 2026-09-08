"""i2i 提示词必须导向艺术家素描（笔式可画），而非上色本描边或灰阶照片。"""
from __future__ import annotations

from hutuji_mcp import modelscope_client

PROMPT = modelscope_client.I2I_LINE_ART_PROMPT
RETRY = modelscope_client.I2I_SPARSE_HATCH_RETRY_PROMPT
# 旧名仍导出，须与稀疏排线重试同源（量产不得丢掉排线感）
FALLBACK = modelscope_client.I2I_CONTOUR_FALLBACK_PROMPT


def test_i2i_prompt_demands_sketch_language() -> None:
    sketch_markers = ("素描", "排线", "结构线", "cross-hatch", "hatching", "sketch")
    assert any(m in PROMPT for m in sketch_markers), PROMPT


def test_i2i_prompt_forbids_photo_gray_wash() -> None:
    forbid_markers = ("不要阴影", "不要灰度", "不要颜色")
    assert all(m in PROMPT for m in forbid_markers), PROMPT


def test_i2i_prompt_not_flat_coloring_book_only() -> None:
    assert "只用均匀的黑色单线条勾勒" not in PROMPT, PROMPT


def test_i2i_prompt_v5_long_sparse_hatch_not_dense() -> None:
    # 必须有排线感；禁止密交叉；倾向长/少/间距大
    assert "排线" in PROMPT, PROMPT
    assert "密交叉排线" in PROMPT or "禁止密交叉" in PROMPT, PROMPT
    assert any(m in PROMPT for m in ("长", "间距", "组数少", "少量")), PROMPT
    assert "交叉排线表现体积" not in PROMPT, PROMPT
    assert "短促" not in PROMPT, PROMPT  # v4 短排线措辞不得回潮


def test_i2i_prompt_subject_agnostic_and_likeness() -> None:
    assert "本人" in PROMPT or "五官" in PROMPT or "identity" in PROMPT.lower()
    assert any(x in PROMPT for x in ("物体", "场景", "主体", "subject")), PROMPT


def test_i2i_retry_keeps_hatch_sketch_feel() -> None:
    # 第二轮仍须带排线（用户硬约束）；不得再「不要排线」
    assert "排线" in RETRY
    assert "不要排线" not in RETRY
    assert RETRY == FALLBACK
    assert RETRY != PROMPT
