from app.keywords.text import TokenIndex, normalize, tokenize


def test_normalize_keeps_length_and_offsets():
    text = "«Санаторий «Ёлки»» — мини‑отель / 1.5-спальный"
    norm = normalize(text)
    assert len(norm) == len(text)
    assert "елки" in norm
    assert "«" not in norm and "—" not in norm and "/" not in norm


def test_tokenize_lowercase_yo_hyphen_quotes():
    words = [t.text for t in tokenize("Поставка «Тапочек»; МИНИ-ОТЕЛЬ «Ёлочка»")]
    assert words == ["поставка", "тапочек", "мини", "отель", "елочка"]


def test_tokenize_decimal_numbers():
    words = [t.text for t in tokenize("КПБ 1,5-спальный и 1.5 спальный, плотность 365 г/м²")]
    assert words.count("1,5") == 2
    assert ["г", "м2"] == words[-2:]


def test_tokenize_does_not_glue_words_by_comma():
    assert [t.text for t in tokenize("простыни,наволочки")] == ["простыни", "наволочки"]


def test_homoglyphs_in_mixed_word():
    # «cатин» с латинской «c» в начале
    assert [t.text for t in tokenize("cатин")] == ["сатин"]
    # чисто латинские слова не трогаем
    assert [t.text for t in tokenize("hotel")] == ["hotel"]


def test_char_span_points_to_original_text():
    text = "Поставка «Тапочек одноразовых»"
    idx = TokenIndex.build(text)
    start, end = idx.char_span(1, 3)
    assert text[start:end] == "Тапочек одноразовых"
