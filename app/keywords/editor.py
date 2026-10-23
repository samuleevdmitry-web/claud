"""Точечные правки файла ключевых слов (xlsx) из интерфейса: добавить минус-слово или маркер,
удалить строку, изменить силу минус-слова или тип товарного ключа.

Файл остаётся единственным источником ключей: правка создаёт новую версию xlsx, которая
активируется как обычная загрузка. Строки ищутся по листу и маске (номера строк между версиями
меняются), новые строки получают оформление последней строки листа.
"""

from __future__ import annotations

import io
import re
from copy import copy
from dataclasses import dataclass

from openpyxl import load_workbook

from app.keywords.loader import SHEET_MARKERS, SHEET_MINUS, SHEET_PRODUCTS, _find_sheet, _norm_header
from app.keywords.text import normalize

ADD_MINUS = "add_minus"
ADD_MARKER = "add_marker"
REMOVE = "remove"
MINUS_STRENGTH = "minus_strength"
PRODUCT_TYPE = "product_type"
ACTIONS = (ADD_MINUS, ADD_MARKER, REMOVE, MINUS_STRENGTH, PRODUCT_TYPE)
SHEETS = {"product": SHEET_PRODUCTS, "marker": SHEET_MARKERS, "minus": SHEET_MINUS}
STRENGTHS = ("Сильный", "Средний")
PRODUCT_TYPES = ("Самодостаточный", "Нужен маркер", "Уточняющий")
FEEDBACK_GROUP = "Из проверки тендеров"

# Колонки по началу заголовка — как в загрузчике.
_COLUMNS = {
    SHEET_PRODUCTS: {
        "group": ("категори",),
        "phrase": ("ключевое слово", "ключ"),
        "mask": ("маска",),
        "type": ("тип",),
        "priority": ("приоритет",),
        "comment": ("комментари",),
    },
    SHEET_MARKERS: {
        "group": ("групп",),
        "phrase": ("маркер",),
        "mask": ("маска",),
        "priority": ("приоритет",),
        "comment": ("комментари",),
    },
    SHEET_MINUS: {
        "group": ("групп",),
        "phrase": ("минус",),
        "mask": ("маска",),
        "strength": ("сила",),
        "comment": ("комментари",),
    },
}


class EditError(ValueError):
    pass


@dataclass(frozen=True)
class Edit:
    action: str
    kind: str = ""  # product / marker / minus — для remove, minus_strength, product_type
    mask: str = ""
    phrase: str = ""
    value: str = ""  # сила, тип ключа или приоритет маркера
    comment: str = ""

    def describe(self) -> str:
        what = {"product": "товарный ключ", "marker": "маркер", "minus": "минус-слово"}.get(self.kind, "")
        label = f"«{self.phrase or self.mask}»"
        if self.action == ADD_MINUS:
            return f"добавить минус-слово {label} ({self.value.lower()})"
        if self.action == ADD_MARKER:
            return f"добавить маркер заказчика {label}"
        if self.action == REMOVE:
            return f"удалить {what} {label}"
        if self.action == MINUS_STRENGTH:
            return f"минус-слово {label}: сила «{self.value}»"
        if self.action == PRODUCT_TYPE:
            return f"товарный ключ {label}: тип «{self.value}»"
        return self.action


def _same_mask(a, b: str) -> bool:
    return " ".join(str(a or "").split()).lower() == " ".join(b.split()).lower()


def _columns(ws, sheet: str) -> dict[str, int | None]:
    header = [_norm_header(c.value) for c in ws[1]]
    return {
        key: next((i for i, h in enumerate(header) if any(h.startswith(p) for p in prefixes)), None)
        for key, prefixes in _COLUMNS[sheet].items()
    }


def _find_row(ws, cols: dict[str, int | None], mask: str) -> int | None:
    if cols["mask"] is None:
        return None
    for row in ws.iter_rows(min_row=2):
        if _same_mask(row[cols["mask"]].value, mask):
            return row[0].row
    return None


