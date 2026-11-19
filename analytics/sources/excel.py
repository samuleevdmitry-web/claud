"""Чтение выгрузок: .xlsx (openpyxl), старый .xls (xlrd) — 1С часто сохраняет в .xls, — «Excel» из Битрикс24
(на деле HTML-таблица с расширением .xls) и CSV.

Возвращает листы как списки строк-кортежей, даты — datetime, как у openpyxl.
"""

from __future__ import annotations

import csv
import io
from html.parser import HTMLParser
from pathlib import Path

import openpyxl

EXTENSIONS = (".xlsx", ".xls", ".csv")


def read_sheets(path: Path) -> dict[str, list[tuple]]:
    path = Path(path)
    head = path.read_bytes()[:2048].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if head.startswith((b"<", b"<!doctype")) or b"<table" in head or b"<html" in head:
        return _read_html(path)
    if path.suffix.lower() == ".csv":
        return _read_csv(path)
    if path.suffix.lower() == ".xls":
        return _read_xls(path)
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        return {name: list(wb[name].iter_rows(values_only=True)) for name in wb.sheetnames}
    finally:
        wb.close()


def xls_value(cell, datemode: int):
    """Ячейка xlrd → значение: пустая — None, дата — datetime, целое число — int, остальное как есть."""
    import xlrd

    if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
        return None
    if cell.ctype == xlrd.XL_CELL_DATE:
        return xlrd.xldate_as_datetime(cell.value, datemode)
    if cell.ctype == xlrd.XL_CELL_NUMBER and float(cell.value).is_integer():
        return int(cell.value)
    if cell.ctype == xlrd.XL_CELL_BOOLEAN:
        return bool(cell.value)
    if cell.ctype == xlrd.XL_CELL_ERROR:
        return None
    return cell.value


def _read_xls(path: Path) -> dict[str, list[tuple]]:
    import xlrd

    wb = xlrd.open_workbook(str(path), on_demand=True)
    try:
        out = {}
        for sheet in wb.sheets():
            out[sheet.name] = [tuple(xls_value(sheet.cell(r, c), wb.datemode) for c in range(sheet.ncols))
                               for r in range(sheet.nrows)]
        return out
    finally:
        wb.release_resources()


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _cell(value: str):
    text = " ".join(value.split())
    if not text:
        return None
    try:
        return int(text) if text.lstrip("-").isdigit() and len(text) < 12 else text
    except ValueError:
        return text


class _Tables(HTMLParser):
    """Строки всех таблиц HTML: ячейки th/td — текстом, <br> — переносом (несколько телефонов в одной ячейке)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[list[tuple]] = []
        self.row: list[str] | None = None
        self.cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.tables.append([])
        elif tag == "tr":
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []
        elif tag == "br" and self.cell is not None:
            self.cell.append(", ")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None and self.row is not None:
            self.row.append("".join(self.cell))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if not self.tables:
                self.tables.append([])
            self.tables[-1].append(tuple(_cell(v) for v in self.row))
            self.row = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)


def _read_html(path: Path) -> dict[str, list[tuple]]:
    parser = _Tables()
    parser.feed(_decode(path.read_bytes()))
    tables = [t for t in parser.tables if t]
    return {(path.stem if i == 0 else f"{path.stem} {i + 1}"): rows for i, rows in enumerate(tables)}


def _read_csv(path: Path) -> dict[str, list[tuple]]:
    text = _decode(path.read_bytes())
    sample = text[:4096]
    delimiter = max((";", ",", "\t"), key=sample.count)
    return {path.stem: [tuple(_cell(v) for v in row) for row in csv.reader(io.StringIO(text), delimiter=delimiter)]}
