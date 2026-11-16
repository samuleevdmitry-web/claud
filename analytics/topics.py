"""О чём письмо и что купили: сравнение по основам слов.

Тема письма — это слова темы, текстов ссылок и адресов страниц, куда ведут ссылки (в адресах слова латиницей:
/membrannye_tkani/ → «мембранные ткани»). Заказ — это номенклатура и группы номенклатуры из 1С. Совпадение —
общие основы слов (первые 5 букв), кроме общих слов вроде «ткань», «каталог», «купить» и цветов, которые есть
почти везде и ничего не говорят о товаре.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

STEM = 5

_TRANSLIT = [("shch", "щ"), ("sch", "щ"), ("yo", "е"), ("zh", "ж"), ("kh", "х"), ("ts", "ц"), ("ch", "ч"), ("sh", "ш"),
             ("yu", "ю"), ("ya", "я"), ("ye", "е"), ("iy", "ий"), ("yy", "ый"), ("ay", "ай"), ("ey", "ей"),
             ("oy", "ой"), ("uy", "уй"),
             ("a", "а"), ("b", "б"), ("v", "в"), ("g", "г"), ("d", "д"), ("e", "е"), ("z", "з"), ("i", "и"), ("y", "ы"),
             ("k", "к"), ("l", "л"), ("m", "м"), ("n", "н"), ("o", "о"), ("p", "п"), ("r", "р"), ("s", "с"), ("t", "т"),
             ("u", "у"), ("f", "ф"), ("h", "х"), ("c", "к"), ("w", "в"), ("x", "кс"), ("j", "дж"), ("q", "к")]
_LATIN = re.compile("|".join(re.escape(a) for a, _ in _TRANSLIT))
_TR = dict(_TRANSLIT)
_WORD = re.compile(r"[a-zа-я]+")

# Слова, которые есть почти в любом письме или заказе и не говорят, какой товар: точные слова и начала слов.
STOP_WORDS = """
для или все наш ваш вас нас это как при без под над www http https html php utm index detail section product catalog
сайт опт оптом шт черный черная белый белая серый серая синий синяя красный красная зеленый желтый бежевый коричневый
хаки темно светло
""".split()
STOP_PREFIXES = tuple("""
ткан катал товар проду скидк акци распр новин новы нова ново оптов олдос заказ доста подро смотр перей узна здрав
уваж письм рассы отпис магаз стран главн разде купи цен метр погон рулон штук упако цвет
""".split())


def translit(word: str) -> str:
    return _LATIN.sub(lambda m: _TR[m.group(0)], word)


def _stem(word: str) -> str:
    return word[:STEM]


STOP = {_stem(translit(w)) for w in STOP_WORDS}


def stems(text: str | None) -> set[str]:
    """Основы значимых слов: латиница переводится в кириллицу, короткие (меньше 3 букв) и общие слова отброшены."""
    out = set()
    for word in _WORD.findall((text or "").lower().replace("ё", "е")):
        word = translit(word) if word.isascii() else word
        if len(word) < 3 or word.isdigit():
            continue
        s = _stem(word)
        if s not in STOP and not s.startswith(STOP_PREFIXES):
            out.add(s)
    return out


def url_words(url: str) -> str:
    """Слова из адреса страницы: путь без домена, через пробел."""
    path = unquote(urlsplit(url).path)
    return " ".join(re.split(r"[/_\-.]+", path))


def campaign_terms(subject: str | None, links: list[tuple[str, str]]) -> set[str]:
    """Тема письма: слова темы, текстов ссылок и адресов, куда они ведут."""
    text = " ".join([subject or ""] + [f"{text} {url_words(url)}" for url, text in links])
    return stems(text)


def order_terms(items: list[str] | None, groups: list[str] | None) -> set[str]:
    return stems(" ".join((items or []) + (groups or [])))
