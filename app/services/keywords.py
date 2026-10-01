"""Версии файла ключевых слов: загрузка, активация, пересчёт релевантности."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.classifier import Classification, PositionText, Relevance, TenderText, classify
from app.config import get_settings
from app.keywords.loader import KeywordSet, load_keyword_set
from app.keywords.masks import parse_mask
from app.models import KeywordSetVersion, Notification, Tender, utcnow

log = logging.getLogger(__name__)

_cache: dict[int, KeywordSet] = {}


class KeywordFileRejected(ValueError):
    def __init__(self, message: str, keyword_set: KeywordSet):
        super().__init__(message)
        self.keyword_set = keyword_set


def parse_keyword_file(content: bytes) -> KeywordSet:
    ks = load_keyword_set(content)
    if not get_settings().mask_fleeting_vowels:
        for key in [*ks.products, *ks.markers, *ks.minus_words]:
            key.mask = parse_mask(key.mask.source, fleeting_vowels=False)
    return ks


def get_active_version(session: Session) -> KeywordSetVersion | None:
    return session.scalars(
        select(KeywordSetVersion)
        .where(KeywordSetVersion.is_active.is_(True))
        .order_by(KeywordSetVersion.id.desc())
    ).first()


def get_active_keyword_set(session: Session) -> tuple[KeywordSetVersion, KeywordSet] | None:
    version = get_active_version(session)
    if version is None:
        return None
    if version.id not in _cache:
        _cache[version.id] = parse_keyword_file(version.content)
    return version, _cache[version.id]


def activate_keyword_file(
    session: Session,
    content: bytes,
    filename: str,
    uploaded_by: str = "",
    allow_errors: bool = False,
    reclassify: bool = True,
) -> KeywordSetVersion:
    """Сохраняет новую версию файла, делает её активной и пересчитывает все тендеры.

    Файл без обязательных листов или без товарных ключей не принимается. Файл с ошибками в
    отдельных масках принимается только с allow_errors=True: такие ключи не будут работать.
    """
    ks = parse_keyword_file(content)
    if not ks.is_usable:
        raise KeywordFileRejected("файл не подходит: нет обязательных листов или товарных ключей", ks)
    if ks.errors and not allow_errors:
        raise KeywordFileRejected(f"в файле {len(ks.errors)} ошибок разбора масок", ks)

    session.execute(update(KeywordSetVersion).values(is_active=False))
    version = KeywordSetVersion(
        filename=filename,
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        uploaded_at=utcnow(),
        uploaded_by=uploaded_by,
        is_active=True,
        summary=ks.summary(),
        issues=[
            {"level": i.level, "sheet": i.sheet, "row": i.row, "message": i.message, "mask": i.mask}
            for i in ks.issues
        ],
    )
    session.add(version)
    session.flush()
    _cache[version.id] = ks
    session.add(
        Notification(
            kind="keywords",
            level="warning" if ks.errors else "info",
            title=f"Загружена новая версия ключевых слов: {filename}",
            body=_summary_text(ks),
        )
    )
    if reclassify:
        stats = reclassify_all(session, version.id, ks)
        log.info("Пересчитано тендеров: %s", stats)
    return version


def bootstrap_keywords(session: Session, path: Path | None = None) -> KeywordSetVersion | None:
    """Импортирует файл из корня проекта, если в БД ещё нет ни одной версии."""
    if session.scalar(select(KeywordSetVersion.id).limit(1)) is not None:
        return None
    path = path or get_settings().keywords_file
    if not path.exists():
        log.warning("Файл ключевых слов %s не найден — классификация недоступна", path)
        return None
    return activate_keyword_file(
        session, path.read_bytes(), path.name, uploaded_by="первый запуск", allow_errors=True
    )


def tender_to_text(tender: Tender) -> TenderText:
    positions = [
        PositionText(name=str(p.get("name") or ""), code=p.get("code") or None)
        for p in (tender.positions or [])
        if isinstance(p, dict)
    ]
    return TenderText(
        title=tender.title or "",
        customer_name=tender.customer_name or "",
        delivery_place=tender.delivery_place or "",
        positions=positions,
        codes=list(tender.okpd2_codes or []),
        notice_text=tender.notice_text or "",
    )


def apply_classification(tender: Tender, result: Classification, keyword_set_id: int) -> None:
    # Тендер без единого ключа после смены файла не удаляем: он уходит в «Отклонённые».
    tender.relevance = str(result.relevance or Relevance.REJECTED)
    tender.score = result.score
    tender.matches = [m.to_dict() for m in result.matches]
    tender.categories = result.categories
    tender.classification_reasons = result.reasons
    tender.keyword_set_id = keyword_set_id


@dataclass
class ReclassifyStats:
    total: int = 0
    relevant: int = 0
    review: int = 0
    rejected: int = 0
    changed: int = 0


def reclassify_all(
    session: Session, keyword_set_id: int, ks: KeywordSet, batch: int = 200
) -> ReclassifyStats:
    stats = ReclassifyStats()
    last_id = 0
    while True:
        tenders = session.scalars(
            select(Tender).where(Tender.id > last_id).order_by(Tender.id).limit(batch)
        ).all()
        if not tenders:
            break
        for tender in tenders:
            before = tender.relevance
            apply_classification(tender, classify(ks, tender_to_text(tender)), keyword_set_id)
            stats.total += 1
            stats.changed += before != tender.relevance
            setattr(stats, tender.relevance, getattr(stats, tender.relevance) + 1)
            last_id = tender.id
        session.flush()
    return stats


def _summary_text(ks: KeywordSet) -> str:
    s = ks.summary()
    text = (
        f"Товарных ключей: {s['products']}, маркеров: {s['markers']}, минус-слов: {s['minus_words']}, "
        f"кодов: {s['codes']} (исключений: {s['excluded_codes']}), готовых запросов: {s['queries']}."
    )
    if s["errors"]:
        text += f" Ошибок разбора: {s['errors']} — эти ключи не работают."
    return text
