from draw_portal.firmware_version import is_newer, parse_hutuji_version


def test_parse_ok():
    assert parse_hutuji_version("hutuji.1.2.3") == (1, 2, 3)


def test_parse_rejects_upstream_and_garbage():
    assert parse_hutuji_version("2.4.0") is None
    assert parse_hutuji_version("hutuji.1.0") is None
    assert parse_hutuji_version("hutuji.01.0.0") == (1, 0, 0)  # 允许前导零经 int


def test_is_newer_matrix():
    assert is_newer("hutuji.1.0.1", "hutuji.1.0.0") is True
    assert is_newer("hutuji.1.0.0", "hutuji.1.0.0") is False
    assert is_newer("hutuji.1.0.0", "hutuji.1.0.1") is False
    assert is_newer("hutuji.2.0.0", "hutuji.1.9.9") is True
    assert is_newer("nope", "hutuji.1.0.0") is False
