"""Чтение Excel-выгрузок: .xlsx (openpyxl) и старый .xls (xlrd) — 1С часто сохраняет в .xls.

Возвращает листы как списки строк-кортежей, даты — datetime, как у openpyxl.
"""

from __future__ import annotations

from pathlib import Path

import openpyxl

EXTENSIONS = (".xlsx", ".xls")


def read_sheets(path: Path) -> dict[str, list[tuple]]:
    if Path(path).suffix.lower() == ".xls":
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
