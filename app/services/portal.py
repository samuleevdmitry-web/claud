"""Обмен с сайтом менеджеров на хостинге (папка portal/, PHP + MySQL).

Компьютер — главный: сначала забирает с сайта отметки менеджеров (статус, заметки, «подходит /
не подходит») и применяет их, если они новее своих, затем отправляет снимок: актуальные тендеры
(релевантные, на проверку и оценённые, кроме удалённых и истёкших больше 30 дней назад),
менеджеров (логин, имя, хеш пароля) и последние уведомления «Новые тендеры».
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models import AppSetting, Notification, Tender, User, UserStatus, utcnow
from app.services.notify import NEW_TENDERS
from app.services.tenders import VERDICT_NO, VERDICT_YES, effective_relevance
from app.sources.registry import BY_CODE
from app.sources.util import aware

log = logging.getLogger(__name__)

BATCH = 150
KEEP_EXPIRED_DAYS = 30
_lock = threading.Lock()

K_CURSOR, K_LAST_SYNC, K_LAST_ERROR, K_LAST_STATS = (
    "portal_cursor",
    "portal_last_sync_at",
    "portal_last_error",
    "portal_last_stats",
)


class PortalError(RuntimeError):
    pass


def configured() -> bool:
    s = get_settings()
    return bool(s.portal_url and s.portal_token)


def portal_link(tender_id: int) -> str:
    return f"{get_settings().portal_url.rstrip('/')}/tender.php?id={tender_id}"


def iso(dt: datetime | None) -> str | None:
    dt = aware(dt)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def parse_iso(text: str | None) -> datetime | None:
    if not text:
        return None
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


class PortalClient:
    def __init__(
        self, url: str, token: str, transport: httpx.BaseTransport | None = None, timeout: float = 60
    ):
        self.url = url.rstrip("/") + "/api.php"
        self.token = token
        self._client = httpx.Client(timeout=timeout, transport=transport, follow_redirects=True)

    def close(self) -> None:
        self._client.close()

    def call(self, action: str, **payload) -> dict:
        try:
            r = self._client.post(self.url, json={"token": self.token, "action": action, **payload})
        except httpx.HTTPError as exc:
            raise PortalError(f"сайт недоступен: {exc}") from exc
        try:
            data = r.json()
        except ValueError as exc:
            raise PortalError(f"сайт ответил не JSON (HTTP {r.status_code}): {r.text[:200]}") from exc
        if r.status_code != 200 or "error" in data:
            raise PortalError(f"сайт: {data.get('error') or r.status_code}")
        return data


# --------------------------------------------------------------------------- отметки менеджеров

_FIELDS = {
    "user_status": ("user_status", "user_status_at", "статус"),
    "notes": ("notes", "notes_at", "заметки"),
    "verdict": ("verdict", "verdict_at", "оценка"),
}


def apply_changes(session: Session, changes: list[dict]) -> int:
    """Отметки с сайта, которые новее отметок на компьютере. Возвращает число применённых."""
    applied = 0
    for c in changes:
        spec = _FIELDS.get(c.get("field", ""))
        tender = session.get(Tender, int(c.get("tender_id", 0)))
        if spec is None or tender is None or tender.deleted_at is not None:
            continue
        attr, at_attr, label = spec
        at = parse_iso(c.get("created_at")) or utcnow()
        local_at = aware(getattr(tender, at_attr))
        if local_at is not None and local_at >= at:
            continue  # на компьютере правили позже
        value = c.get("value")
        if attr == "user_status" and value not in UserStatus.ALL:
            continue
        if attr == "verdict":
            value = value if value in (VERDICT_YES, VERDICT_NO) else None
            tender.is_new = False
        if attr == "notes":
            value = (value or "")[:10000]
        setattr(tender, attr, value)
        setattr(tender, at_attr, at)
        shown = (
            {VERDICT_YES: "подходит", VERDICT_NO: "не подходит"}.get(value, value)
            if attr == "verdict"
            else value
        )
        text = "изменены" if attr == "notes" else f"→ {shown or 'снята'}"
        tender.change_log = [
            *(tender.change_log or []),
            {
                "at": utcnow().isoformat(),
                "source": f"сайт: {c.get('login', '')}",
                "changes": [f"{label} {text}"],
            },
        ]
        applied += 1
    return applied


# --------------------------------------------------------------------------- снимок для сайта


def published_tenders(session: Session) -> list[Tender]:
    eff = effective_relevance()
    cutoff = utcnow() - timedelta(days=KEEP_EXPIRED_DAYS)
    return list(
        session.scalars(
            select(Tender)
            .where(Tender.deleted_at.is_(None))
            .where(or_(eff.in_(["relevant", "review"]), Tender.verdict.is_not(None)))
            .where(or_(Tender.application_deadline.is_(None), Tender.application_deadline >= cutoff))
            .order_by(Tender.id)
        )
    )


def tender_payload(t: Tender) -> dict:
    effective = {VERDICT_YES: "relevant", VERDICT_NO: "rejected"}.get(
        t.verdict or "", t.relevance or "review"
    )
    return {
        "id": t.id,
        "relevance": t.relevance or "review",
        "effective": effective,
        "deadline": iso(t.application_deadline),
        "published": iso(t.published_at),
        "first_seen": iso(t.first_seen_at),
        "nmck": float(t.nmck) if t.nmck is not None else None,
        "user_status": t.user_status or UserStatus.NEW,
        "notes": t.notes or "",
        "verdict": t.verdict,
        "status_at": iso(t.user_status_at),
        "notes_at": iso(t.notes_at),
        "verdict_at": iso(t.verdict_at),
        "data": {
            "title": t.title,
            "customer_name": t.customer_name,
            "customer_inn": t.customer_inn,
            "region": t.region,
            "delivery_place": t.delivery_place,
            "law": t.law,
            "procedure_type": t.procedure_type,
            "currency": t.currency,
            "status": t.status,
            "eis_number": t.eis_number,
            "okpd2_codes": list(t.okpd2_codes or []),
            "positions": list(t.positions or []),
            "categories": list(t.categories or []),
            "reasons": list(t.classification_reasons or []),
            "sources": [
                {
                    "title": BY_CODE[s.source_code].title if s.source_code in BY_CODE else s.source_code,
                    "url": s.url,
                }
                for s in t.sources
                if s.url
            ],
        },
    }


# --------------------------------------------------------------------------- синхронизация


@dataclass
class SyncResult:
    pulled: int = 0
    applied: int = 0
    pushed: int = 0
    managers: int = 0
    errors: list[str] = field(default_factory=list)


def _get(session: Session, key: str) -> str | None:
    row = session.get(AppSetting, key)
    return row.value if row else None


def _set(session: Session, key: str, value: str | None) -> None:
    row = session.get(AppSetting, key)
    if value is None:
        if row is not None:
            session.delete(row)
        return
    if row is None:
        session.add(AppSetting(key=key, value=value))
    else:
        row.value = value


def status(session: Session) -> dict:
    return {
        "configured": configured(),
        "url": get_settings().portal_url,
        "last_sync": parse_iso(_get(session, K_LAST_SYNC)),
        "error": _get(session, K_LAST_ERROR),
        "stats": _get(session, K_LAST_STATS),
    }


def sync(factory: sessionmaker[Session], client: PortalClient | None = None) -> SyncResult:
    """Забрать отметки менеджеров и отправить снимок. Ошибка сохраняется и видна администратору."""
    result = SyncResult()
    if not configured() and client is None:
        return result
    if not _lock.acquire(blocking=False):
        result.errors.append("обмен уже идёт")
        return result
    s = get_settings()
    own = client is None
    client = client or PortalClient(s.portal_url, s.portal_token)
    try:
        with factory() as session:
            cursor = int(_get(session, K_CURSOR) or 0)
            while True:
                changes = client.call("changes", after=cursor).get("changes", [])
                result.pulled += len(changes)
                result.applied += apply_changes(session, changes)
                if changes:
                    cursor = max(int(c["id"]) for c in changes)
                    _set(session, K_CURSOR, str(cursor))
                session.commit()
                if len(changes) < 1000:
                    break

            tenders = [tender_payload(t) for t in published_tenders(session)]
            managers = [
                {"login": u.login, "name": u.name, "password_hash": u.password_hash, "active": u.is_active}
                for u in session.scalars(select(User))
            ]
            notes = session.scalars(
                select(Notification)
                .where(Notification.kind == NEW_TENDERS)
                .order_by(Notification.id.desc())
                .limit(50)
            )
            notifications = [
                {"id": n.id, "title": n.title, "body": n.body or "", "created_at": iso(n.created_at)}
                for n in notes
            ]
            batches = [tenders[i : i + BATCH] for i in range(0, len(tenders), BATCH)] or [[]]
            for i, batch in enumerate(batches):
                payload: dict = {"tenders": batch, "final": i == len(batches) - 1}
                if i == 0:
                    payload |= {
                        "all_ids": [t["id"] for t in tenders],
                        "managers": managers,
                        "notifications": notifications,
                        "ack": cursor,
                    }
                client.call("push", **payload)
            result.pushed, result.managers = len(tenders), len(managers)
            _set(session, K_LAST_SYNC, iso(utcnow()))
            _set(session, K_LAST_ERROR, None)
            _set(
                session,
                K_LAST_STATS,
                f"на сайте тендеров: {result.pushed}, менеджеров: {result.managers}; "
                f"отметок с сайта получено: {result.pulled}, применено: {result.applied}",
            )
            session.commit()
    except PortalError as exc:
        result.errors.append(str(exc))
        log.warning("Обмен с сайтом менеджеров: %s", exc)
        with factory() as session:
            _set(session, K_LAST_ERROR, f"{datetime.now(UTC):%d.%m %H:%M} UTC: {exc}")
            session.commit()
    finally:
        if own:
            client.close()
        _lock.release()
    return result


def ping(client: PortalClient | None = None) -> dict:
    s = get_settings()
    own = client is None
    client = client or PortalClient(s.portal_url, s.portal_token)
    try:
        return client.call("ping")
    finally:
        if own:
            client.close()
