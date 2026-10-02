"""Поисковые запросы к площадкам, построенные из файла ключевых слов.

В запросы идут:
* все «Самодостаточные» товарные ключи;
* товарные ключи «Нужен маркер» с приоритетом 1–2;
* фразы с листа «Готовые запросы» с типом «Без логики».

«Уточняющие» ключи (ткани, размеры) в поиск не идут: по файлу они «сами по себе не ищутся»,
а запрос «сатин» или «евро» завалил бы выдачу тканями и чем угодно.

Поиск площадок морфологический, поэтому вместо маски берём фразу из колонки
«Ключевое слово / фраза» в её основной форме («полотенце махровое»).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.keywords.loader import KeyType, KeywordSet
from app.keywords.text import normalize


@dataclass(frozen=True)
class SearchQuery:
    text: str
    origin: str  # откуда взят: «ключ, стр. 12» / «готовый запрос, стр. 5»


def _phrase_variants(phrase: str) -> list[str]:
    """«простыня / простынь» → оба варианта; «полотенце для рук / для лица» → только первый."""
    phrase = re.sub(r"\([^)]*\)", " ", phrase)
    parts = [" ".join(p.split()) for p in phrase.split("/")]
    parts = [p for p in parts if p]
    if not parts:
        return []
    first_len = len(parts[0].split())
    return [parts[0]] + [p for p in parts[1:] if len(p.split()) >= first_len]


def _key(text: str) -> str:
    return " ".join(normalize(text).split())


def build_queries(ks: KeywordSet) -> list[SearchQuery]:
    seen: dict[str, SearchQuery] = {}

    def add(text: str, origin: str) -> None:
        text = " ".join(text.replace("ё", "е").replace("Ё", "Е").split())
        if len(text) >= 3 and _key(text) not in seen:
            seen[_key(text)] = SearchQuery(text, origin)

    for key in ks.products:
        take = key.type == KeyType.SELF or (key.type == KeyType.NEEDS_MARKER and key.priority in (1, 2))
        if take:
            for variant in _phrase_variants(key.phrase):
                add(variant, f"товарный ключ, стр. {key.row}")
    for q in ks.queries:
        if q.is_plain:
            add(q.query, f"готовый запрос, стр. {q.row}")
    return list(seen.values())


def search_codes(ks: KeywordSet) -> list[str]:
    """Коды ОКПД2/КТРУ для поиска (без кодов-исключений)."""
    return list(dict.fromkeys(c.code for c in ks.codes if not c.excluded))
