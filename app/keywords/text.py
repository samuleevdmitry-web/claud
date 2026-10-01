"""Нормализация и токенизация текста для сопоставления с масками.

Нормализация сохраняет длину строки: каждый символ заменяется ровно одним символом.
Благодаря этому позиции токенов в нормализованной строке совпадают с позициями
в исходном тексте, и найденный фрагмент можно подсветить в оригинале.
"""

from __future__ import annotations

import re
from bisect import bisect_left
from collections.abc import Iterator
from dataclasses import dataclass, field

# Символы, которые при сравнении считаются разделителями слов.
_SEPARATORS = (
    "«»„“”‟\"'`‘’‚‛‹›"  # кавычки
    "-‐‑‒–—―−﹘﹣－"  # дефисы и тире
    "/\\|"
    "     ​\t\r\n"
)
_CHAR_MAP: dict[str, str] = {ch: " " for ch in _SEPARATORS}
_CHAR_MAP.update({"ё": "е", "Ё": "е", "²": "2", "³": "3", "¹": "1"})

# Латинские буквы, которые выглядят как кириллические. Заменяются только в словах,
# где смешаны оба алфавита («cатин» с латинской «c»).
_HOMOGLYPHS = str.maketrans({"a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х", "y": "у", "k": "к"})

_DECIMAL_RE = re.compile(r"(?<=\d)\.(?=\d)")
TOKEN_RE = re.compile(r"\d+(?:,\d+)+|[0-9a-zа-я]+")
_CYR_RE = re.compile(r"[а-я]")
_LAT_RE = re.compile(r"[a-z]")


def normalize(text: str) -> str:
    """Приводит текст к нижнему регистру, ё→е, кавычки/дефисы/слэши → пробел.

    Длина результата равна длине исходной строки.
    """
    out: list[str] = []
    for ch in text:
        mapped = _CHAR_MAP.get(ch)
        if mapped is None:
            low = ch.lower()
            mapped = low if len(low) == 1 else ch
            if mapped == "ё":
                mapped = "е"
        out.append(mapped)
    return _DECIMAL_RE.sub(",", "".join(out))


def fix_homoglyphs(word: str) -> str:
    if _CYR_RE.search(word) and _LAT_RE.search(word):
        return word.translate(_HOMOGLYPHS)
    return word


@dataclass(frozen=True, slots=True)
class Token:
    text: str
    start: int
    end: int


def tokenize(text: str) -> list[Token]:
    norm = normalize(text)
    return [Token(fix_homoglyphs(m.group()), m.start(), m.end()) for m in TOKEN_RE.finditer(norm)]


@dataclass
class TokenIndex:
    """Токены одного текстового поля и индекс «слово → позиции» для быстрого поиска по префиксу."""

    text: str
    tokens: list[Token]
    positions: dict[str, list[int]] = field(default_factory=dict)
    sorted_words: list[str] = field(default_factory=list)

    @classmethod
    def build(cls, text: str) -> TokenIndex:
        tokens = tokenize(text)
        positions: dict[str, list[int]] = {}
        for i, tok in enumerate(tokens):
            positions.setdefault(tok.text, []).append(i)
        return cls(text=text, tokens=tokens, positions=positions, sorted_words=sorted(positions))

    def words_with_prefix(self, prefix: str) -> Iterator[str]:
        if not prefix:
            yield from self.sorted_words
            return
        i = bisect_left(self.sorted_words, prefix)
        while i < len(self.sorted_words) and self.sorted_words[i].startswith(prefix):
            yield self.sorted_words[i]
            i += 1

    def char_span(self, start_tok: int, end_tok: int) -> tuple[int, int]:
        return self.tokens[start_tok].start, self.tokens[end_tok - 1].end
