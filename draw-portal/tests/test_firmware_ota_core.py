from draw_portal.firmware_catalog import load_catalog, plan_check, recommended_for_board
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
