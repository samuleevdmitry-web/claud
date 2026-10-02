"""Сохранение, дедупликация, прогон по площадкам, сигнализация о сбоях, импорт."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.keywords.queries import build_queries, search_codes
from app.models import Notification, Run, RunSourceStat, SourceSetting, Tender, TenderSource
from app.services.importer import ImportPayload, import_payload
from app.services.ingest import dedup_key, ingest
from app.services.keywords import activate_keyword_file
from app.services.runner import RunAlreadyActive, run_all, search_window, start_run
from app.sources.base import Position, SourceBlocked, SourceCaptcha, SourceError, TenderDetails, TenderStub

FUTURE = datetime.now(UTC) + timedelta(days=10)


@pytest.fixture
def factory(keywords_file):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    f = sessionmaker(engine, expire_on_commit=False)
    with f() as s:
        activate_keyword_file(s, keywords_file.read_bytes(), keywords_file.name)
        s.commit()
    return f


@pytest.fixture
def ks_version(factory):
    from app.services.keywords import get_active_keyword_set

    with factory() as s:
        version, ks = get_active_keyword_set(s)
        return version.id, ks


def details(**kw) -> TenderDetails:
    base = dict(
        source_code="onlinecontract",
        external_id="1",
        url="https://x/1",
        title="Поставка полотенец махровых",
        customer_name="Санаторий «Дюны»",
        application_deadline=FUTURE,
        published_at=datetime(2026, 10, 1, tzinfo=UTC),
        revision="r1",
    )
    base.update(kw)
    return TenderDetails(**base)


# --------------------------------------------------------------------------- запросы


def test_queries_from_keyword_file(ks):
    texts = [q.text for q in build_queries(ks)]
    assert "гостиничный текстиль" in texts
    assert "полотенце махровое" in texts
    assert "простыня" in texts and "простынь" in texts
    assert "сатин" not in texts  # уточняющие ключи сами по себе не ищутся
    assert "плед" not in texts  # «Нужен маркер» с приоритетом 3
    assert "мягкий инвентарь санаторий" in texts  # готовый запрос
    assert len(texts) == len(set(texts))
    codes = search_codes(ks)
    assert "13.92.12" in codes and "13.92.12.160" not in codes


# --------------------------------------------------------------------------- сохранение


def test_ingest_new_discard_and_dedup(factory, ks_version):
    vid, ks = ks_version
    with factory() as s:
        assert ingest(s, details(), ks, vid).outcome == "new"
        assert ingest(s, details(title="Поставка бумаги", external_id="2"), ks, vid).outcome == "discarded"
        assert ingest(s, details(), ks, vid).outcome == "unchanged"
        assert s.scalar(select(func.count(Tender.id))) == 1


def test_same_eis_number_from_two_sources_is_one_tender(factory, ks_version):
    vid, ks = ks_version
    with factory() as s:
        ingest(s, details(source_code="eis_gosplan", external_id="fz44:1", eis_number="0123"), ks, vid)
        ingest(
            s, details(source_code="eis", external_id="0123", eis_number="0123", title="Полотенца"), ks, vid
        )
        tender = s.scalar(select(Tender))
        assert s.scalar(select(func.count(Tender.id))) == 1
        assert sorted(src.source_code for src in tender.sources) == ["eis", "eis_gosplan"]


def test_dedup_by_customer_title_date_without_eis_number(factory, ks_version):
    vid, ks = ks_version
    with factory() as s:
        ingest(
            s, details(source_code="bidzaar", external_id="b1", customer_name='ООО "Отель Альфа"'), ks, vid
        )
        ingest(
            s, details(source_code="tektorg", external_id="t1", customer_name="ООО «Отель Альфа»"), ks, vid
        )
        assert s.scalar(select(func.count(Tender.id))) == 1
        assert s.scalar(select(func.count(TenderSource.id))) == 2
    assert dedup_key("ООО «Альфа»", "Полотенца", None) == dedup_key('ооо "альфа"', "ПОЛОТЕНЦА", None)


def test_deadline_change_marks_updated(factory, ks_version):
    vid, ks = ks_version
    with factory() as s:
        t = ingest(s, details(), ks, vid).tender
        res = ingest(s, details(application_deadline=FUTURE + timedelta(days=3), revision="r2"), ks, vid)
        assert res.outcome == "updated"
        assert t.is_updated and t.last_changed_at is not None
        assert any("срок подачи" in c for c in t.change_log[-1]["changes"])
        assert t.is_new  # «новый» остаётся до просмотра


def test_ingest_stores_fields_and_classification(factory, ks_version):
    vid, ks = ks_version
    d = details(
        nmck=Decimal("100.50"),
        positions=[Position("Полотенце 50х90", 10, "шт", "13.92.14.110")],
        okpd2_codes=["13.92.14.110"],
        law="коммерческая",
    )
    with factory() as s:
        t = ingest(s, d, ks, vid).tender
        assert t.relevance == "relevant" and t.score > 0
        assert t.positions[0]["code"] == "13.92.14.110"
        assert t.keyword_set_id == vid
        assert t.sources[0].revision == "r1"


# --------------------------------------------------------------------------- прогон


class FakeAdapter:
    code = "onlinecontract"
    title = "fake"
    supports_codes = False

    def __init__(self, items=None, error=None, detail_error=None):
        self.items = items if items is not None else []
        self.error = error
        self.detail_error = detail_error
        self.searched = []
        self.detailed = []

    async def search(self, query, published_from, published_to):
        self.searched.append((query, published_from))
        if self.error:
            raise self.error
        for d in self.items:
            yield TenderStub(
                d.source_code,
                d.external_id,
                d.url,
                d.title,
                application_deadline=d.application_deadline,
                revision=d.revision,
            )

    async def fetch_details(self, stub):
        self.detailed.append(stub.external_id)
        if self.detail_error:
            raise self.detail_error
        for d in self.items:
            if d.external_id == stub.external_id:
                return d
        raise SourceError("404")

    async def healthcheck(self):
        return None


def run(factory, adapters, **kw):
    return asyncio.run(run_all(factory, trigger="manual", adapters=adapters, queries=["полотенца"], **kw))


def test_run_saves_new_and_repeat_run_has_no_duplicates(factory):
    items = [details(external_id="1"), details(external_id="2", title="Поставка бумаги офисной")]
    adapter = FakeAdapter(items)
    run_id = run(factory, {"onlinecontract": adapter})
    with factory() as s:
        r = s.get(Run, run_id)
        assert r.status == "success" and r.new_count == 1 and r.new_relevant == 1
        stat = r.source_stats[0]
        assert (stat.found, stat.new, stat.status) == (2, 1, "ok")
        assert s.scalar(select(SourceSetting).where(SourceSetting.code == "onlinecontract")).last_success_at
        summary = s.scalar(select(Notification.title).where(Notification.kind == "run_summary"))
        assert "найдено 1 новых (1 релевантных, 0 на проверку)" in summary

    adapter2 = FakeAdapter(items)
    run(factory, {"onlinecontract": adapter2})
    with factory() as s:
        assert s.scalar(select(func.count(Tender.id))) == 1
    # Тендер с той же меткой версии повторно не запрашивается; отброшенный — запрашивается.
    assert adapter2.detailed == ["2"]


def test_second_run_window_overlaps_two_days(factory):
    adapter = FakeAdapter([details()])
    run(factory, {"onlinecontract": adapter})
    adapter2 = FakeAdapter([details()])
    run(factory, {"onlinecontract": adapter2})
    today = datetime.now(UTC).date()
    assert adapter.searched[0][1] == today - timedelta(days=30)
    assert (today - adapter2.searched[0][1]).days in (2, 3)


def test_first_run_skips_closed_tenders(factory):
    closed = details(external_id="old", application_deadline=datetime.now(UTC) - timedelta(days=1))
    adapter = FakeAdapter([closed, details(external_id="open")])
    run(factory, {"onlinecontract": adapter})
    assert adapter.detailed == ["open"]


@pytest.mark.parametrize(
    ("error", "status"), [(SourceCaptcha("капча"), "captcha"), (SourceBlocked("403"), "blocked")]
)
def test_source_failure_is_reported_and_does_not_break_others(factory, error, status):
    good = FakeAdapter([details(source_code="eis_gosplan", external_id="fz44:9", eis_number="9")])
    good.code = "eis_gosplan"
    run_id = run(factory, {"onlinecontract": FakeAdapter(error=error), "eis_gosplan": good})
    with factory() as s:
        r = s.get(Run, run_id)
        statuses = {st.source_code: st.status for st in r.source_stats}
        assert statuses == {"onlinecontract": status, "eis_gosplan": "ok"}
        assert r.status == "partial" and r.new_count == 1
        titles = s.scalars(select(Notification.title).where(Notification.kind == "source_failure")).all()
        label = {"captcha": "капча", "blocked": "доступ заблокирован"}[status]
        assert titles == [f"Площадка Online Contract не проверена ({label})"]
        assert s.get(SourceSetting, "onlinecontract").last_success_at is None


def test_zero_results_after_previous_results_is_flagged(factory):
    run(factory, {"onlinecontract": FakeAdapter([details()])})
    empty = FakeAdapter([])

    async def card(stub):
        return details()

    empty.fetch_details = card  # ранее найденный открытый тендер обновляется по карточке
    run_id = run(factory, {"onlinecontract": empty})
    with factory() as s:
        stat = s.scalar(select(RunSourceStat).where(RunSourceStat.run_id == run_id))
        assert stat.status == "empty"


def test_detail_errors_are_logged_not_fatal(factory):
    items = [details(), details(external_id="2")]
    adapter = FakeAdapter(items)
    original = adapter.fetch_details

    async def flaky(stub):
        if stub.external_id == "1":
            raise ValueError("неожиданная вёрстка")
        return await original(stub)

    adapter.fetch_details = flaky
    run_id = run(factory, {"onlinecontract": adapter})
    with factory() as s:
        stat = s.scalar(select(RunSourceStat).where(RunSourceStat.run_id == run_id))
        assert stat.status == "ok" and stat.new == 1 and any("карточка 1" in e for e in stat.errors)


def test_all_cards_failing_marks_source_failed(factory):
    run_id = run(factory, {"onlinecontract": FakeAdapter([details()], detail_error=SourceError("500"))})
    with factory() as s:
        stat = s.scalar(select(RunSourceStat).where(RunSourceStat.run_id == run_id))
        assert stat.status == "failed"


def test_open_tenders_are_refreshed_even_if_not_found(factory):
    run(factory, {"onlinecontract": FakeAdapter([details()])})
    adapter = FakeAdapter([])
    adapter.items_for_details = [details(application_deadline=FUTURE + timedelta(days=5), revision="r2")]

    async def fetch(stub):
        adapter.detailed.append(stub.external_id)
        return adapter.items_for_details[0]

    adapter.fetch_details = fetch
    run(factory, {"onlinecontract": adapter})
    assert adapter.detailed == ["1"]
    with factory() as s:
        assert s.scalar(select(Tender)).is_updated


def test_parallel_run_is_refused(factory):
    start_run(factory, "manual")
    with pytest.raises(RunAlreadyActive):
        start_run(factory, "schedule")


def test_search_window_first_and_next():
    s = SourceSetting(code="x")
    assert search_window(s, date(2026, 10, 2)) == (date(2026, 9, 2), date(2026, 10, 2), True)
    s.last_success_at = datetime(2026, 9, 30, 3, tzinfo=UTC)
    assert search_window(s, date(2026, 10, 2)) == (date(2026, 9, 28), date(2026, 10, 2), False)


# --------------------------------------------------------------------------- импорт


def test_import_payload(factory, ks_version):
    vid, ks = ks_version
    payload = ImportPayload.model_validate(
        {
            "source": "bidzaar",
            "searched_queries": ["полотенца"],
            "tenders": [
                {
                    "external_id": 777,
                    "title": "Поставка халатов махровых",
                    "customer_name": "Отель «Море»",
                    "nmck": "350 000,50",
                    "application_deadline": FUTURE.isoformat(),
                    "positions": [{"name": "Халат махровый", "qty": 100, "unit": "шт"}],
                },
                {"external_id": "778", "title": "Поставка бумаги"},
            ],
        }
    )
    with factory() as s:
        stat = import_payload(s, payload, ks, vid)
        s.commit()
        assert (stat.new, stat.found, stat.status) == (1, 2, "ok")
        t = s.scalar(select(Tender))
        assert t.relevance == "relevant" and t.nmck == Decimal("350000.50")
        assert t.sources[0].source_code == "bidzaar" and t.sources[0].external_id == "777"
        assert s.get(SourceSetting, "bidzaar").last_success_at is not None
        assert s.scalar(select(Run)).trigger == "import"


def test_incomplete_import_does_not_mark_source_checked(factory, ks_version):
    vid, ks = ks_version
    payload = ImportPayload(source="tektorg", complete=False, errors=["капча на 3-й странице"])
    with factory() as s:
        stat = import_payload(s, payload, ks, vid)
        assert stat.status == "failed"
        assert s.get(SourceSetting, "tektorg").last_success_at is None
