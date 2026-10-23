"""Уведомления. В приложении — всегда (таблица notifications); Telegram и e-mail — если заданы в .env.

Чтобы добавить канал, реализуйте Notifier.send и добавьте его в external_notifiers().
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import Protocol

import httpx

from app.config import get_settings

NEW_TENDERS = "new_tenders"  # вид уведомления, которое видят менеджеры

log = logging.getLogger(__name__)


class Notifier(Protocol):
    name: str

    def send(self, title: str, body: str = "") -> None: ...


class TelegramNotifier:
    name = "telegram"

    def __init__(self, token: str, chat_id: str, transport: httpx.BaseTransport | None = None):
        self.token = token
        self.chat_id = chat_id
        self.transport = transport

    def send(self, title: str, body: str = "") -> None:
        text = f"{title}\n\n{body}".strip()[:4000]
        with httpx.Client(timeout=20, transport=self.transport) as client:
            r = client.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True},
            )
            r.raise_for_status()


class EmailNotifier:
    name = "email"

    def __init__(self, host: str, port: int, user: str, password: str, sender: str, recipients: list[str]):
        self.host, self.port, self.user, self.password = host, port, user, password
        self.sender, self.recipients = sender, recipients

    def send(self, title: str, body: str = "") -> None:
        msg = EmailMessage()
        msg["Subject"] = title[:200]
        msg["From"] = self.sender
        msg["To"] = ", ".join(self.recipients)
        msg.set_content(body or title)
        with smtplib.SMTP(self.host, self.port, timeout=30) as smtp:
            smtp.starttls()
            if self.user:
                smtp.login(self.user, self.password)
            smtp.send_message(msg)


def external_notifiers() -> list[Notifier]:
    s = get_settings()
    out: list[Notifier] = []
    if s.telegram_bot_token and s.telegram_chat_id:
        out.append(TelegramNotifier(s.telegram_bot_token, s.telegram_chat_id))
    if s.smtp_host and s.smtp_to:
        recipients = [x.strip() for x in s.smtp_to.split(",") if x.strip()]
        out.append(
            EmailNotifier(
                s.smtp_host, s.smtp_port, s.smtp_user, s.smtp_password, s.smtp_from or s.smtp_user, recipients
            )
        )
    return out


def send_external(messages: list[tuple[str, str]], notifiers: list[Notifier] | None = None) -> None:
    """Отправляет сообщения во внешние каналы. Сбой канала не мешает работе приложения."""
    notifiers = external_notifiers() if notifiers is None else notifiers
    if not notifiers or not messages:
        return
    title = messages[0][0]
    body = "\n\n".join(f"{t}\n{b}".strip() for t, b in messages[1:]) or messages[0][1]
    for n in notifiers:
        try:
            n.send(title, body)
        except Exception:  # noqa: BLE001
            log.exception("Не удалось отправить уведомление через %s", n.name)


# --------------------------------------------------------------------------- новые тендеры для менеджеров


def _money(value) -> str:
    return f"{float(value):,.0f}".replace(",", " ") + " ₽" if value is not None else "—"


def new_tenders_notification(session, since, run_id: int | None = None):
    """Уведомление «Новые тендеры» со списком найденных с момента since (релевантные и на проверку)."""
    from sqlalchemy import case, select

    from app.models import Notification, Tender
    from app.sources.util import MSK, aware

    tenders = session.scalars(
        select(Tender)
        .where(
            Tender.first_seen_at >= since,
            Tender.deleted_at.is_(None),
            Tender.relevance.in_(["relevant", "review"]),
        )
        .order_by(case((Tender.relevance == "relevant", 0), else_=1), Tender.application_deadline)
    ).all()
    if not tenders:
        return None
    relevant = sum(t.relevance == "relevant" for t in tenders)
    title = f"Новые тендеры: {relevant} релевантных, {len(tenders) - relevant} на проверку"
    from app.services.portal import configured as portal_configured
    from app.services.portal import portal_link

    base = get_settings().app_base_url.rstrip("/")
    lines = []
    for t in tenders[:30]:
        deadline = aware(t.application_deadline)
        when = deadline.astimezone(MSK).strftime("%d.%m %H:%M") if deadline else "—"
        mark = "★" if t.relevance == "relevant" else "?"
        line = f"{mark} {t.title}\n   {t.customer_name or '—'}; срок {when}; НМЦК {_money(t.nmck)}"
        if portal_configured():
            line += f"\n   {portal_link(t.id)}"
        elif base:
            line += f"\n   {base}/tenders/{t.id}"
        lines.append(line)
    if len(tenders) > 30:
        lines.append(f"…и ещё {len(tenders) - 30}")
    note = Notification(kind=NEW_TENDERS, level="info", title=title, body="\n".join(lines), run_id=run_id)
    session.add(note)
    return note


def manager_notifiers(session) -> list[Notifier]:
    """Письма менеджерам (у кого указан e-mail и включены письма) и группа менеджеров в Telegram."""
    from sqlalchemy import select

    from app.models import User

    s = get_settings()
    out: list[Notifier] = []
    if s.telegram_bot_token and s.telegram_managers_chat_id:
        out.append(TelegramNotifier(s.telegram_bot_token, s.telegram_managers_chat_id))
    if s.smtp_host:
        emails = [
            u.email
            for u in session.scalars(
                select(User).where(User.is_active.is_(True), User.notify_email.is_(True))
            )
            if u.email
        ]
        if emails:
            out.append(
                EmailNotifier(
                    s.smtp_host, s.smtp_port, s.smtp_user, s.smtp_password, s.smtp_from or s.smtp_user, emails
                )
            )
    return out


def send_to_managers(session, note) -> None:
    if note is not None:
        send_external([(note.title, note.body)], manager_notifiers(session))
