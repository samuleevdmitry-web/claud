"""Парсер и сопоставление масок из файла ключевых слов.

Синтаксис маски:
    *            — любое окончание слова (может стоять в любом месте слова);
    |            — «или» (на верхнем уровне и внутри скобок);
    ( … )        — группировка вариантов: «халат* для (гостиниц*|отел*)»;
    [её]         — один символ из набора;
    слова подряд — фраза: порядок важен, между словами допускается не более 2 других слов;
    дефис или /  — внутри слова («мини-отел*», «спа-*», «г/м») делят его на части,
                   которые должны идти в тексте строго подряд.

Сопоставление идёт по целым словам (токенам) нормализованного текста, поэтому
«отел*» не совпадает с «отдел», а «сиз» не совпадает с «сизо».
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.keywords.text import TokenIndex, fix_homoglyphs, normalize

DEFAULT_MAX_GAP = 2
_WILDCARD_RE = "[0-9a-zа-я,]*"
_WORD_CHARS_RE = re.compile(r"[0-9a-zа-я]")
# Основа на «согласный + к/ц» перед «*»: в родительном падеже множественного числа появляется
# беглая гласная (подушк* → подушек, полотенц* → полотенец, салфетк* → салфеток).
# Прилагательные на «-ск-» (детск*, туристическ*) не затрагиваются.
_FLEETING_RE = re.compile(r"^([0-9a-zа-я]*(?:[бвгджзклмнпртфхцчшщ]|(?<!с)с(?!к)))([кц])\*$")


class MaskSyntaxError(ValueError):
    def __init__(self, message: str, position: int | None = None):
        super().__init__(message)
        self.message = message
        self.position = position

    def __str__(self) -> str:
        if self.position is None:
            return self.message
        return f"{self.message} (позиция {self.position + 1})"


@dataclass(frozen=True, eq=False)
class WordPattern:
    """Одно слово маски, например «полотенц*» или «при[её]м*»."""

    source: str
    regex: re.Pattern[str]
    prefix: str  # буквальное начало слова до первого * или [

    def token_positions(self, index: TokenIndex) -> frozenset[int]:
        found: set[int] = set()
        for word in index.words_with_prefix(self.prefix):
            if self.regex.fullmatch(word):
                found.update(index.positions[word])
        return frozenset(found)


@dataclass(frozen=True, eq=False)
class Sequence:
    """Фраза: элементы по порядку; gaps[i] — сколько слов можно пропустить перед items[i + 1]."""

    items: tuple[WordPattern | Group, ...]
    gaps: tuple[int, ...]


@dataclass(frozen=True, eq=False)
class Group:
    alternatives: tuple[Sequence, ...]


@dataclass(frozen=True)
class Span:
    start: int  # индекс первого токена
    end: int  # индекс токена после последнего


@dataclass(eq=False)
class Mask:
    source: str
    root: Group
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # пояснения к разбору (например, беглые гласные)

    def find(self, index: TokenIndex) -> list[Span]:
        """Все непересекающиеся вхождения маски (слева направо, самое короткое совпадение)."""
        if not index.tokens:
            return []
        matcher = _Matcher(index)
        starts = sorted(matcher.first_positions(self.root))
        spans: list[Span] = []
        last_end = -1
        for start in starts:
            if start < last_end:
                continue
            ends = matcher.elem_ends(self.root, start)
            if ends:
                end = min(ends)
                spans.append(Span(start, end))
                last_end = end
        return spans

    def matches(self, text: str) -> bool:
        return bool(self.find(TokenIndex.build(text)))


class _Matcher:
    def __init__(self, index: TokenIndex):
        self.index = index
        self.n = len(index.tokens)
        self._positions: dict[int, frozenset[int]] = {}
        self._firsts: dict[int, frozenset[int]] = {}

    def positions(self, word: WordPattern) -> frozenset[int]:
        key = id(word)
        if key not in self._positions:
            self._positions[key] = word.token_positions(self.index)
        return self._positions[key]

    def first_positions(self, elem: WordPattern | Group) -> frozenset[int]:
        key = id(elem)
        if key not in self._firsts:
            if isinstance(elem, WordPattern):
                result = self.positions(elem)
            else:
                result = frozenset().union(*(self.first_positions(alt.items[0]) for alt in elem.alternatives))
            self._firsts[key] = result
        return self._firsts[key]

    def elem_ends(self, elem: WordPattern | Group, pos: int) -> set[int]:
        if isinstance(elem, WordPattern):
            return {pos + 1} if pos in self.positions(elem) else set()
        ends: set[int] = set()
        for alt in elem.alternatives:
            ends |= self.seq_ends(alt, pos)
        return ends

    def seq_ends(self, seq: Sequence, pos: int) -> set[int]:
        ends = self.elem_ends(seq.items[0], pos)
        for item, gap in zip(seq.items[1:], seq.gaps, strict=True):
            nxt: set[int] = set()
            for end in ends:
                for skip in range(gap + 1):
                    p = end + skip
                    if p >= self.n:
                        break
                    nxt |= self.elem_ends(item, p)
            ends = nxt
            if not ends:
                break
        return ends


# --------------------------------------------------------------------------- разбор


def parse_mask(source: str, max_gap: int = DEFAULT_MAX_GAP, fleeting_vowels: bool = True) -> Mask:
    """Разбирает маску. При синтаксической ошибке бросает MaskSyntaxError.

    fleeting_vowels — дополнительно принимать форму с беглой гласной («подушк*» → «подушек»).
    """
    if source is None or not str(source).strip():
        raise MaskSyntaxError("пустая маска")
    parser = _Parser(str(source), max_gap, fleeting_vowels)
    root = parser.parse()
    return Mask(source=str(source), root=root, warnings=parser.warnings, notes=parser.notes)


_LEX_RE = re.compile(r"\s+|[()|]|[^\s()|]+")
_QUOTES_RE = re.compile("[«»„“”‟\"'`‘’]")


class _Parser:
    def __init__(self, source: str, max_gap: int, fleeting_vowels: bool = True):
        self.source = source
        self.max_gap = max_gap
        self.fleeting_vowels = fleeting_vowels
        self.warnings: list[str] = []
        self.notes: list[str] = []
        source = _QUOTES_RE.sub(" ", source)
        self.lexemes: list[tuple[str, int]] = [
            (m.group(), m.start()) for m in _LEX_RE.finditer(source) if not m.group().isspace()
        ]
        self.i = 0

    def peek(self) -> str | None:
        return self.lexemes[self.i][0] if self.i < len(self.lexemes) else None

    def pos(self) -> int:
        return self.lexemes[self.i][1] if self.i < len(self.lexemes) else len(self.source)

    def parse(self) -> Group:
        group = self.parse_alternatives()
        if self.peek() is not None:
            raise MaskSyntaxError("лишняя закрывающая скобка", self.pos())
        return group

    def parse_alternatives(self) -> Group:
        alts = [self.parse_sequence()]
        while self.peek() == "|":
            self.i += 1
            alts.append(self.parse_sequence())
        return Group(tuple(alts))

    def parse_sequence(self) -> Sequence:
        start_pos = self.pos()
        items: list[WordPattern | Group] = []
        gaps: list[int] = []
        while (lex := self.peek()) is not None and lex not in (")", "|"):
            if lex == "(":
                open_pos = self.pos()
                self.i += 1
                group = self.parse_alternatives()
                if self.peek() != ")":
                    raise MaskSyntaxError("не закрыта скобка", open_pos)
                self.i += 1
                parts: list[WordPattern | Group] = [group]
                inner_gaps: list[int] = []
            else:
                word_pos = self.pos()
                self.i += 1
                parts, inner_gaps = self.parse_word(lex, word_pos)
            if items:
                gaps.append(self.max_gap)
            items.extend(parts)
            gaps.extend(inner_gaps)
        if not items:
            raise MaskSyntaxError(
                "пустой вариант (два «|» подряд, «|» в начале/конце или пустые скобки)", start_pos
            )
        if all(
            isinstance(it, WordPattern) and it.prefix == "" and it.source.strip("*") == "" for it in items
        ):
            raise MaskSyntaxError("вариант состоит только из «*» и совпадёт с любым текстом", start_pos)
        return Sequence(tuple(items), tuple(gaps))

    def parse_word(self, word: str, word_pos: int) -> tuple[list[WordPattern | Group], list[int]]:
        raw_parts = re.split(r"[-‐‑‒–—/]", word)
        parts = [p for p in raw_parts if p]
        if not parts:
            raise MaskSyntaxError(f"слово «{word}» не содержит букв", word_pos)
        if "/" in word and "*" not in parts[-1]:
            self.warnings.append(
                f"«{word}»: «/» делит слово на части {', '.join(f'«{p}»' for p in parts)}; "
                f"без «*» последняя часть совпадёт только с точным словом (например, не с «{parts[-1]}2»)"
            )
        patterns: list[WordPattern | Group] = [self.compile_part(p, word_pos) for p in parts]
        return patterns, [0] * (len(patterns) - 1)

    def compile_part(self, part: str, word_pos: int) -> WordPattern:
        norm = fix_homoglyphs(normalize(part))
        regex: list[str] = []
        prefix: list[str] = []
        literal = True
        i = 0
        while i < len(norm):
            ch = norm[i]
            if ch == "*":
                regex.append(_WILDCARD_RE)
                literal = False
                i += 1
            elif ch == "[":
                close = norm.find("]", i + 1)
                if close == -1:
                    raise MaskSyntaxError(f"в слове «{part}» не закрыта «[»", word_pos)
                chars = norm[i + 1 : close]
                if not chars or not all(_WORD_CHARS_RE.fullmatch(c) for c in chars):
                    raise MaskSyntaxError(f"в слове «{part}» неверный набор символов «[{chars}]»", word_pos)
                regex.append("[" + re.escape("".join(sorted(set(chars)))) + "]")
                literal = False
                i = close + 1
            elif ch == "]":
                raise MaskSyntaxError(f"в слове «{part}» лишняя «]»", word_pos)
            elif ch == "," and 0 < i < len(norm) - 1 and norm[i - 1].isdigit() and norm[i + 1].isdigit():
                regex.append(",")
                if literal:
                    prefix.append(",")
                i += 1
            elif _WORD_CHARS_RE.fullmatch(ch):
                regex.append(re.escape(ch))
                if literal:
                    prefix.append(ch)
                i += 1
            else:
                raise MaskSyntaxError(f"недопустимый символ «{part[i]}» в слове «{part}»", word_pos)
        pattern, literal_prefix = "".join(regex), "".join(prefix)
        fleeting = _FLEETING_RE.match(norm) if self.fleeting_vowels else None
        if fleeting:
            stem, last = fleeting.group(1), fleeting.group(2)
            pattern = f"(?:{pattern}|{re.escape(stem)}[ео]{last})"
            literal_prefix = stem
            self.notes.append(f"«{part}» также ловит форму с беглой гласной «{stem}[ео]{last}»")
        return WordPattern(source=part, regex=re.compile(pattern), prefix=literal_prefix)
