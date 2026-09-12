"""机型标识是包线/换纸分流依据，两 Grbl 分支不能冒用同一 build。"""
import os
import re
from pathlib import Path

import pytest


def _build(root: Path) -> str:
    path = root / "Grbl_Esp32/src/Grbl.h"
    if not path.is_file():
        pytest.skip("外仓不可用")
    match = re.search(r'GRBL_VERSION_BUILD\s*=\s*"([^"]+)"', path.read_text(encoding="utf-8"))
    assert match
    return match.group(1)


def test_paper_and_nopaper_firmware_have_distinct_sku_markers():
    paper = Path(os.environ.get("GRBL_ROOT", "D:/Users/Grbl_Esp32"))
    nopaper = Path(os.environ.get("GRBL_MASSPROD_ROOT", "D:/Users/Grbl_Esp32-massprod"))
    # 分支结构证明角色，不能仅凭目录名或文档宣称是哪个机型。
    assert (paper / "Grbl_Esp32/src/PaperSystem.cpp").is_file()
    if not nopaper.is_dir():
        pytest.skip("量产 worktree 不可用")
    assert not (nopaper / "Grbl_Esp32/src/PaperSystem.cpp").exists()
    assert _build(paper) != _build(nopaper)


def test_nopaper_marker_matches_s3_selector():
    nopaper = Path(os.environ.get("GRBL_MASSPROD_ROOT", "D:/Users/Grbl_Esp32-massprod"))
    s3 = Path(os.environ.get("XIAOZHI_ROOT", "D:/Users/xiaozhi-esp32"))
    header = s3 / "main/boards/lichuang-dev/hutuji_nopaper_core.h"
    if not header.is_file():
        pytest.skip("S3 外仓不可用")
    match = re.search(r'kNopaperGrblBuild\s*=\s*"([^"]+)"', header.read_text(encoding="utf-8"))
    assert match and match.group(1) == _build(nopaper)
