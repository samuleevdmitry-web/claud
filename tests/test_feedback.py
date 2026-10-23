"""Оценка «подходит / не подходит», удаление тендеров, разбор ошибок и правки файла ключей."""

import io
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.keywords.editor import (
    ADD_MARKER,
    ADD_MINUS,
    MINUS_STRENGTH,
    PRODUCT_TYPE,
    REMOVE,
    Edit,
    EditError,
    apply_edits,
    suggest_mask,
)
from app.keywords.loader import KeyType, Strength, load_keyword_set
from app.models import Tender
from app.services.feedback import build_report, is_error, simulate, tender_fixes
from app.services.ingest import ingest
from app.services.keywords import activate_keyword_file, get_active_keyword_set
from app.services.tenders import (
    TenderFilters,
    delete_tenders,
    filtered_ids,
    list_tenders,
    restore_tenders,
    set_verdict,
    tab_counts,
)
from app.sources.base import TenderDetails

FUTURE = datetime.now(UTC) + timedelta(days=10)


@pytest.fixture
def session(keywords_file):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(engine, expire_on_commit=False)()
    activate_keyword_file(s, keywords_file.read_bytes(), keywords_file.name)
    s.flush()
    yield s
    s.close()


def add(session, ext_id: str, title: str, customer: str) -> Tender:
    version, ks = get_active_keyword_set(session)
    d = TenderDetails(
        "onlinecontract",
        ext_id,
        f"https://x/{ext_id}",
        title,
        customer_name=customer,
        application_deadline=FUTURE,
    )
    return ingest(session, d, ks, version.id).tender


def titles(session, **kw) -> set[str]:
    return {t.title for t in list_tenders(session, TenderFilters(**kw)).items}


# --------------------------------------------------------------------------- оценка и вкладки


def test_verdict_overrides_tab_and_marks_seen(session):
    hotel = add(session, "1", "Поставка постельного белья", "ООО «Гостиница Москва»")
    unknown = add(session, "2", "Поставка полотенец", "ООО «Альфа»")
    assert (hotel.relevance, unknown.relevance) == ("relevant", "review")

    set_verdict(session, hotel.id, "no")
    set_verdict(session, unknown.id, "yes")
    assert not hotel.is_new and not unknown.is_new
    assert titles(session, tab="relevant") == {"Поставка полотенец"}
    assert titles(session, tab="rejected") == {"Поставка постельного белья"}
    assert titles(session, tab="all", verdict="mismatch") == {
        "Поставка постельного белья",
        "Поставка полотенец",
    }
    counts = tab_counts(session)
    assert (counts["relevant"], counts["review"], counts["rejected"], counts["all"]) == (1, 0, 1, 2)

    set_verdict(session, hotel.id, None)  # снять оценку — снова по автоматике
    assert hotel.verdict is None and "Поставка постельного белья" in titles(session, tab="relevant")
    with pytest.raises(ValueError):
        set_verdict(session, hotel.id, "maybe")


def test_delete_hides_tender_and_it_does_not_come_back(session):
    t = add(session, "1", "Поставка постельного белья", "ООО «Гостиница Москва»")
    assert delete_tenders(session, [t.id]) == 1
    assert titles(session, tab="all") == set()
    assert titles(session, tab="trash") == {"Поставка постельного белья"}
    assert tab_counts(session)["trash"] == 1 and tab_counts(session)["new"] == 0

    again = add(session, "1", "Поставка постельного белья", "ООО «Гостиница Москва»")  # следующий прогон
    assert again.id == t.id and again.deleted_at is not None and not again.is_new
    assert titles(session, tab="new") == set()

    assert restore_tenders(session, [t.id]) == 1
    assert titles(session, tab="relevant") == {"Поставка постельного белья"}


def test_bulk_ids_by_filter(session):
    add(session, "1", "Поставка постельного белья", "ООО «Гостиница Москва»")
    add(session, "2", "Поставка полотенец", "ООО «Альфа»")
    ids = filtered_ids(session, TenderFilters(tab="review"))
    assert len(ids) == 1
    delete_tenders(session, ids)
    assert titles(session, tab="all") == {"Поставка постельного белья"}


# --------------------------------------------------------------------------- правка файла ключей


def test_editor_operations_roundtrip(keywords_file):
    content = keywords_file.read_bytes()
    new = apply_edits(
        content,
        [
            Edit(ADD_MINUS, mask="кадет* корпус*", phrase="кадетский корпус", value="Сильный"),
            Edit(ADD_MARKER, mask="гостиничн* хозяйств*", phrase="гостиничное хозяйство", value="2"),
            Edit(REMOVE, "marker", "хостел*"),
            Edit(MINUS_STRENGTH, "minus", "аренд* | прокат*", value="Средний"),
            Edit(PRODUCT_TYPE, "product", "топпер*", value="Самодостаточный"),
        ],
    )
    before, after = load_keyword_set(io.BytesIO(content)), load_keyword_set(io.BytesIO(new))
    assert not after.errors
    assert len(after.minus_words) == len(before.minus_words) + 1
    added = after.minus_words[-1]
    assert (added.word, added.strength, added.group) == (
        "кадетский корпус",
        Strength.STRONG,
        "Из проверки тендеров",
    )
    assert any(m.marker == "гостиничное хозяйство" and m.priority == 2 for m in after.markers)
    assert not any(m.mask.source == "хостел*" for m in after.markers)
    assert (
        next(n for n in after.minus_words if n.mask.source == "аренд* | прокат*").strength == Strength.MEDIUM
    )
    assert next(p for p in after.products if p.mask.source == "топпер*").type == KeyType.SELF


