"""图库重发须刷新 MCP output mtime，避免 /files TTL 假过期。"""
from __future__ import annotations

import os
import time
from pathlib import Path

from draw_portal.push import _install_capability_pair


def test_install_capability_pair_refreshes_mtime(tmp_path: Path):
    src = tmp_path / "src"
    out = tmp_path / "out"
    src.mkdir()
    gcode = src / "draw.gcode"
    png = src / "preview.png"
    gcode.write_text("G21\n", encoding="ascii")
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    old = time.time() - 3600
    os.utime(gcode, (old, old))
    os.utime(png, (old, old))

    before = time.time()
    published = _install_capability_pair(
        gcode_src=gcode,
        png_src=png,
        mcp_output_dir=out,
        public_base_url="https://example.test",
    )
    after = time.time()

    g_dst = out / f"{published.basename}.gcode"
    p_dst = out / f"{published.basename}.png"
    assert g_dst.is_file() and p_dst.is_file()
    assert before - 1 <= g_dst.stat().st_mtime <= after + 1
    assert before - 1 <= p_dst.stat().st_mtime <= after + 1
    assert published.preview_url.endswith(f"/files/{published.basename}.png")
