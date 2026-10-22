"""Версии файла ключей: активация, пересчёт, замена файла меняет результаты без правки кода."""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import KeywordSetVersion, Notification, Tender
from app.services.keywords import (
    KeywordFileRejected,
    activate_keyword_file,
    bootstrap_keywords,
    get_active_keyword_set,
)
from tests.conftest import MARKERS_HEADER, MINUS_HEADER, PRODUCTS_HEADER, make_workbook


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _tender(title, customer="") -> Tender:
    return Tender(title=title, customer_name=customer, dedup_key=title[:64])


def test_bootstrap_imports_root_file_once(session, keywords_file):
    version = bootstrap_keywords(session, keywords_file)
    assert version is not None and version.is_active
    assert version.summary["products"] == 77
    assert bootstrap_keywords(session, keywords_file) is None


def test_bootstrap_picks_up_changed_project_file_but_not_over_manual_upload(
    session, small_workbook, tmp_path
):
    root = tmp_path / "root.xlsx"
    root.write_bytes(small_workbook.read_bytes())
    first = bootstrap_keywords(session, root)
    changed = make_workbook(
        tmp_path / "changed.xlsx",
        {
            "Товарные ключи": [PRODUCTS_HEADER, ["К", "полотенце", "полотенц*", "Нужен маркер", 1, ""]],
            "Маркеры заказчика": [MARKERS_HEADER, ["Тип", "отель", "отел*", 1, ""]],
            "Минус-слова": [MINUS_HEADER, ["М", "больница", "больниц*", "Сильный", ""]],
        },
    )
    root.write_bytes(changed.read_bytes())
    second = bootstrap_keywords(session, root)  # файл проекта изменился — новая версия
    assert second is not None and second.id != first.id and second.is_active
    assert bootstrap_keywords(session, root) is None  # тот же файл — ничего не делаем

    manual = activate_keyword_file(session, small_workbook.read_bytes(), "мой.xlsx", uploaded_by="admin")
    root.write_bytes(changed.read_bytes())  # файл проекта отличается от загруженного вручную
    assert bootstrap_keywords(session, root) is None  # загруженный вручную файл не перезаписываем
    assert manual.is_active


def test_activation_reclassifies_and_new_file_changes_results(session, small_workbook, tmp_path):
    activate_keyword_file(session, small_workbook.read_bytes(), "v1.xlsx")
    tender = _tender("Поставка топперов", "ООО «Ромашка»")
    session.add(tender)
    session.flush()
    from app.services.keywords import reclassify_all

    version, ks = get_active_keyword_set(session)
    reclassify_all(session, version.id, ks)
    assert tender.relevance == "relevant"
    assert tender.keyword_set_id == version.id

    # Новая версия: «топпер» стал «Нужен маркер» — тот же тендер уходит на проверку.
    v2 = make_workbook(
        tmp_path / "v2.xlsx",
        {
            "Товарные ключи": [PRODUCTS_HEADER, ["Наматрасники", "топпер", "топпер*", "Нужен маркер", 1, ""]],
            "Маркеры заказчика": [MARKERS_HEADER, ["Тип", "санаторий", "санатори*", 1, ""]],
            "Минус-слова": [MINUS_HEADER, ["Медицина", "больница", "больниц*", "Сильный", ""]],
        },
    )
    version2 = activate_keyword_file(session, v2.read_bytes(), "v2.xlsx", uploaded_by="тест")
    assert tender.relevance == "review"
    assert tender.keyword_set_id == version2.id
    active = session.scalars(select(KeywordSetVersion).where(KeywordSetVersion.is_active)).all()
    assert [v.id for v in active] == [version2.id]
    assert session.scalars(select(Notification)).all()


def test_tender_losing_all_keys_is_kept_as_rejected(session, small_workbook, tmp_path):
    activate_keyword_file(session, small_workbook.read_bytes(), "v1.xlsx")
    tender = _tender("Поставка топперов")
    session.add(tender)
    session.flush()
    v2 = make_workbook(
        tmp_path / "v2.xlsx",
        {
            "Товарные ключи": [PRODUCTS_HEADER, ["Халаты", "халат", "халат*", "Нужен маркер", 1, ""]],
            "Маркеры заказчика": [MARKERS_HEADER],
            "Минус-слова": [MINUS_HEADER],
        },
    )
    activate_keyword_file(session, v2.read_bytes(), "v2.xlsx")
    assert tender.relevance == "rejected"
    assert session.get(Tender, tender.id) is not None


def test_file_with_mask_errors_needs_confirmation(session, tmp_path):
    bad = make_workbook(
        tmp_path / "bad.xlsx",
        {
            "Товарные ключи": [
                PRODUCTS_HEADER,
                ["К", "топпер", "топпер*", "Самодостаточный", 1, ""],
                ["К", "битая", "(халат*", "Самодостаточный", 1, ""],
            ],
            "Маркеры заказчика": [MARKERS_HEADER],
            "Минус-слова": [MINUS_HEADER],
        },
    )
    with pytest.raises(KeywordFileRejected):
        activate_keyword_file(session, bad.read_bytes(), "bad.xlsx")
    version = activate_keyword_file(session, bad.read_bytes(), "bad.xlsx", allow_errors=True)
    assert version.summary["errors"] == 1


def test_unusable_file_is_rejected_even_with_allow_errors(session):
    with pytest.raises(KeywordFileRejected):
        activate_keyword_file(session, b"garbage", "x.xlsx", allow_errors=True)
