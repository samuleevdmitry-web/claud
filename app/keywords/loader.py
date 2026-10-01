"""Загрузка файла ключевых слов (xlsx) в структуру KeywordSet.

Файл — единственный источник ключей. Ошибки разбора не прерывают загрузку:
они собираются в список issues, чтобы показать их в предпросмотре.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from enum import StrEnum
from typing import BinaryIO

import openpyxl

from app.keywords.codes import ClassifierCode, CodeMatcher, expand_code_cell
from app.keywords.masks import Mask, MaskSyntaxError, parse_mask

SHEET_PRODUCTS = "Товарные ключи"
SHEET_MARKERS = "Маркеры заказчика"
SHEET_MINUS = "Минус-слова"
SHEET_CODES = "Коды ОКПД2-КТРУ"
SHEET_QUERIES = "Готовые запросы"
REQUIRED_SHEETS = (SHEET_PRODUCTS, SHEET_MARKERS, SHEET_MINUS)


class KeyType(StrEnum):
    SELF = "self"  # «Самодостаточный»
    NEEDS_MARKER = "needs_marker"  # «Нужен маркер»
    REFINING = "refining"  # «Уточняющий»


KEY_TYPE_LABELS = {
    KeyType.SELF: "Самодостаточный",
    KeyType.NEEDS_MARKER: "Нужен маркер",
    KeyType.REFINING: "Уточняющий",
}


class Strength(StrEnum):
    STRONG = "strong"
    MEDIUM = "medium"


STRENGTH_LABELS = {Strength.STRONG: "Сильный", Strength.MEDIUM: "Средний"}


@dataclass(frozen=True)
class Issue:
    level: str  # "error" | "warning"
    sheet: str
    row: int | None
    message: str
    mask: str = ""

    def __str__(self) -> str:
        where = f"{self.sheet}, строка {self.row}" if self.row else self.sheet
        return f"[{where}] {self.message}"


@dataclass(eq=False)
class ProductKey:
    id: str
    row: int
    category: str
    phrase: str
    mask: Mask
    type: KeyType
    priority: int | None
    comment: str = ""


@dataclass(eq=False)
class CustomerMarker:
    id: str
    row: int
    group: str
    marker: str
    mask: Mask
    priority: int | None
    comment: str = ""


@dataclass(eq=False)
class MinusWord:
    id: str
    row: int
    group: str
    word: str
    mask: Mask
    strength: Strength
    comment: str = ""


@dataclass(frozen=True)
class ReadyQuery:
    row: int
    type: str
    query: str
    comment: str = ""

    @property
    def is_plain(self) -> bool:
        return self.type.strip().lower().startswith("без логики")


@dataclass(eq=False)
class KeywordSet:
    products: list[ProductKey] = field(default_factory=list)
    markers: list[CustomerMarker] = field(default_factory=list)
    minus_words: list[MinusWord] = field(default_factory=list)
    codes: list[ClassifierCode] = field(default_factory=list)
    queries: list[ReadyQuery] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "warning"]

    @property
    def is_usable(self) -> bool:
        """Набор можно применять: есть товарные ключи и обязательные листы."""
        missing = any(i.level == "error" and i.row is None for i in self.issues)
        return bool(self.products) and not missing

    @property
    def code_matcher(self) -> CodeMatcher:
        return CodeMatcher(self.codes)

    def summary(self) -> dict[str, int]:
        return {
            "products": len(self.products),
            "markers": len(self.markers),
            "minus_words": len(self.minus_words),
            "codes": len([c for c in self.codes if not c.excluded]),
            "excluded_codes": len([c for c in self.codes if c.excluded]),
            "queries": len(self.queries),
            "errors": len(self.errors),
            "warnings": len(self.warnings),
        }


# --------------------------------------------------------------------------- чтение листов


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def _norm_header(value) -> str:
    return " ".join(_cell(value).lower().replace("ё", "е").split())


def _find_sheet(wb, title: str):
    want = _norm_header(title)
    for ws in wb.worksheets:
        if _norm_header(ws.title) == want:
            return ws
    return None


class _SheetReader:
    """Находит колонки по началу заголовка, чтобы не зависеть от точной формулировки."""

    def __init__(self, ws, columns: dict[str, tuple[str, ...]], issues: list[Issue]):
        self.ws = ws
        self.issues = issues
        rows = list(ws.iter_rows(values_only=True))
        self.header = [_norm_header(v) for v in rows[0]] if rows else []
        self.rows = rows[1:]
        self.index: dict[str, int | None] = {}
        for key, prefixes in columns.items():
            self.index[key] = next(
                (i for i, h in enumerate(self.header) if any(h.startswith(p) for p in prefixes)), None
            )

    def missing(self, *keys: str) -> list[str]:
        return [k for k in keys if self.index.get(k) is None]

    def __iter__(self):
        for offset, values in enumerate(self.rows):
            if not any(_cell(v) for v in values):
                continue
            row = {
                k: (_cell(values[i]) if i is not None and i < len(values) else "")
                for k, i in self.index.items()
            }
            yield offset + 2, row  # номер строки в Excel (1 — заголовок)


def _priority(value: str, sheet: str, row: int, issues: list[Issue]) -> int | None:
    if not value:
        return None
    try:
        p = int(float(value.replace(",", ".")))
    except ValueError:
        issues.append(Issue("warning", sheet, row, f"приоритет «{value}» не число — считаю пустым"))
        return None
    if p not in (1, 2, 3):
        issues.append(Issue("warning", sheet, row, f"приоритет {p} вне диапазона 1–3"))
    return p


def _parse_mask(source: str, sheet: str, row: int, issues: list[Issue]) -> Mask | None:
    try:
        mask = parse_mask(source)
    except MaskSyntaxError as exc:
        issues.append(Issue("error", sheet, row, f"маска не разобрана: {exc}", source))
        return None
    for w in mask.warnings:
        issues.append(Issue("warning", sheet, row, w, source))
    return mask


def _load_products(wb, ks: KeywordSet) -> None:
    ws = _find_sheet(wb, SHEET_PRODUCTS)
    if ws is None:
        ks.issues.append(Issue("error", SHEET_PRODUCTS, None, "нет листа"))
        return
    reader = _SheetReader(
        ws,
        {
            "category": ("категори",),
            "phrase": ("ключевое слово", "ключ"),
            "mask": ("маска",),
            "type": ("тип",),
            "priority": ("приоритет",),
            "comment": ("комментари",),
        },
        ks.issues,
    )
    if miss := reader.missing("mask", "type"):
        ks.issues.append(Issue("error", SHEET_PRODUCTS, None, f"не найдены колонки: {', '.join(miss)}"))
        return
    types = {
        "самодостаточный": KeyType.SELF,
        "нужен маркер": KeyType.NEEDS_MARKER,
        "уточняющий": KeyType.REFINING,
    }
    for row, r in reader:
        if not r["mask"]:
            ks.issues.append(Issue("error", SHEET_PRODUCTS, row, f"пустая маска у ключа «{r['phrase']}»"))
            continue
        key_type = types.get(_norm_header(r["type"]))
        if key_type is None:
            ks.issues.append(
                Issue(
                    "warning", SHEET_PRODUCTS, row, f"неизвестный тип «{r['type']}» — считаю «Нужен маркер»"
                )
            )
            key_type = KeyType.NEEDS_MARKER
        mask = _parse_mask(r["mask"], SHEET_PRODUCTS, row, ks.issues)
        if mask is None:
            continue
        ks.products.append(
            ProductKey(
                id=f"p{row}",
                row=row,
                category=r["category"] or "Без категории",
                phrase=r["phrase"] or r["mask"],
                mask=mask,
                type=key_type,
                priority=_priority(r["priority"], SHEET_PRODUCTS, row, ks.issues),
                comment=r["comment"],
            )
        )


def _load_markers(wb, ks: KeywordSet) -> None:
    ws = _find_sheet(wb, SHEET_MARKERS)
    if ws is None:
        ks.issues.append(Issue("error", SHEET_MARKERS, None, "нет листа"))
        return
    reader = _SheetReader(
        ws,
        {
            "group": ("групп",),
            "marker": ("маркер",),
            "mask": ("маска",),
            "priority": ("приоритет",),
            "comment": ("комментари",),
        },
        ks.issues,
    )
    if reader.missing("mask"):
        ks.issues.append(Issue("error", SHEET_MARKERS, None, "не найдена колонка «Маска»"))
        return
    for row, r in reader:
        if not r["mask"]:
            ks.issues.append(Issue("error", SHEET_MARKERS, row, f"пустая маска у маркера «{r['marker']}»"))
            continue
        mask = _parse_mask(r["mask"], SHEET_MARKERS, row, ks.issues)
        if mask is None:
            continue
        ks.markers.append(
            CustomerMarker(
                id=f"m{row}",
                row=row,
                group=r["group"],
                marker=r["marker"] or r["mask"],
                mask=mask,
                priority=_priority(r["priority"], SHEET_MARKERS, row, ks.issues),
                comment=r["comment"],
            )
        )


def _load_minus(wb, ks: KeywordSet) -> None:
    ws = _find_sheet(wb, SHEET_MINUS)
    if ws is None:
        ks.issues.append(Issue("error", SHEET_MINUS, None, "нет листа"))
        return
    reader = _SheetReader(
        ws,
        {
            "group": ("групп",),
            "word": ("минус",),
            "mask": ("маска",),
            "strength": ("сила",),
            "comment": ("комментари",),
        },
        ks.issues,
    )
    if reader.missing("mask"):
        ks.issues.append(Issue("error", SHEET_MINUS, None, "не найдена колонка «Маска»"))
        return
    for row, r in reader:
        if not r["mask"]:
            ks.issues.append(Issue("error", SHEET_MINUS, row, f"пустая маска у минус-слова «{r['word']}»"))
            continue
        strength_raw = _norm_header(r["strength"])
        if strength_raw.startswith("сильн"):
            strength = Strength.STRONG
        elif strength_raw.startswith("средн"):
            strength = Strength.MEDIUM
        else:
            ks.issues.append(
                Issue("warning", SHEET_MINUS, row, f"неизвестная сила «{r['strength']}» — считаю «Средний»")
            )
            strength = Strength.MEDIUM
        mask = _parse_mask(r["mask"], SHEET_MINUS, row, ks.issues)
        if mask is None:
            continue
        ks.minus_words.append(
            MinusWord(
                id=f"n{row}",
                row=row,
                group=r["group"],
                word=r["word"] or r["mask"],
                mask=mask,
                strength=strength,
                comment=r["comment"],
            )
        )


def _load_codes(wb, ks: KeywordSet) -> None:
    ws = _find_sheet(wb, SHEET_CODES)
    if ws is None:
        ks.issues.append(Issue("warning", SHEET_CODES, None, "нет листа — поиск по кодам отключён"))
        return
    reader = _SheetReader(
        ws,
        {
            "code": ("код",),
            "classifier": ("классификатор",),
            "name": ("наименован",),
            "goods": ("товары",),
            "priority": ("приоритет",),
            "comment": ("комментари",),
        },
        ks.issues,
    )
    for row, r in reader:
        if not r["code"]:
            continue
        priority = _priority(r["priority"], SHEET_CODES, row, ks.issues)
        excluded = priority is None and "исключить" in r["comment"].lower()
        try:
            expanded = expand_code_cell(r["code"])
        except ValueError as exc:
            ks.issues.append(Issue("error", SHEET_CODES, row, str(exc), r["code"]))
            continue
        for code in expanded:
            ks.codes.append(
                ClassifierCode(
                    code=code,
                    classifier=r["classifier"],
                    name=r["name"],
                    goods=r["goods"],
                    priority=priority,
                    excluded=excluded,
                    comment=r["comment"],
                    row=row,
                )
            )


def _load_queries(wb, ks: KeywordSet) -> None:
    ws = _find_sheet(wb, SHEET_QUERIES)
    if ws is None:
        ks.issues.append(Issue("warning", SHEET_QUERIES, None, "нет листа"))
        return
    reader = _SheetReader(ws, {"type": ("тип",), "query": ("запрос",), "comment": ("комментари",)}, ks.issues)
    for row, r in reader:
        if r["query"]:
            ks.queries.append(ReadyQuery(row=row, type=r["type"], query=r["query"], comment=r["comment"]))


def load_keyword_set(source: bytes | BinaryIO | str) -> KeywordSet:
    """Читает xlsx (путь, байты или файловый объект) и возвращает KeywordSet с issues."""
    if isinstance(source, bytes):
        source = io.BytesIO(source)
    ks = KeywordSet()
    try:
        wb = openpyxl.load_workbook(source, read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 — любой сбой чтения показываем пользователю
        ks.issues.append(Issue("error", "файл", None, f"не удалось прочитать xlsx: {exc}"))
        return ks
    try:
        _load_products(wb, ks)
        _load_markers(wb, ks)
        _load_minus(wb, ks)
        _load_codes(wb, ks)
        _load_queries(wb, ks)
    finally:
        wb.close()
    return ks
