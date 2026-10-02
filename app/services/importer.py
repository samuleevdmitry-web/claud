"""Приём тендеров, собранных вне приложения (Claude in Chrome, вручную).

Импорт записывается в журнал прогонов как отдельный прогон с trigger="import", поэтому площадка,
проверенная через Claude in Chrome, считается проверенной и не попадает в предупреждения.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.keywords.loader import KeywordSet
from app.models import Notification, Run, RunSourceStat, utcnow
from app.services.ingest import ingest
from app.services.runner import get_source_setting
from app.sources.base import Position, TenderDetails
from app.sources.registry import get_source
from app.sources.util import MSK, is_future, parse_dt


class ImportPosition(BaseModel):
    name: str
    qty: float | None = None
    unit: str | None = None
    code: str | None = None
    price: float | None = None


class ImportTender(BaseModel):
    external_id: str = Field(min_length=1, description="номер/идентификатор процедуры на площадке")
    url: str = ""
    title: str = Field(min_length=1)
    customer_name: str = ""
    customer_inn: str | None = None
    eis_number: str | None = None
    region: str | None = None
    delivery_place: str = ""
    law: str = "коммерческая"
    procedure_type: str | None = None
    nmck: Decimal | None = None
    currency: str = "RUB"
    published_at: str | None = None
    application_deadline: str | None = None
    status: str | None = None
    okpd2_codes: list[str] = Field(default_factory=list)
    positions: list[ImportPosition] = Field(default_factory=list)
    notice_text: str = ""

    @field_validator("external_id", "eis_number", mode="before")
    @classmethod
    def _to_str(cls, v):
        return None if v is None else str(v).strip()

    @field_validator("nmck", mode="before")
    @classmethod
    def _money(cls, v):
        if isinstance(v, str):
            v = v.replace(" ", "").replace(" ", "").replace(",", ".").removesuffix("₽").strip() or None
        return v


class ImportPayload(BaseModel):
    source: str
    tenders: list[ImportTender] = Field(default_factory=list)
    searched_queries: list[str] = Field(default_factory=list)
    # true — площадка проверена по всем запросам (тогда она считается успешно проверенной).
    complete: bool = True
    errors: list[str] = Field(default_factory=list)
    collector: str = "Claude in Chrome"


def to_details(source: str, t: ImportTender) -> TenderDetails:
    deadline = parse_dt(t.application_deadline)
    return TenderDetails(
        source_code=source,
        external_id=t.external_id,
        url=t.url,
        title=t.title.strip(),
        eis_number=t.eis_number or None,
        customer_name=t.customer_name.strip(),
        customer_inn=t.customer_inn,
        region=t.region,
        delivery_place=t.delivery_place,
        law=t.law or "коммерческая",
        procedure_type=t.procedure_type,
        nmck=t.nmck,
        currency=t.currency or "RUB",
        published_at=parse_dt(t.published_at),
        application_deadline=deadline,
        status=t.status,
        is_open=is_future(deadline),
        okpd2_codes=t.okpd2_codes,
        positions=[Position(p.name, p.qty, p.unit, p.code, p.price) for p in t.positions],
        notice_text=t.notice_text,
        revision="|".join(str(x or "") for x in (t.status, t.application_deadline, t.nmck)),
    )


def import_payload(
    session: Session, payload: ImportPayload, ks: KeywordSet, keyword_set_id: int
) -> RunSourceStat:
    info = get_source(payload.source)
    now = utcnow()
    run = Run(trigger="import", status="running", started_at=now)
    session.add(run)
    session.flush()
    stat = RunSourceStat(
        run_id=run.id,
        source_code=info.code,
        status="running",
        started_at=now,
        queries_total=len(payload.searched_queries),
        queries_done=len(payload.searched_queries),
        found=len(payload.tenders),
        errors=list(payload.errors),
    )
    session.add(stat)
    new = relevant = review = updated = discarded = 0
    for t in payload.tenders:
        res = ingest(session, to_details(info.code, t), ks, keyword_set_id)
        if res.outcome == "new":
            new += 1
            relevant += res.tender.relevance == "relevant"
            review += res.tender.relevance == "review"
        elif res.outcome == "updated":
            updated += 1
        elif res.outcome == "discarded":
            discarded += 1
    stat.new, stat.updated = new, updated
    stat.status = "ok" if payload.complete else "failed"
    if not payload.complete:
        stat.errors = [*stat.errors, "площадка проверена не полностью"]
    stat.finished_at = utcnow()
    run.status = "success" if stat.status == "ok" else "partial"
    run.finished_at = utcnow()
    run.new_count, run.new_relevant, run.new_review, run.updated_count = new, relevant, review, updated

    setting = get_source_setting(session, info.code)
    setting.last_status = stat.status
    setting.last_error = "\n".join(stat.errors[-5:]) or None
    if stat.status == "ok":
        setting.last_success_at = now
        if payload.tenders:
            setting.last_nonzero_at = now
    when = now.astimezone(MSK).strftime("%d.%m %H:%M")
    session.add(
        Notification(
            kind="run_summary",
            level="info" if stat.status == "ok" else "warning",
            run_id=run.id,
            title=(
                f"Импорт {info.title} ({payload.collector}) {when}: найдено {new} новых "
                f"({relevant} релевантных, {review} на проверку), обновлено {updated}"
            ),
            body=f"Получено записей: {len(payload.tenders)}, без товарных ключей отброшено: {discarded}.",
        )
    )
    session.flush()
    return stat