def _append(ws, cols: dict[str, int | None], values: dict[str, object]) -> None:
    last = ws.max_row
    while last > 1 and not any(c.value not in (None, "") for c in ws[last]):
        last -= 1
    new = last + 1
    for key, idx in cols.items():
        if idx is None:
            continue
        cell = ws.cell(row=new, column=idx + 1, value=values.get(key))
        src = ws.cell(row=last, column=idx + 1)
        if src.has_style:
            cell._style = copy(src._style)


def _apply(wb, edit: Edit) -> None:
    if edit.action not in ACTIONS:
        raise EditError(f"неизвестное действие {edit.action!r}")
    if not edit.mask.strip():
        raise EditError("не указана маска")
    if edit.action == ADD_MINUS:
        sheet = SHEET_MINUS
    elif edit.action == ADD_MARKER:
        sheet = SHEET_MARKERS
    else:
        sheet = SHEETS.get(edit.kind)
        if sheet is None:
            raise EditError(f"неизвестный вид записи {edit.kind!r}")
    ws = _find_sheet(wb, sheet)
    if ws is None:
        raise EditError(f"в файле нет листа «{sheet}»")
    cols = _columns(ws, sheet)
    row = _find_row(ws, cols, edit.mask)

    if edit.action in (ADD_MINUS, ADD_MARKER):
        if row is not None:
            raise EditError(f"«{edit.mask}» уже есть на листе «{sheet}»")
        values: dict[str, object] = {
            "group": FEEDBACK_GROUP,
            "phrase": edit.phrase or edit.mask,
            "mask": edit.mask.strip(),
            "comment": edit.comment or None,
        }
        if edit.action == ADD_MINUS:
            if edit.value not in STRENGTHS:
                raise EditError(f"сила минус-слова — одно из: {', '.join(STRENGTHS)}")
            values["strength"] = edit.value
        else:
            values["group"] = "Тип заказчика"
            values["priority"] = int(edit.value) if edit.value.isdigit() else 1
        _append(ws, cols, values)
        return

    if row is None:
        raise EditError(f"«{edit.mask}» не найдено на листе «{sheet}» — возможно, файл уже изменён")
    if edit.action == REMOVE:
        ws.delete_rows(row)
    elif edit.action == MINUS_STRENGTH:
        if sheet != SHEET_MINUS or edit.value not in STRENGTHS or cols["strength"] is None:
            raise EditError("силу можно менять только у минус-слова: «Сильный» или «Средний»")
        ws.cell(row=row, column=cols["strength"] + 1, value=edit.value)
    elif edit.action == PRODUCT_TYPE:
        if sheet != SHEET_PRODUCTS or edit.value not in PRODUCT_TYPES or cols["type"] is None:
            raise EditError(f"тип товарного ключа — одно из: {', '.join(PRODUCT_TYPES)}")
        ws.cell(row=row, column=cols["type"] + 1, value=edit.value)


def apply_edits(content: bytes, edits: list[Edit]) -> bytes:
    """Новая версия файла с правками (исходные байты не меняются)."""
    if not edits:
        raise EditError("нет правок")
    wb = load_workbook(io.BytesIO(content))
    for edit in edits:
        _apply(wb, edit)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


_ENDINGS = sorted(
    "иями ями ами ого его ому ему ыми ими ая яя ое ее ые ие ый ий ой ей ам ям ах ях ом ем ов ев "
    "а я ы и е о у ю ь й".split(),
    key=len,
    reverse=True,
)


def suggest_mask(word: str) -> str:
    """Маска по слову: «общежитие» → «общежити*», «ГБУ» → «гбу»; фраза — по каждому слову."""
    parts = []
    for w in re.findall(r"[\wё-]+", normalize(word)):
        if len(w) <= 4 or not re.search(r"[а-я]", w):
            parts.append(w)
            continue
        stem = w
        for end in _ENDINGS:
            if w.endswith(end) and len(w) - len(end) >= 4:
                stem = w[: -len(end)]
                break
        parts.append(stem + "*")
    return " ".join(parts)
