"""Прогон по площадкам: поиск → детали → классификация → сохранение, журнал и сигнализация."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.keywords.loader import KeywordSet
from app.keywords.queries import build_queries, search_codes
from app.models import Notification, Run, RunSourceStat, SourceSetting, Tender, TenderSource, utcnow
from app.services.ingest import ingest
from app.services.keywords import get_active_keyword_set
from app.services.notify import send_external
from app.sources.base import SourceAdapter, SourceError, TenderStub
from app.sources.registry import SourceInfo, adapter_sources, get_source
from app.sources.util import MSK, aware

log = logging.getLogger(__name__)

STALE_RUN = timedelta(hours=6)


class RunAlreadyActive(RuntimeError):
    pass


@dataclass
class SourceRunResult:
    code: str
    status: str
    found: int = 0
    new: int = 0
    new_relevant: int = 0
    new_review: int = 0
    updated: int = 0
    errors: list[str] = field(default_factory=list)
    details_attempted: int = 0
    details_failed: int = 0


def get_source_setting(session: Session, code: str) -> SourceSetting:
    setting = session.get(SourceSetting, code)
    if setting is None:
        setting = SourceSetting(code=code, enabled=True, options={})
        session.add(setting)
        session.flush()
    return setting


def search_window(setting: SourceSetting, today: date) -> tuple[date, date, bool]:
    """Окно поиска: от последнего успешного прогона минус перекрытие; первый запуск — история."""
    s = get_settings()
    if setting.last_success_at is None:
        return today - timedelta(days=s.initial_history_days), today, True
    last = aware(setting.last_success_at).astimezone(MSK).date()
    return min(last - timedelta(days=s.search_overlap_days), today), today, False


async def run_source(
    factory: sessionmaker[Session],
    info: SourceInfo,
    adapter: SourceAdapter,
    ks: KeywordSet,
    keyword_set_id: int,
    run_id: int,
    *,
    max_queries: int | None = None,
    queries: list[str] | None = None,
    today: date | None = None,
) -> SourceRunResult:
    today = today or utcnow().astimezone(MSK).date()
    started = time.monotonic()
    if queries is None:
        queries = [q.text for q in build_queries(ks)]
        if getattr(adapter, "supports_codes", False):
            queries += [f"code:{c}" for c in search_codes(ks)]
    if max_queries:
        queries = queries[:max_queries]

    with factory() as session:
        setting = get_source_setting(session, info.code)
        window_from, window_to, first_run = search_window(setting, today)
        stat = RunSourceStat(
            run_id=run_id,
            source_code=info.code,
            status="running",
            started_at=utcnow(),
            window_from=window_from,
            window_to=window_to,
            queries_total=len(queries),
        )
        session.add(stat)
        session.commit()
        stat_id = stat.id
        had_results_before = setting.last_nonzero_at is not None

    result = SourceRunResult(code=info.code, status="running")
    stubs: dict[str, TenderStub] = {}
    failed_queries = 0
    try:
        for i, query in enumerate(queries, 1):
            fresh: list[TenderStub] = []
            try:
                async for stub in adapter.search(query, window_from, window_to):
                    if first_run and stub.application_deadline and stub.application_deadline < utcnow():
                        continue  # историческая выгрузка — только с открытым приёмом заявок
                    if stub.external_id not in stubs:
                        stubs[stub.external_id] = stub
                        fresh.append(stub)
            except SourceError as exc:
                if exc.status in ("blocked", "captcha"):
                    raise
                failed_queries += 1
                result.errors.append(f"запрос «{query}»: {exc}")
            result.found = len(stubs)
            _update_stat(factory, stat_id, queries_done=i, found=len(stubs), errors=result.errors)
            # Карточки — сразу после запроса: прерванный прогон не теряет найденное.
            await _process_stubs(factory, adapter, fresh, ks, keyword_set_id, result, stat_id)
        if queries and failed_queries == len(queries):
            raise SourceError("все поисковые запросы завершились ошибкой")

        await _refresh_open(factory, adapter, info.code, set(stubs), ks, keyword_set_id, result, stat_id)
        if result.details_attempted and result.details_failed == result.details_attempted:
            raise SourceError("ни одну карточку не удалось получить или разобрать")

        if result.found == 0 and had_results_before:
            result.status = "empty"
            result.errors.append("0 результатов по всем запросам, хотя раньше результаты были")
        else:
            result.status = "ok"
    except SourceError as exc:
        result.status = exc.status
        result.errors.append(str(exc))
        log.warning("%s: %s", info.code, exc)
    except Exception as exc:  # noqa: BLE001 — сбой одного адаптера не роняет прогон
        result.status = "failed"
        result.errors.append(f"внутренняя ошибка: {exc!r}")
        log.exception("%s: сбой адаптера", info.code)

    with factory() as session:
        stat = session.get(RunSourceStat, stat_id)
        stat.status = result.status
        stat.finished_at = utcnow()
        stat.duration_sec = round(time.monotonic() - started, 1)
        stat.found, stat.new, stat.updated = result.found, result.new, result.updated
        stat.errors = result.errors[-50:]
        setting = get_source_setting(session, info.code)
        setting.last_status = result.status
        setting.last_error = "\n".join(result.errors[-5:]) or None
        if result.status == "ok":
            setting.last_success_at = utcnow()
            if result.found:
                setting.last_nonzero_at = utcnow()
        session.commit()
    return result


def _update_stat(factory, stat_id: int, **values) -> None:
    with factory() as session:
        stat = session.get(RunSourceStat, stat_id)
        for key, value in values.items():
            setattr(stat, key, list(value)[-50:] if isinstance(value, list) else value)
        session.commit()


async def _process_stubs(factory, adapter, stubs, ks, keyword_set_id, result, stat_id) -> None:
    for stub in stubs:
        with factory() as session:
            link = session.scalar(
                select(TenderSource).where(
                    TenderSource.source_code == stub.source_code, TenderSource.external_id == stub.external_id
                )
            )
            if link is not None and stub.revision and link.revision == stub.revision:
                link.last_seen_at = utcnow()
                link.tender.last_seen_at = utcnow()
                session.commit()
                continue
        await _fetch_and_ingest(factory, adapter, stub, ks, keyword_set_id, result)
        _update_stat(factory, stat_id, new=result.new, updated=result.updated, errors=result.errors)


async def _refresh_open(factory, adapter, code, seen: set[str], ks, keyword_set_id, result, stat_id) -> None:
    """Обновляет ранее найденные тендеры с открытым приёмом заявок, не попавшие в поиск."""
    with factory() as session:
        rows = session.execute(
            select(TenderSource, Tender)
            .join(Tender, Tender.id == TenderSource.tender_id)
            .where(TenderSource.source_code == code)
            .where((Tender.application_deadline.is_(None)) | (Tender.application_deadline > utcnow()))
        ).all()
        stubs = [
            TenderStub(
                source_code=code,
                external_id=link.external_id,
                url=link.url,
                title=t.title,
                eis_number=t.eis_number,
            )
            for link, t in rows
            if link.external_id not in seen
        ]
    for stub in stubs:
        await _fetch_and_ingest(factory, adapter, stub, ks, keyword_set_id, result)
    if stubs:
        _update_stat(factory, stat_id, new=result.new, updated=result.updated, errors=result.errors)


async def _fetch_and_ingest(factory, adapter, stub, ks, keyword_set_id, result) -> None:
    result.details_attempted += 1
    try:
        details = await adapter.fetch_details(stub)
    except SourceError as exc:
        if exc.status in ("blocked", "captcha"):
            raise
        result.details_failed += 1
        result.errors.append(f"карточка {stub.external_id}: {exc}")
        return
    except Exception as exc:  # noqa: BLE001 — неожиданная вёрстка одной карточки не роняет площадку
        log.exception("%s: карточка %s", stub.source_code, stub.external_id)
        result.details_failed += 1
        result.errors.append(f"карточка {stub.external_id}: {exc!r}")
        return
    with factory() as session:
        outcome = ingest(session, details, ks, keyword_set_id)
        if outcome.outcome == "new":
            result.new += 1
            if outcome.tender.relevance == "relevant":
                result.new_relevant += 1
            elif outcome.tender.relevance == "review":
                result.new_review += 1
        elif outcome.outcome == "updated":
            result.updated += 1
        session.commit()


# --------------------------------------------------------------------------- прогон целиком


def start_run(factory: sessionmaker[Session], trigger: str) -> int:
    """Создаёт запись прогона; защищает от двух одновременных прогонов."""
    with factory() as session:
        active = session.scalar(select(Run).where(Run.status == "running").order_by(Run.id.desc()))
        if active is not None:
            if utcnow() - aware(active.started_at) < STALE_RUN:
                raise RunAlreadyActive(f"прогон №{active.id} ещё идёт")
            active.status = "failed"
            active.error = "прогон прерван (истёк срок)"
            active.finished_at = utcnow()
        run = Run(trigger=trigger, status="running", started_at=utcnow())
        session.add(run)
        session.commit()
        return run.id


async def run_all(
    factory: sessionmaker[Session],
    trigger: str = "schedule",
    only: list[str] | None = None,
    max_queries: int | None = None,
    adapters: dict[str, SourceAdapter] | None = None,
    queries: list[str] | None = None,
) -> int:
    run_id = start_run(factory, trigger)
    with factory() as session:
        active = get_active_keyword_set(session)
        disabled = {s.code for s in session.scalars(select(SourceSetting)) if not s.enabled}
    if active is None:
        _finish_run(factory, run_id, [], error="нет активной версии ключевых слов")
        return run_id
    version, ks = active

    infos = [s for s in adapter_sources() if s.code not in disabled and (only is None or s.code in only)]
    if adapters:
        infos = [get_source(code) for code in adapters if only is None or code in only]

    async def one(info: SourceInfo) -> SourceRunResult:
        adapter = (adapters or {}).get(info.code) or info.factory()
        try:
            return await run_source(
                factory, info, adapter, ks, version.id, run_id, max_queries=max_queries, queries=queries
            )
        finally:
            close = getattr(adapter, "aclose", None)
            if close and not adapters:
                await close()

    results = await asyncio.gather(*(one(i) for i in infos))
    _finish_run(factory, run_id, list(results))
    return run_id


def _finish_run(factory, run_id: int, results: list[SourceRunResult], error: str | None = None) -> None:
    with factory() as session:
        run = session.get(Run, run_id)
        run.finished_at = utcnow()
        run.new_count = sum(r.new for r in results)
        run.new_relevant = sum(r.new_relevant for r in results)
        run.new_review = sum(r.new_review for r in results)
        run.updated_count = sum(r.updated for r in results)
        bad = [r for r in results if r.status != "ok"]
        run.status = (
            "failed" if error or (results and len(bad) == len(results)) else ("partial" if bad else "success")
        )
        run.error = error
        when = aware(run.started_at).astimezone(MSK).strftime("%d.%m %H:%M")
        body = (
            f"Прогон {when}: найдено {run.new_count} новых ({run.new_relevant} релевантных, "
            f"{run.new_review} на проверку), обновлено {run.updated_count}"
        )
        session.add(
            Notification(
                kind="run_summary",
                level="warning" if bad or error else "info",
                title=body,
                body=error or "",
                run_id=run_id,
            )
        )
        messages = [(body, error or "")]
        for r in bad:
            title = f"Площадка {get_source(r.code).title} не проверена ({_status_label(r.status)})"
            details = "\n".join(r.errors[-5:])
            session.add(
                Notification(kind="source_failure", level="error", run_id=run_id, title=title, body=details)
            )
            messages.append((title, details))
        session.commit()
    if run.new_relevant or run.new_review or bad or error:
        send_external(messages)


def _status_label(status: str) -> str:
    return {
        "failed": "ошибка",
        "blocked": "доступ заблокирован",
        "captcha": "капча",
        "empty": "0 результатов",
        "skipped": "не настроена",
    }.get(status, status)