def test_editor_errors(keywords_file):
    content = keywords_file.read_bytes()
    with pytest.raises(EditError, match="уже есть"):
        apply_edits(content, [Edit(ADD_MARKER, mask="отел*", value="1")])
    with pytest.raises(EditError, match="не найдено"):
        apply_edits(content, [Edit(REMOVE, "marker", "нет-такого*")])
    with pytest.raises(EditError):
        apply_edits(content, [Edit(MINUS_STRENGTH, "minus", "аренд* | прокат*", value="Слабый")])


def test_suggest_mask():
    assert suggest_mask("Общежитие") == "общежит*"
    assert suggest_mask("ГБУ") == "гбу"
    assert suggest_mask("кадетский корпус") == "кадетск* корпус*"


# --------------------------------------------------------------------------- разбор ошибок


def test_report_finds_wrong_marker_and_wrong_minus(session):
    # «апарт*» — маркер; «Апартаменты для персонала» МВД — не отель
    fp = add(session, "1", "Поставка постельного белья", "ООО «Апартаменты Северные»")
    ok = add(session, "2", "Поставка полотенец", "ООО «Гостиница Москва»")
    missed = add(session, "3", "Поставка постельного белья для отеля, управление номерами", "ООО «Ромашка»")
    assert fp.relevance == "relevant"
    assert missed.relevance == "review"  # среднее минус-слово «управлени*» при маркере
    set_verdict(session, fp.id, "no")
    set_verdict(session, ok.id, "yes")
    set_verdict(session, missed.id, "yes")
    _, ks = get_active_keyword_set(session)

    report = build_report(session, ks)
    assert (report.rated, report.yes, report.no) == (3, 2, 1)
    assert [t.id for t in report.false_positive] == [fp.id]
    assert [t.id for t in report.missed] == [missed.id]
    wrong = {(s.kind, s.mask): s for s in report.suspicious}
    marker = wrong[("marker", "апарт*")]
    assert marker.wrong == [fp.id] and marker.fixes[0][0].action == REMOVE
    minus = wrong[("minus", "администрац* | министерств* | управлени* | департамент*")]
    assert minus.wrong == [missed.id]
    assert {e.action for e, _ in minus.fixes} == {REMOVE}  # среднее — только удалить
    assert ("marker", "гостиниц* | гостиничн*") not in wrong  # верный маркер не подозрительный

    fixes = tender_fixes(fp, ks)
    assert [(e.action, e.mask) for e, _ in fixes] == [(REMOVE, "апарт*")]
    assert tender_fixes(ok, ks) == []  # оценка совпала — править нечего


def test_simulate_shows_fixed_errors(session, keywords_file):
    fp = add(session, "1", "Поставка постельного белья", "ООО «Апартаменты Северные»")
    add(session, "2", "Поставка полотенец", "ООО «Гостиница Москва»")
    set_verdict(session, fp.id, "no")
    new = apply_edits(keywords_file.read_bytes(), [Edit(REMOVE, "marker", "апарт*")])
    impact = simulate(session, load_keyword_set(io.BytesIO(new)))
    assert impact.total == 2
    assert (impact.errors_before, impact.errors_after) == (1, 0)
    assert [t.id for t in impact.fixed] == [fp.id] and not impact.broken
    assert impact.transitions[("relevant", "review")] == 1
    assert session.scalar(select(Tender).where(Tender.id == fp.id)).relevance == "relevant"  # БД не тронута


def test_is_error():
    assert is_error("yes", "review") and is_error("yes", "rejected") and not is_error("yes", "relevant")
    assert is_error("no", "relevant") and not is_error("no", "review") and not is_error(None, "relevant")


def test_report_word_candidates_need_two_tenders(session):
    for i, cust in enumerate(["ООО «Пионер-Сервис»", "АНО «Пионер Плюс»", "ООО «Альфа»"], 1):
        t = add(session, str(i), "Поставка постельного белья для гостиницы", cust)
        set_verdict(session, t.id, "no")
    _, ks = get_active_keyword_set(session)
    words = {c.word: c for c in build_report(session, ks).minus_candidates}
    assert words["пионер"].count == 2 and words["пионер"].mask == "пионер*"
    assert "альфа" not in words  # встретилось один раз
