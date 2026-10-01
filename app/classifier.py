"""Классификация тендера по набору ключевых слов. Чистый модуль: без БД и сети.

Правила (из ТЗ):
* Релевантный — есть «Самодостаточный» товарный ключ, либо («Нужен маркер»-ключ или код
  ОКПД2 из списка) вместе с маркером заказчика; и нигде нет минус-слов.
* Отклонён — есть товарный ключ или код, но «сильное» минус-слово стоит в названии закупки или
  в наименовании заказчика, а маркера заказчика нет.
* На проверку — всё остальное, где есть хотя бы один товарный ключ или код из списка
  (включая релевантные, у которых нашлось «среднее» минус-слово или «сильное» — только в
  позициях, месте поставки или ТЗ, а также при наличии маркера).
* None — нет ни одного товарного ключа и кода: тендер не сохраняется.

Минус-слово, целиком лежащее внутри найденного товарного ключа или маркера (например,
«кроват*» внутри «дорожк* на кроват*»), не учитывается и помечается как suppressed.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from enum import StrEnum

from app.keywords.codes import normalize_code
from app.keywords.loader import KeyType, KeywordSet, Strength
from app.keywords.text import TokenIndex


class Relevance(StrEnum):
    RELEVANT = "relevant"
    REVIEW = "review"
    REJECTED = "rejected"


RELEVANCE_LABELS = {
    Relevance.RELEVANT: "Релевантный",
    Relevance.REVIEW: "На проверку",
    Relevance.REJECTED: "Отклонён",
}


class Field(StrEnum):
    TITLE = "title"
    POSITIONS = "positions"
    CUSTOMER = "customer"
    DELIVERY_PLACE = "delivery_place"
    CODES = "codes"
    NOTICE = "notice"


FIELD_LABELS = {
    Field.TITLE: "Название закупки",
    Field.POSITIONS: "Позиции",
    Field.CUSTOMER: "Заказчик",
    Field.DELIVERY_PLACE: "Место поставки",
    Field.CODES: "Коды ОКПД2/КТРУ",
    Field.NOTICE: "Извещение / ТЗ",
}

# Поля, где «сильное» минус-слово ведёт к отклонению (при отсутствии маркера).
HEAD_FIELDS = (Field.TITLE, Field.CUSTOMER)


@dataclass
class PositionText:
    name: str
    code: str | None = None


@dataclass
class TenderText:
    title: str
    customer_name: str = ""
    delivery_place: str = ""
    positions: list[PositionText] = field(default_factory=list)
    codes: list[str] = field(default_factory=list)
    notice_text: str = ""

    def all_codes(self) -> list[str]:
        seen: dict[str, None] = {}
        for code in [*self.codes, *(p.code for p in self.positions if p.code)]:
            norm = normalize_code(code)
            if norm:
                seen.setdefault(norm, None)
        return list(seen)

    def text_fields(self) -> Iterable[tuple[Field, int | None, str]]:
        yield Field.TITLE, None, self.title or ""
        yield Field.CUSTOMER, None, self.customer_name or ""
        yield Field.DELIVERY_PLACE, None, self.delivery_place or ""
        for i, pos in enumerate(self.positions):
            yield Field.POSITIONS, i, pos.name or ""
        yield Field.NOTICE, None, self.notice_text or ""


@dataclass
class MatchRecord:
    kind: str  # product | marker | minus | code | code_excluded
    key_id: str
    label: str  # фраза / маркер / минус-слово / код из файла
    mask: str
    field: str
    position_index: int | None
    fragment: str  # найденный текст
    context: str  # фрагмент с окружением для показа
    start: int  # смещение в исходном тексте поля
    end: int
    category: str = ""
    key_type: str = ""
    priority: int | None = None
    strength: str = ""
    suppressed: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Classification:
    relevance: Relevance | None
    score: float
    matches: list[MatchRecord]
    categories: list[str]
    reasons: list[str]

    @property
    def has_product(self) -> bool:
        return self.relevance is not None


_CONTEXT = 40


def _context(text: str, start: int, end: int) -> str:
    left = max(0, start - _CONTEXT)
    right = min(len(text), end + _CONTEXT)
    return ("…" if left > 0 else "") + text[left:right].strip() + ("…" if right < len(text) else "")


def _field_matches(
    index: TokenIndex, fld: Field, pos_index: int | None, kind: str, key, label: str, **extra
) -> list[MatchRecord]:
    found: list[MatchRecord] = []
    for span in key.mask.find(index):
        start, end = index.char_span(span.start, span.end)
        found.append(
            MatchRecord(
                kind=kind,
                key_id=key.id,
                label=label,
                mask=key.mask.source,
                field=fld.value,
                position_index=pos_index,
                fragment=index.text[start:end],
                context=_context(index.text, start, end),
                start=start,
                end=end,
                **extra,
            )
        )
    return found


def _find_matches(ks: KeywordSet, tender: TenderText) -> list[MatchRecord]:
    matches: list[MatchRecord] = []
    for fld, pos_index, text in tender.text_fields():
        if not text.strip():
            continue
        index = TokenIndex.build(text)
        if not index.tokens:
            continue
        where = (index, fld, pos_index)
        for p in ks.products:
            matches += _field_matches(
                *where,
                "product",
                p,
                p.phrase,
                category=p.category,
                key_type=p.type.value,
                priority=p.priority,
            )
        for m in ks.markers:
            matches += _field_matches(*where, "marker", m, m.marker, category=m.group, priority=m.priority)
        for n in ks.minus_words:
            matches += _field_matches(*where, "minus", n, n.word, category=n.group, strength=n.strength.value)

    matcher = ks.code_matcher
    for code in tender.all_codes():
        listed = matcher.match(code)
        if listed is None:
            continue
        matches.append(
            MatchRecord(
                kind="code_excluded" if listed.excluded else "code",
                key_id=f"c{listed.row}:{listed.code}",
                label=listed.code,
                mask="",
                field=Field.CODES.value,
                position_index=None,
                fragment=code,
                context=f"{code} — {listed.name}",
                start=0,
                end=len(code),
                category=listed.goods,
                priority=listed.priority,
            )
        )
    _suppress_nested_minus(matches)
    return matches


def _suppress_nested_minus(matches: list[MatchRecord]) -> None:
    covers = [m for m in matches if m.kind in ("product", "marker")]
    for m in matches:
        if m.kind != "minus":
            continue
        for c in covers:
            if (
                c.field == m.field
                and c.position_index == m.position_index
                and c.start <= m.start
                and m.end <= c.end
                and (c.start, c.end) != (m.start, m.end)
            ):
                m.suppressed = True
                break


_PRODUCT_WEIGHT = {1: 10.0, 2: 6.0, 3: 3.0, None: 2.0}
_TYPE_FACTOR = {KeyType.SELF.value: 1.5, KeyType.NEEDS_MARKER.value: 1.0, KeyType.REFINING.value: 0.4}
_MARKER_WEIGHT = {1: 8.0, 2: 5.0, 3: 2.0, None: 2.0}
CUSTOMER_MARKER_BONUS = 6.0
CATEGORY_BONUS = 3.0
CODE_WEIGHT = 4.0
MINUS_PENALTY = {Strength.STRONG.value: 10.0, Strength.MEDIUM.value: 5.0}


def _score(matches: list[MatchRecord]) -> float:
    score = 0.0
    seen_products: dict[str, MatchRecord] = {}
    for m in matches:
        if m.kind == "product":
            seen_products.setdefault(m.key_id, m)
    for m in seen_products.values():
        score += _PRODUCT_WEIGHT.get(m.priority, 2.0) * _TYPE_FACTOR.get(m.key_type, 1.0)
    categories = {m.category for m in seen_products.values() if m.key_type != KeyType.REFINING.value}
    score += CATEGORY_BONUS * max(0, len(categories) - 1)

    markers = {m.key_id: m for m in matches if m.kind == "marker"}
    if markers:
        score += max(_MARKER_WEIGHT.get(m.priority, 2.0) for m in markers.values())
        if any(m.field == Field.CUSTOMER.value for m in markers.values()):
            score += CUSTOMER_MARKER_BONUS

    codes = {m.label for m in matches if m.kind == "code"}
    score += CODE_WEIGHT * min(len(codes), 3)

    minus = {m.key_id: m for m in matches if m.kind == "minus" and not m.suppressed}
    score -= sum(MINUS_PENALTY.get(m.strength, 5.0) for m in minus.values())
    return round(score, 1)


def classify(ks: KeywordSet, tender: TenderText) -> Classification:
    matches = _find_matches(ks, tender)
    products = [m for m in matches if m.kind == "product"]
    codes = [m for m in matches if m.kind == "code"]
    markers = [m for m in matches if m.kind == "marker"]
    minus = [m for m in matches if m.kind == "minus" and not m.suppressed]

    has_self = any(m.key_type == KeyType.SELF.value for m in products)
    has_needs_marker = any(m.key_type == KeyType.NEEDS_MARKER.value for m in products) or bool(codes)
    has_marker = bool(markers)
    strong_head = [m for m in minus if m.strength == Strength.STRONG.value and m.field in HEAD_FIELDS]
    other_minus = [m for m in minus if m not in strong_head]

    categories = sorted({m.category for m in products if m.key_type != KeyType.REFINING.value})
    categories += sorted({m.category for m in codes if m.category} - set(categories))
    reasons: list[str] = []

    if not products and not codes:
        reasons.append("нет товарных ключей и кодов ОКПД2 из списка")
        return Classification(None, _score(matches), matches, categories, reasons)

    relevant_base = has_self or (has_needs_marker and has_marker)
    if has_self:
        reasons.append("есть самодостаточный товарный ключ")
    elif has_needs_marker and has_marker:
        reasons.append("товарный ключ/код + маркер заказчика")
    elif has_needs_marker:
        reasons.append("товарный ключ/код без маркера заказчика")
    else:
        reasons.append("только уточняющие товарные ключи")

    if strong_head and not has_marker:
        reasons.append("сильное минус-слово в названии/заказчике без маркера: " + _labels(strong_head))
        relevance = Relevance.REJECTED
    elif relevant_base and not minus:
        relevance = Relevance.RELEVANT
    else:
        if strong_head:
            reasons.append(
                "сильное минус-слово в названии/заказчике при наличии маркера: " + _labels(strong_head)
            )
        if other_minus:
            reasons.append("минус-слова: " + _labels(other_minus))
        relevance = Relevance.REVIEW
    return Classification(relevance, _score(matches), matches, categories, reasons)


def _labels(matches: list[MatchRecord]) -> str:
    return ", ".join(
        dict.fromkeys(f"«{m.fragment}» ({FIELD_LABELS[Field(m.field)].lower()})" for m in matches)
    )
