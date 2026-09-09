from draw_portal.teach_chars import extract_teach_chars, TEACH_MAX_CHARS


def test_extract_mixed():
    for sep in (",", "\uff0c"):
        chars, trunc = extract_teach_chars(f"春眠不觉晓{sep}hello 123")
        assert chars == "春眠不觉晓"
        assert trunc is False


def test_extract_fullwidth_comma():
    chars, trunc = extract_teach_chars("你好，世界")
    assert chars == "你好世界"
    assert trunc is False


def test_extract_truncate():
    s = "一二三四五六七八九十" + "甲乙丙丁戊己庚辛壬癸子"
    chars, trunc = extract_teach_chars(s)
    assert len(chars) == TEACH_MAX_CHARS == 20
    assert trunc is True
    assert chars.startswith("一二三四五六七八九十")


def test_extract_empty():
    assert extract_teach_chars("hello!") == ("", False)
