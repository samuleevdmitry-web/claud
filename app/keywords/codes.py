"""Коды ОКПД2/КТРУ из листа «Коды ОКПД2-КТРУ» и сопоставление с кодами позиций тендера."""

from __future__ import annotations

import re
from dataclasses import dataclass

_RANGE_RE = re.compile(r"^(?P<head>.*?)(?P<first>\d+)\s*(?:…|\.{2,3})\s*(?P<last>\d+)$")
_CODE_RE = re.compile(r"^\d{2}(?:\.\d{1,3}){0,3}(?:-\d{1,11})?$")


def normalize_code(code: str) -> str:
    return re.sub(r"\s+", "", str(code)).strip(".")


def expand_code_cell(cell: str) -> list[str]:
    """Разворачивает ячейку с кодами.

    «14.14.13 / 14.14.14» → два кода;
    «13.92.14.000-00000001…03» → 13.92.14.000-00000001, …-00000002, …-00000003.
    """
    codes: list[str] = []
    for part in re.split(r"[/;\n]", str(cell)):
        part = normalize_code(part)
        if not part:
            continue
        m = _RANGE_RE.match(part)
        if m:
            head, first, last = m.group("head"), m.group("first"), m.group("last")
            base = first[: len(first) - len(last)]
            width = len(last)
            start, stop = int(first[-width:]), int(last)
            if stop < start or stop - start > 1000:
                raise ValueError(f"неверный диапазон кодов «{part}»")
            codes.extend(f"{head}{base}{n:0{width}d}" for n in range(start, stop + 1))
        else:
            codes.append(part)
    for code in codes:
        if not _CODE_RE.match(code):
            raise ValueError(f"код «{code}» не похож на код ОКПД2/КТРУ")
    return codes


def code_matches(listed: str, actual: str) -> bool:
    """Код из файла покрывает код позиции, если совпадает с ним или является его началом."""
    return actual == listed or actual.startswith(listed + ".") or actual.startswith(listed + "-")


@dataclass(frozen=True)
class ClassifierCode:
    code: str
    classifier: str
    name: str
    goods: str
    priority: int | None
    excluded: bool
    comment: str = ""
    row: int = 0


class CodeMatcher:
    def __init__(self, codes: list[ClassifierCode]):
        self.codes = codes

    def match(self, actual: str) -> ClassifierCode | None:
        """Самый длинный код из файла, покрывающий код позиции (может оказаться кодом-исключением)."""
        actual = normalize_code(actual)
        best: ClassifierCode | None = None
        for c in self.codes:
            if code_matches(c.code, actual) and (best is None or len(c.code) > len(best.code)):
                best = c
        return best
