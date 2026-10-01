from app.keywords.loader import KeyType, Strength, load_keyword_set
from app.keywords.masks import parse_mask
from tests.conftest import MARKERS_HEADER, MINUS_HEADER, PRODUCTS_HEADER, make_workbook


def test_real_file_every_mask_parses(ks, keywords_file):
    """Каждая маска реального файла разбирается без ошибок."""
    assert ks.errors == [], [str(e) for e in ks.errors]
    assert ks.is_usable
    # Повторно разбираем каждую маску напрямую — исключения быть не должно.
    for key in [*ks.products, *ks.markers, *ks.minus_words]:
        parse_mask(key.mask.source)


def test_real_file_counts(ks):
    s = ks.summary()
    assert s["products"] == 87
    assert s["markers"] == 30
    assert s["minus_words"] == 23
    assert s["queries"] == 23
    assert {p.type for p in ks.products} == set(KeyType)
    assert {n.strength for n in ks.minus_words} == set(Strength)


def test_real_file_only_expected_warning(ks):
    assert [w.mask for w in ks.warnings] == ["петл* | г/м"]


def test_real_file_codes(ks):
    codes = {c.code: c for c in ks.codes}
    assert codes["13.92.12.160"].excluded
    assert not codes["13.92.12"].excluded
    assert {"13.92.14.000-00000001", "13.92.14.000-00000002", "13.92.14.000-00000003"} <= codes.keys()
    assert {"14.14.13", "14.14.14", "15.20.12.113", "15.20.12.123"} <= codes.keys()


def test_real_file_plain_queries(ks):
    plain = [q.query for q in ks.queries if q.is_plain]
    assert "гостиничный текстиль" in plain
    assert len(plain) == 17


def test_missing_required_sheet(tmp_path):
    path = make_workbook(
        tmp_path / "bad.xlsx",
        {"Товарные ключи": [PRODUCTS_HEADER, ["К", "т", "топпер*", "Самодостаточный", 1, ""]]},
    )
    ks = load_keyword_set(str(path))
    assert not ks.is_usable
    assert {i.sheet for i in ks.errors} == {"Маркеры заказчика", "Минус-слова"}


def test_bad_mask_reported_with_row(tmp_path):
    path = make_workbook(
        tmp_path / "bad.xlsx",
        {
            "Товарные ключи": [
                PRODUCTS_HEADER,
                ["К", "ок", "топпер*", "Самодостаточный", 1, ""],
                ["К", "сломанная", "халат* (гостиниц*", "Самодостаточный", 1, ""],
                ["К", "странный тип", "плед*", "Непонятный", "x", ""],
            ],
            "Маркеры заказчика": [MARKERS_HEADER, ["Т", "отель", "отел*", 1, ""]],
            "Минус-слова": [MINUS_HEADER, ["М", "больница", "больниц*", "Очень", ""]],
        },
    )
    ks = load_keyword_set(str(path))
    assert ks.is_usable
    assert [(e.sheet, e.row) for e in ks.errors] == [("Товарные ключи", 3)]
    assert len(ks.products) == 2
    assert ks.products[1].type == KeyType.NEEDS_MARKER
    assert ks.products[1].priority is None
    assert ks.minus_words[0].strength == Strength.MEDIUM
    assert len([w for w in ks.warnings if w.row]) == 3


def test_unreadable_file():
    ks = load_keyword_set(b"not an xlsx")
    assert not ks.is_usable
    assert ks.errors
