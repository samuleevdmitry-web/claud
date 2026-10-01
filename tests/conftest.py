from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest

from app.keywords.loader import KeywordSet, load_keyword_set

ROOT = Path(__file__).resolve().parent.parent
KEYWORDS_FILE = ROOT / "Velmorium_ключевые_слова_тендеры.xlsx"


@pytest.fixture(scope="session")
def keywords_file() -> Path:
    assert KEYWORDS_FILE.exists(), "файл ключевых слов должен лежать в корне проекта"
    return KEYWORDS_FILE


@pytest.fixture(scope="session")
def ks(keywords_file) -> KeywordSet:
    return load_keyword_set(str(keywords_file))


def make_workbook(path: Path, sheets: dict[str, list[list]]) -> Path:
    """Создаёт xlsx с заданными листами (первая строка — заголовок)."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for title, rows in sheets.items():
        ws = wb.create_sheet(title)
        for row in rows:
            ws.append(row)
    wb.save(path)
    return path


PRODUCTS_HEADER = [
    "Категория",
    "Ключевое слово / фраза",
    "Маска для парсера (* = любые окончания, | = или)",
    "Тип",
    "Приоритет",
    "Комментарий",
]
MARKERS_HEADER = ["Группа", "Маркер", "Маска для парсера", "Приоритет", "Комментарий"]
MINUS_HEADER = ["Группа", "Минус-слово", "Маска для парсера", "Сила исключения", "Комментарий"]
CODES_HEADER = ["Код", "Классификатор", "Наименование", "Товары Velmorium", "Приоритет", "Комментарий"]
QUERIES_HEADER = ["Тип", "Запрос", "Комментарий"]


@pytest.fixture
def small_workbook(tmp_path) -> Path:
    return make_workbook(
        tmp_path / "small.xlsx",
        {
            "Товарные ключи": [
                PRODUCTS_HEADER,
                ["Тапочки", "тапочки одноразовые", "тапоч* одноразов*", "Самодостаточный", 1, ""],
                ["Полотенца", "полотенце", "полотенц*", "Нужен маркер", 1, ""],
                ["Наматрасники", "топпер", "топпер*", "Самодостаточный", 1, ""],
            ],
            "Маркеры заказчика": [MARKERS_HEADER, ["Тип", "санаторий", "санатори*", 1, ""]],
            "Минус-слова": [MINUS_HEADER, ["Медицина", "больница", "больниц*", "Сильный", ""]],
            "Коды ОКПД2-КТРУ": [CODES_HEADER, ["13.92.14", "ОКПД2", "Бельё туалетное", "Полотенца", 1, ""]],
            "Готовые запросы": [QUERIES_HEADER, ["Без логики (фраза)", "тапочки одноразовые", ""]],
        },
    )
