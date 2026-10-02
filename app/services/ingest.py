"""Сохранение найденных тендеров: дедупликация, классификация, флаги «новый» и «обновлён»."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.classifier import Classification, PositionText, TenderText, classify
from app.keywords.loader import KeywordSet
from app.keywords.text import normalize
from app.models import Tender, TenderSource, utcnow
from app.services.keywords import apply_classification
from app.sources.base import TenderDetails
from app.sources.util import aware

_NON_WORD = re.compile(r"[^0-9a-zа-я]+")


def _norm(text: str | None) -> str:
    return _NON_WORD.sub(" ", normalize(text or "")).strip()


def dedup_key(customer: str | None, title: str | None, published_at: datetime | None) -> str:
    """Ключ «заказчик + название + дата публикации» для тендеров без номера ЕИС."""
    day = published_at.date().isoformat() if published_at else ""
    raw = f"{_norm(customer)}|{_norm(title)}|{day}"
    return hashlib.sha256(raw.encode()).hexdigest()


def details_to_text(d: TenderDetails) -> TenderText:
    return TenderText(
        title=d.title,
        customer_name=d.customer_name,
        delivery_place=d.delivery_place,
        positions=[PositionText(p.name, p.code) for p in d.positions],
        codes=list(d.okpd2_codes),
        notice_text=d.notice_text,
    )


@dataclass
class IngestResult:
    outcome: str  # new / updated / unchanged / discarded
    tender: Tender | None
    classification: Classification


def find_existing(session: Session, d: TenderDetails) -> Tender | None:
    if d.eis_number:
        tender = session.scalar(select(Tender).where(Tender.eis_number == d.eis_number))
        if tender:
            return tender
    link = session.scalar(
        select(TenderSource).where(
            TenderSource.source_code == d.source_code, TenderSource.external_id == d.external_id
        )
    )
    if link:
        return link.tender
    key = dedup_key(d.customer_name, d.title, d.published_at)
    if d.published_at or d.customer_name:
        return session.scalar(select(Tender).where(Tender.dedup_key == key, Tender.eis_number.is_(None)))
    return None


def _apply_fields(tender: Tender, d: TenderDetails) -> None:
    tender.title = d.title or tender.title
    tender.customer_name = d.customer_name or tender.customer_name or ""
    tender.customer_inn = d.customer_inn or tender.customer_inn
    tender.region = d.region or tender.region
    tender.delivery_place = d.delivery_place or tender.delivery_place or ""
    if d.law != "коммерческая" or not tender.law:
        tender.law = d.law
    tender.procedure_type = d.procedure_type or tender.procedure_type
    tender.nmck = d.nmck if d.nmck is not None else tender.nmck
    tender.currency = d.currency or tender.currency
    tender.published_at = d.published_at or tender.published_at
    tender.application_deadline = d.application_deadline or tender.application_deadline
    tender.status = d.status or tender.status
    if d.okpd2_codes:
        tender.okpd2_codes = list(dict.fromkeys(d.okpd2_codes))
    if d.positions:
        tender.positions = [p.to_dict() for p in d.positions]
    if d.notice_text:
        tender.notice_text = d.notice_text
    if d.eis_number and not tender.eis_number:
        tender.eis_number = d.eis_number
    tender.dedup_key = dedup_key(tender.customer_name, tender.title, tender.published_at)


def _changes(tender: Tender, d: TenderDetails, link: TenderSource | None) -> list[str]:
    changes = []
    if (
        d.application_deadline
        and tender.application_deadline
        and d.application_deadline != _aware(tender.application_deadline)
    ):
        changes.append(f"срок подачи: {_fmt(tender.application_deadline)} → {_fmt(d.application_deadline)}")
    if link is not None and link.revision and d.revision and link.revision != d.revision:
        changes.append("извещение изменено на площадке")
    if d.status and tender.status and d.status != tender.status:
        changes.append(f"статус: {tender.status} → {d.status}")
    return changes


def _aware(dt: datetime) -> datetime:
    return aware(dt)


def _fmt(dt: datetime) -> str:
    return _aware(dt).strftime("%d.%m.%Y %H:%M UTC")


def ingest(session: Session, d: TenderDetails, ks: KeywordSet, keyword_set_id: int) -> IngestResult:
    result = classify(ks, details_to_text(d))
    now = utcnow()
    tender = find_existing(session, d)

    if tender is None:
        if result.relevance is None:  # ни одного товарного ключа — не сохраняем
            return IngestResult("discarded", None, result)
        tender = Tender(title=d.title, dedup_key="", first_seen_at=now, last_seen_at=now, is_new=True)
        _apply_fields(tender, d)
        apply_classification(tender, result, keyword_set_id)
        tender.sources.append(
            TenderSource(
                source_code=d.source_code,
                external_id=d.external_id,
                url=d.url,
                revision=d.revision,
                first_seen_at=now,
                last_seen_at=now,
            )
        )
        session.add(tender)
        session.flush()
        return IngestResult("new", tender, result)

    link = next(
        (s for s in tender.sources if s.source_code == d.source_code and s.external_id == d.external_id), None
    )
    changes = _changes(tender, d, link)
    _apply_fields(tender, d)
    apply_classification(tender, classify(ks, _tender_text(tender)), keyword_set_id)
    tender.last_seen_at = now
    if link is None:
        tender.sources.append(
            TenderSource(
                source_code=d.source_code,
                external_id=d.external_id,
                url=d.url,
                revision=d.revision,
                first_seen_at=now,
                last_seen_at=now,
            )
        )
    else:
        link.last_seen_at = now
        link.url = d.url or link.url
        link.revision = d.revision or link.revision
    if changes:
        tender.is_updated = True
        tender.last_changed_at = now
        tender.change_log = [
            *(tender.change_log or []),
            {"at": now.isoformat(), "source": d.source_code, "changes": changes},
        ]
    session.flush()
    return IngestResult("updated" if changes else "unchanged", tender, result)


def _tender_text(tender: Tender) -> TenderText:
    from app.services.keywords import tender_to_text

    return tender_to_text(tender)
