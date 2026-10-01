import pytest

from app.keywords.masks import MaskSyntaxError, parse_mask
from app.keywords.text import TokenIndex


def found(mask: str, text: str) -> list[str]:
    idx = TokenIndex.build(text)
    return [text[slice(*idx.char_span(s.start, s.end))] for s in parse_mask(mask).find(idx)]


def matches(mask: str, text: str) -> bool:
    return parse_mask(mask).matches(text)


# --------------------------------------------------------------------------- базовый синтаксис


def test_star_means_any_ending():
    assert matches("полотенц*", "Полотенце махровое")
    assert matches("полотенц*", "полотенцами")
    assert not matches("полотенц*", "полотно")


def test_word_without_star_is_exact():
    assert matches("кпб", "Комплект КПБ евро")
    assert not matches("кпб", "КПБшки")
    assert matches("сиз", "поставка СИЗ")
    assert not matches("сиз", "СИЗО-1")


def test_word_boundaries():
    assert matches("отел*", "для отеля")
    assert not matches("отел*", "для отдела кадров")
    assert not matches("отел*", "мотель")  # не середина слова
    assert matches("мотел*", "мотель")


def test_case_and_yo_insensitive():
    assert matches("стёган*", "ОДЕЯЛО СТЕГАНОЕ")
    assert matches("стеган*", "одеяло стёганое")
    assert matches("отбел*", "Отбелённое бельё")


def test_top_level_alternatives():
    assert matches("hotel textile | hotel linen", "Hotel linen supply")
    assert matches("пошив* бель* | пошив* текстил*", "Пошив текстиля для номеров")
    assert not matches("пошив* бель* | пошив* текстил*", "пошив штор")


def test_groups():
    mask = "халат* для (гостиниц*|отел*)"
    assert matches(mask, "Халаты для гостиницы")
    assert matches(mask, "халат махровый для отеля")
    assert not matches(mask, "халат для больницы")


def test_group_with_phrase_alternatives_and_nested_groups():
    mask = "(подушк*|одеял*) для (гостиниц*|отел*)"
    assert found(mask, "Поставка одеял для отеля «Заря»") == ["одеял для отеля"]
    assert matches("(дом* отдых*|санатори*) (сочи|крым*)", "дом отдыха Сочи")


def test_char_class():
    assert matches("дом* при[её]м*", "Дом приёмов")
    assert matches("дом* при[её]м*", "дом приемов")


# --------------------------------------------------------------------------- фразы


def test_phrase_order_matters():
    assert matches("постельн* бель*", "постельное бельё")
    assert not matches("постельн* бель*", "бельё постельное")


def test_phrase_allows_up_to_two_words_between():
    assert matches("постельн* бель*", "постельного белья")
    assert matches("постельн* бель*", "постельного нательного белья")
    assert matches("постельн* бель*", "постельного и нательного белья")
    assert not matches("постельн* бель*", "постельного и для нательного белья")


def test_hyphen_inside_mask_word_means_adjacent_parts():
    assert matches("мини-отел*", "Мини-отель «Уют»")
    assert matches("мини-отел*", "мини отель")
    assert not matches("мини-отел*", "мини бар в отеле")


def test_spa_hyphen_star():
    mask = "спа-* | spa | термальн*"
    assert matches(mask, "СПА-отель")
    assert matches(mask, "SPA комплекс")
    assert not matches(mask, "спасательный круг")


def test_trailing_hyphen_and_numbers():
    assert matches("фсин | исправительн* | сизо | ик-", "ФКУ ИК-5 УФСИН")
    assert matches("евро | 1,5 | двуспальн* | king", "КПБ 1,5-спальный")
    assert matches("евро | 1,5 | двуспальн* | king", "КПБ 1.5 спальный")


def test_quotes_in_mask_are_ignored():
    assert matches('"дом отдыха"', "Дом отдыха «Берёзка»")


def test_slash_in_mask_word_warns():
    mask = parse_mask("петл* | г/м")
    assert mask.warnings and "«/»" in mask.warnings[0]
    assert mask.matches("плотность 450 г/м")


def test_find_returns_fragments_and_non_overlapping():
    text = "Полотенца, полотенце банное и ещё полотенца"
    assert found("полотенц*", text) == ["Полотенца", "полотенце", "полотенца"]


def test_fleeting_vowel_genitive_plural():
    assert matches("полотенц*", "Поставка полотенец")
    assert matches("подушк*", "подушек перьевых")
    assert matches("салфетк* махров*", "салфеток махровых")
    assert matches("тапоч* | тапк*", "тапок одноразовых")
    assert not matches("детск*", "детсек")  # прилагательные на -ск- не трогаем
    assert not parse_mask("полотенц*", fleeting_vowels=False).matches("полотенец")


# --------------------------------------------------------------------------- ошибки


@pytest.mark.parametrize(
    "mask",
    [
        "",
        "   ",
        "(отел*",
        "отел*)",
        "a || b",
        "| отел*",
        "отел* |",
        "()",
        "(|отел*)",
        "*",
        "* *",
        "при[её м*",
        "отел]*",
        "отел#",
        "полотенце@",
    ],
)
def test_syntax_errors(mask):
    with pytest.raises(MaskSyntaxError):
        parse_mask(mask)


def test_syntax_error_has_position():
    with pytest.raises(MaskSyntaxError) as exc:
        parse_mask("халат* для (гостиниц*|отел*")
    assert exc.value.position == 11
