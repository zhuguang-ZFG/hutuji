import hashlib
import hmac
import time
from urllib.parse import parse_qs, urlparse

from draw_portal.firmware_catalog import (
    build_bin_url,
    load_catalog,
    plan_check,
    recommended_for_board,
)
from draw_portal.firmware_ota import assert_upgrade_allowed


def test_plan_check_newer(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(
        '{"device":"s3","boards":{"freenove-esp32s3-display-2.8-lcd":'
        '{"version":"hutuji.1.0.1","notes":"fix","sha256":"aa","size":1,'
        '"relpath":"s3/freenove-esp32s3-display-2.8-lcd/hutuji.1.0.1/xiaozhi.bin"}}}',
        encoding="utf-8",
    )
    cat = load_catalog(p)
    out = plan_check(
        board="freenove-esp32s3-display-2.8-lcd",
        current_version="hutuji.1.0.0",
        catalog=cat,
    )
    assert out["update_available"] is True
    assert out["latest"] == "hutuji.1.0.1"


def test_busy_and_wrong_board_reject():
    assert assert_upgrade_allowed(job_state="streaming", board="a", manifest_board="a") == "busy"
    assert assert_upgrade_allowed(job_state="idle", board="a", manifest_board="b") == "board_mismatch"
    assert assert_upgrade_allowed(job_state="idle", board="a", manifest_board="a") is None


def test_build_bin_url_hmac_signs_stripped_rel():
    """HMAC 必须签 URL 里的 rel（lstrip '/'），不能签带 leading slash 的原 relpath。"""
    before = int(time.time())
    url = build_bin_url(
        "https://fw.example.test/",
        "/s3/board/v/xiaozhi.bin",
        secret="sekrit",
        ttl_sec=900,
    )
    after = int(time.time())
    parsed = urlparse(url)
    assert parsed.path == "/firmware/s3/board/v/xiaozhi.bin"
    qs = parse_qs(parsed.query)
    exp = int(qs["exp"][0])
    assert before + 900 <= exp <= after + 900
    rel = "s3/board/v/xiaozhi.bin"
    expect = hmac.new(
        b"sekrit", f"{rel}:{exp}".encode("utf-8"), hashlib.sha256
    ).hexdigest()
    assert qs["sig"][0] == expect
    # 若误用带 slash 的原文，签名应对不上
    wrong = hmac.new(
        b"sekrit", f"/s3/board/v/xiaozhi.bin:{exp}".encode("utf-8"), hashlib.sha256
    ).hexdigest()
    assert qs["sig"][0] != wrong
