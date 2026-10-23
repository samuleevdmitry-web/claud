"""Выборки тендеров для интерфейса и экспорта, счётчики и состояние площадок."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import Select, String, case, cast, func, or_, select
from sqlalchemy.orm import Session

from app.models import Notification, SourceSetting, Tender, TenderSource, UserStatus, utcnow
from app.sources.registry import SOURCES, SourceInfo
from app.sources.util import aware

TABS = {
    "new": "Новые",
    "relevant": "Релевантные",
    "review": "На проверку",
    "rejected": "Отклонённые",
    "all": "Все",
    "trash": "Удалённые",
}
VERDICT_YES, VERDICT_NO = "yes", "no"
VERDICT_LABELS = {VERDICT_YES: "подходит", VERDICT_NO: "не подходит"}
VERDICT_FILTERS = {
    "none": "без оценки",
    "yes": "подходит",
    "no": "не подходит",
    "mismatch": "оценка расходится с автоматической",
}
SORTS = {"deadline": "по сроку подачи", "score": "по score", "published": "по дате публикации"}
URGENT_DAYS = 3


@dataclass
class TenderFilters:
    tab: str = "relevant"
    source: str = ""
    law: str = ""
    region: str = ""
    nmck_from: str = ""
    nmck_to: str = ""
    deadline_from: str = ""
    deadline_to: str = ""
    category: str = ""
    user_status: str = ""
    verdict: str = ""
    q: str = ""
    sort: str = "deadline"
    page: int = 1

    def query_string(self, **override) -> str:
        from urllib.parse import urlencode

        values = {k: v for k, v in {**self.__dict__, **override}.items() if v not in ("", None)}
        if values.get("page") == 1:
            values.pop("page")
        return urlencode(values)


def _decimal(value: str) -> Decimal | None:
    try:
        return Decimal(value.replace(" ", "").replace(",", ".")) if value else None
    except InvalidOperation:
        return None


def _date(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def effective_relevance():
    """Оценка пользователя важнее автоматики: «подходит» — в релевантные, «не подходит» — в отклонённые."""
    return case(
        (Tender.verdict == VERDICT_YES, "relevant"),
        (Tender.verdict == VERDICT_NO, "rejected"),
        else_=Tender.relevance,
    )


def mismatch_condition():
    """Автоматика ошиблась: подходящий тендер не попал в релевантные или неподходящий — попал."""
    return or_(
        (Tender.verdict == VERDICT_YES) & (Tender.relevance != "relevant"),
        (Tender.verdict == VERDICT_NO) & (Tender.relevance == "relevant"),
    )


def build_query(f: TenderFilters) -> Select:
    stmt = select(Tender)
    if f.tab == "trash":
        stmt = stmt.where(Tender.deleted_at.is_not(None))
    else:
        stmt = stmt.where(Tender.deleted_at.is_(None))
    if f.tab == "new":
        stmt = stmt.where(Tender.is_new.is_(True), effective_relevance().in_(["relevant", "review"]))
    elif f.tab in ("relevant", "review", "rejected"):
        stmt = stmt.where(effective_relevance() == f.tab)
    if f.verdict == "none":
        stmt = stmt.where(Tender.verdict.is_(None))
    elif f.verdict in (VERDICT_YES, VERDICT_NO):
        stmt = stmt.where(Tender.verdict == f.verdict)
    elif f.verdict == "mismatch":
        stmt = stmt.where(mismatch_condition())
    if f.source:
        stmt = stmt.where(Tender.sources.any(TenderSource.source_code == f.source))
    if f.law:
        stmt = stmt.where(Tender.law == f.law)
    if f.region:
        stmt = stmt.where(func.lower(Tender.region).contains(f.region.lower()))
    if (v := _decimal(f.nmck_from)) is not None:
        stmt = stmt.where(Tender.nmck >= v)
    if (v := _decimal(f.nmck_to)) is not None:
        stmt = stmt.where(Tender.nmck <= v)
    if d := _date(f.deadline_from):
        stmt = stmt.where(Tender.application_deadline >= d)
    if d := _date(f.deadline_to):
        stmt = stmt.where(Tender.application_deadline < d + timedelta(days=1))
    if f.category:
        as_text = cast(Tender.categories, String)
        escaped = json.dumps(f.category)  # записи, сохранённые с экранированием \uXXXX
        stmt = stmt.where(or_(as_text.contains(f'"{f.category}"'), as_text.contains(escaped)))
    if f.user_status:
        stmt = stmt.where(Tender.user_status == f.user_status)
    if f.q:
        like = f"%{f.q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Tender.title).like(like),
                func.lower(Tender.customer_name).like(like),
                Tender.eis_number.like(like),
            )
        )
    now = utcnow()
    if f.sort == "score":
        stmt = stmt.order_by(Tender.score.desc(), Tender.id.desc())
    elif f.sort == "published":
        stmt = stmt.order_by(Tender.published_at.desc().nulls_last(), Tender.id.desc())
    else:
        # Открытые — по ближайшему сроку; без срока — следом; истёкшие — в конце.
        bucket = case(
            (Tender.application_deadline.is_(None), 1),
            (Tender.application_deadline >= now, 0),
            else_=2,
        )
        stmt = stmt.order_by(bucket, Tender.application_deadline.asc(), Tender.score.desc())
    return stmt


@dataclass
class Page:
    items: list[Tender]
    total: int
    page: int
    per_page: int

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.per_page))


def list_tenders(session: Session, f: TenderFilters, per_page: int = 50) -> Page:
    stmt = build_query(f)
    total = session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    page = max(1, f.page)
    items = session.scalars(stmt.limit(per_page).offset((page - 1) * per_page)).all()
    return Page(list(items), total or 0, page, per_page)


def is_urgent(tender: Tender) -> bool:
    deadline = aware(tender.application_deadline)
    return deadline is not None and utcnow() <= deadline < utcnow() + timedelta(days=URGENT_DAYS)


def is_expired(tender: Tender) -> bool:
    deadline = aware(tender.application_deadline)
    return deadline is not None and deadline < utcnow()


def filter_options(session: Session) -> dict:
    laws = [x for x in session.scalars(select(Tender.law).distinct()) if x]
    regions = sorted(x for x in session.scalars(select(Tender.region).distinct()) if x)
    categories: set[str] = set()
    for cats in session.scalars(select(Tender.categories)):
        categories.update(cats or [])
    return {
        "laws": sorted(laws),
        "regions": regions,
        "categories": sorted(categories),
        "user_statuses": UserStatus.ALL,
        "verdicts": VERDICT_FILTERS,
        "sources": SOURCES,
    }


# --------------------------------------------------------------------------- шапка и площадки


@dataclass
class SourceState:
    info: SourceInfo
    setting: SourceSetting | None
    warning: str | None = None
    level: str = "ok"  # ok / warning / error / muted

    @property
    def enabled(self) -> bool:
        return self.setting.enabled if self.setting else True


def source_states(session: Session) -> list[SourceState]:
    settings = {s.code: s for s in session.scalars(select(SourceSetting))}
    now = utcnow()
    states = []
    for info in SOURCES:
        setting = settings.get(info.code)
        state = SourceState(info, setting)
        if info.mode == "planned":
            state.level, state.warning = "muted", "адаптер ещё не подключён"
        elif info.mode == "excluded":
            state.level, state.warning = "muted", "не используется"
        elif setting is not None and not setting.enabled:
            state.level, state.warning = "muted", "выключена в настройках"
        else:
            last = aware(setting.last_success_at) if setting else None
            status = setting.last_status if setting else None
            if status and status not in ("ok",):
                labels = {
                    "failed": "ошибка",
                    "blocked": "доступ заблокирован",
                    "captcha": "капча",
                    "empty": "0 результатов, хотя раньше были",
                    "skipped": "не настроена",
                }
                state.level, state.warning = "error", f"не проверена: {labels.get(status, status)}"
            elif last is None:
                state.level, state.warning = "error", "ещё ни разу не проверена"
            elif now - last > timedelta(days=info.max_age_days):
                state.level, state.warning = "error", f"не проверялась {(now - last).days} дн."
        states.append(state)
    return states


@dataclass
class HeaderInfo:
    new_relevant: int = 0
    new_review: int = 0
    unread: int = 0
    source_alerts: list[SourceState] = field(default_factory=list)
    planned: int = 0
    mismatches: int = 0


def _new_counts(session: Session) -> dict[str, int]:
    eff = effective_relevance()
    return dict(
        session.execute(
            select(eff, func.count())
            .where(Tender.deleted_at.is_(None), Tender.is_new.is_(True), eff.in_(["relevant", "review"]))
            .group_by(eff)
        ).all()
    )


def header_info(session: Session) -> HeaderInfo:
    counts = _new_counts(session)
    unread = session.scalar(select(func.count()).where(Notification.is_read.is_(False))) or 0
    states = source_states(session)
    return HeaderInfo(
        new_relevant=counts.get("relevant", 0),
        new_review=counts.get("review", 0),
        unread=unread,
        source_alerts=[s for s in states if s.level == "error"],
        planned=sum(1 for s in states if s.info.mode == "planned"),
        mismatches=session.scalar(
            select(func.count()).where(Tender.deleted_at.is_(None), mismatch_condition())
        )
        or 0,
    )


def tab_counts(session: Session) -> dict[str, int]:
    eff = effective_relevance()
    rows = dict(
        session.execute(select(eff, func.count()).where(Tender.deleted_at.is_(None)).group_by(eff)).all()
    )
    trash = session.scalar(select(func.count()).where(Tender.deleted_at.is_not(None)))
    return {
        "new": sum(_new_counts(session).values()),
        "relevant": rows.get("relevant", 0),
        "review": rows.get("review", 0),
        "rejected": rows.get("rejected", 0),
        "all": sum(v for k, v in rows.items() if k),
        "trash": trash or 0,
    }


def filtered_ids(session: Session, f: TenderFilters) -> list[int]:
    return list(session.scalars(build_query(f).with_only_columns(Tender.id).order_by(None)))


def set_verdict(session: Session, tender_id: int, verdict: str | None) -> Tender | None:
    """Оценка «подходит/не подходит» (None — снять). Оценённый тендер считается просмотренным."""
    tender = session.get(Tender, tender_id)
    if tender is None:
        return None
    if verdict not in (None, VERDICT_YES, VERDICT_NO):
        raise ValueError(f"неизвестная оценка {verdict!r}")
    tender.verdict = verdict
    tender.verdict_at = utcnow() if verdict else None
    if verdict:
        tender.is_new = False
        tender.is_updated = False
    return tender


def delete_tenders(session: Session, ids: list[int]) -> int:
    """В корзину: тендеры пропадают из списков и не возвращаются при следующих прогонах."""
    now = utcnow()
    count = 0
    for t in session.scalars(select(Tender).where(Tender.id.in_(ids), Tender.deleted_at.is_(None))):
        t.deleted_at = now
        t.is_new = False
        t.is_updated = False
        count += 1
    return count


def restore_tenders(session: Session, ids: list[int]) -> int:
    count = 0
    for t in session.scalars(select(Tender).where(Tender.id.in_(ids), Tender.deleted_at.is_not(None))):
        t.deleted_at = None
        count += 1
    return count


def mark_seen(session: Session, f: TenderFilters | None = None, tender_id: int | None = None) -> int:
    if tender_id is not None:
        tender = session.get(Tender, tender_id)
        if tender is None:
            return 0
        tender.is_new = False
        tender.is_updated = False
        return 1
    ids = session.scalars(
        build_query(f or TenderFilters(tab="new")).with_only_columns(Tender.id).order_by(None)
    )
    count = 0
    for t in session.scalars(select(Tender).where(Tender.id.in_(list(ids)))):
        t.is_new = False
        t.is_updated = False
        count += 1
    return count
