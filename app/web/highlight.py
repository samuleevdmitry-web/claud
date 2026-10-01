"""Подсветка найденных ключей, маркеров и минус-слов в тексте поля."""

from __future__ import annotations

from markupsafe import Markup, escape

_CLASS = {
    "product": "hl-product",
    "marker": "hl-marker",
    "minus": "hl-minus",
}


def highlight(text: str, matches: list[dict], field: str, position_index: int | None = None) -> Markup:
    spans = sorted(
        (
            m
            for m in matches
            if m.get("field") == field
            and m.get("position_index") == position_index
            and m.get("kind") in _CLASS
        ),
        key=lambda m: (m["start"], -m["end"]),
    )
    out: list[str] = []
    cursor = 0
    for m in spans:
        start, end = m["start"], m["end"]
        if start < cursor:  # пересечения: показываем первое, более длинное совпадение
            continue
        out.append(str(escape(text[cursor:start])))
        css = _CLASS[m["kind"]] + (" hl-suppressed" if m.get("suppressed") else "")
        title = f"{m.get('label', '')} · маска: {m.get('mask', '')}"
        out.append(f'<mark class="{css}" title="{escape(title)}">{escape(text[start:end])}</mark>')
        cursor = end
    out.append(str(escape(text[cursor:])))
    return Markup("".join(out))
