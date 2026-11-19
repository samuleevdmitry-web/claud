"""UniSender: рассылки, их статистика и база контактов.

API: https://api.unisender.com/ru/api/<метод>?format=json&api_key=… Ключ — в личном кабинете UniSender:
«Настройки аккаунта» → «Интеграции и API». База выгружается асинхронно: async/exportContacts → task_uuid →
async/getTaskResult, пока статус не «completed», → файл CSV по ссылке file_to_download.
Почта и телефон из выгрузки сразу превращаются в отпечатки (analytics.privacy) и в базу не попадают.

Лимиты UniSender считаются за 60 секунд на ключ или IP (у exportContacts — 20 запросов). Поэтому между запросами
пауза, а на ответ «Api call limit exceeded» клиент ждёт минуту и повторяет, а не роняет загрузку.
"""

from __future__ import annotations

import csv
import io
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from html import unescape

import requests

from analytics import privacy
from analytics.sources.http import send

API = "https://api.unisender.com/ru/api/"
EXPORT_FIELDS = ["email", "email_status", "email_list_ids", "phone"]


class UnisenderError(Exception):
    pass


@dataclass(frozen=True)
class Campaign:
    campaign_id: int
    start_time: datetime | None
    status: str | None
    list_id: int | None
    message_id: int | None
    subject: str | None


@dataclass(frozen=True)
class CampaignStats:
    campaign_id: int
    total: int
    sent: int
    delivered: int
    read_unique: int
    read_all: int
    clicked_unique: int
    clicked_all: int
    unsubscribed: int
    spam: int


@dataclass(frozen=True)
class Delivery:
    """Статус письма рассылки у одного адреса (отчёт о доставке UniSender)."""
    email_hash: str
    status: str
    level: int  # 0 — не доставлено, 1 — доставлено, 2 — открыто, 3 — переход по ссылке
    email: str | None = None  # сам адрес — только если включены списки получателей (EMAIL_LISTS)


@dataclass(frozen=True)
class Link:
    """Ссылка из письма рассылки: по ней видно, про какие товары письмо."""
    url: str
    text: str


@dataclass(frozen=True)
class Contact:
    email_hash: str | None
    phone_hash: str | None
    status: str | None
    list_ids: tuple[int, ...]
    email: str | None = None  # сам адрес — только если включены списки получателей (EMAIL_LISTS)


class UnisenderClient:
    def __init__(self, api_key: str, session: requests.Session | None = None, max_retries: int = 4,
                 backoff: float = 2.0, pause: float = 1.0, limit_wait: float = 65.0, limit_retries: int = 3,
                 log: Callable[[str], None] = print):
        if not api_key:
            raise UnisenderError("Не задан UNISENDER_API_KEY (ключ API UniSender)")
        self.api_key = api_key
        self.session = session or requests.Session()
        self.max_retries, self.backoff, self.pause = max_retries, backoff, pause
        self.limit_wait, self.limit_retries, self.log = limit_wait, limit_retries, log

    def call(self, method: str, **params):
        for attempt in range(self.limit_retries + 1):
            resp = send(self.session, "GET", API + method, max_retries=self.max_retries, backoff=self.backoff,
                        params={"format": "json", "api_key": self.api_key, **params})
            time.sleep(self.pause)
            if resp.status_code != 200:
                raise UnisenderError(f"UniSender {method}: HTTP {resp.status_code}")
            data = resp.json()
            if "error" not in data:
                return data.get("result")
            if rate_limited(data) and attempt < self.limit_retries:
                self.log(f"  UniSender {method}: лимит запросов за минуту исчерпан — жду {self.limit_wait:.0f} с")
                time.sleep(self.limit_wait)  # окно лимита — 60 секунд
                continue
            code = data.get("code") or ""
            hint = " — проверьте ключ API" if "invalid_api_key" in str(code) or "api_key" in str(data["error"]) else ""
            if rate_limited(data):
                hint = " — превышен лимит запросов UniSender, загрузка повторится сама через час"
            raise UnisenderError(f"UniSender {method}: {data['error']}{hint}")

    def lists(self) -> dict[int, str]:
        return {int(x["id"]): x.get("title") or "" for x in self.call("getLists") or []}

    def campaigns(self, since: date, until: date) -> list[Campaign]:
        out, offset = [], 0
        while True:
            page = self.call("getCampaigns", **{"from": f"{since} 00:00:00", "to": f"{until} 23:59:59",
                                                "limit": 1000, "offset": offset}) or []
            out += [parse_campaign(x) for x in page]
            if len(page) < 1000:
                return out
            offset += len(page)

    def stats(self, campaign_id: int) -> CampaignStats:
        return parse_stats(campaign_id, self.call("getCampaignCommonStats", campaign_id=campaign_id) or {})

    def export_contacts(self, wait: float = 15.0, timeout: float = 1800.0, keep_emails: bool = False) -> list[Contact]:
        params = {f"field_names[{i}]": name for i, name in enumerate(EXPORT_FIELDS)}
        result = self._task("async/exportContacts", "выгрузка базы", wait, timeout, **params)
        return parse_contacts(self._download(result.get("file_to_download") or ""), keep_emails)

    def delivery(self, campaign_id: int, wait: float = 15.0, timeout: float = 1800.0,
                 keep_emails: bool = False) -> list[Delivery]:
        """Кому ушло письмо и что с ним сделали: статус по каждому адресу (только за последние 180 дней)."""
        return parse_delivery(self.delivery_table(campaign_id, wait, timeout), keep_emails)

    def delivery_table(self, campaign_id: int, wait: float = 15.0, timeout: float = 1800.0) -> list[dict]:
        result = self._task("async/getCampaignDeliveryStats", f"отчёт о доставке рассылки {campaign_id}", wait, timeout,
                            campaign_id=campaign_id)
        if result.get("file_to_download"):
            return csv_rows(self._download(result["file_to_download"]))
        return table_rows(result.get("result") if isinstance(result.get("result"), dict) else result)

    def message_links(self, message_id: int) -> list[Link]:
        """Ссылки из текста письма (getMessage)."""
        result = self.call("getMessage", id=message_id) or {}
        message = result[0] if isinstance(result, list) and result else result
        return extract_links((message or {}).get("body") or "") if isinstance(message, dict) else []

    def _task(self, method: str, what: str, wait: float, timeout: float, **params) -> dict:
        """Асинхронный метод: запуск задачи и ожидание результата (сначала часто, потом раз в wait секунд)."""
        task = self.call(method, **params) or {}
        uuid = task.get("task_uuid")
        if not uuid:
            raise UnisenderError(f"UniSender не вернул task_uuid ({what})")
        started, delay = time.monotonic(), min(2.0, wait)
        while True:
            result = self.call("async/getTaskResult", task_uuid=uuid) or {}
            if result.get("status") == "completed":
                return result
            if result.get("status") not in ("new", "processing", None) or time.monotonic() - started > timeout:
                raise UnisenderError(f"UniSender: {what} не готов(а), статус {result.get('status')}")
            time.sleep(delay)
            delay = min(delay * 2, wait)

    def _download(self, url: str) -> str:
        if not url:
            raise UnisenderError("UniSender не дал ссылку на файл выгрузки базы")
        resp = send(self.session, "GET", url, max_retries=self.max_retries, backoff=self.backoff)
        if resp.status_code in (401, 403):  # ссылка без ключа — пробуем с ключом
            resp = send(self.session, "GET", url, max_retries=self.max_retries, backoff=self.backoff,
                        params={"api_key": self.api_key})
        if resp.status_code != 200:
            raise UnisenderError(f"Файл выгрузки базы UniSender: HTTP {resp.status_code}")
        raw = getattr(resp, "content", None)
        return raw.decode("utf-8-sig", errors="replace") if isinstance(raw, bytes) else resp.text


def rate_limited(data: dict) -> bool:
    """Ответ «Api call limit exceeded for ip: limit 20 calls 329» — превышен лимит за минуту."""
    text = f"{data.get('code') or ''} {data.get('error') or ''}".lower()
    return "limit exceeded" in text or "limit_exceeded" in text


def _int(v) -> int:
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


def _dt(v) -> datetime | None:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(v), fmt)
        except (TypeError, ValueError):
            continue
    return None


def parse_campaign(x: dict) -> Campaign:
    return Campaign(_int(x.get("id")), _dt(x.get("start_time")), x.get("status"),
                    _int(x.get("list_id")) or None, _int(x.get("message_id")) or None, x.get("subject"))


def parse_stats(campaign_id: int, x: dict) -> CampaignStats:
    keys = ("total", "sent", "delivered", "read_unique", "read_all", "clicked_unique", "clicked_all",
            "unsubscribed", "spam")
    return CampaignStats(campaign_id, *(_int(x.get(k)) for k in keys))


def delivery_level(status: str | None) -> int:
    """ok_link_visited → 3, ok_read и ok_unsubscribed (отписаться можно только из открытого письма) → 2,
    прочие ok_… (доставлено) → 1, ошибки и «не отправлено» → 0."""
    s = (status or "").strip().lower()
    if not s.startswith("ok"):
        return 0
    if "link" in s or "visit" in s or "click" in s:
        return 3
    if "read" in s or "unsubscrib" in s:
        return 2
    return 1


def csv_rows(text: str) -> list[dict]:
    text = text.lstrip("\ufeff")
    sample = text[:2000]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","
    return [{(k or "").strip().lower(): v for k, v in row.items()}
            for row in csv.DictReader(io.StringIO(text), delimiter=delimiter)]


def table_rows(result) -> list[dict]:
    """Ответ вида {"fields": [...], "data": [[...], ...]} → строки-словари."""
    if not isinstance(result, dict):
        return []
    fields = [str(f).strip().lower() for f in result.get("fields") or []]
    return [dict(zip(fields, row)) for row in result.get("data") or [] if isinstance(row, (list, tuple))]


STATUS_KEYS = ("send_result", "status", "state", "delivery_status", "result")


def parse_delivery(rows: list[dict], keep_emails: bool = False) -> list[Delivery]:
    """Строки отчёта о доставке → отпечаток адреса и статус; при повторе адреса остаётся лучший статус.
    Сам адрес остаётся, только если keep_emails (списки получателей включены)."""
    best: dict[str, Delivery] = {}
    for row in rows:
        found = privacy.emails(row.get("email"))
        h = privacy.email_hash(row.get("email"))
        status = next((str(row[k]) for k in STATUS_KEYS if row.get(k)), "")
        if not h:
            continue
        d = Delivery(h, status, delivery_level(status), found[0] if keep_emails and found else None)
        if h not in best or d.level > best[h].level:
            best[h] = d
    return list(best.values())


_A = re.compile(r"<a\b[^>]*?href\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
SERVICE_LINK = re.compile(r"unisender|unsubscribe|mailto:|tel:|\{\{|%7B%7B|vk\.com|t\.me|telegram|whatsapp|wa\.me|"
                          r"youtube|instagram|ok\.ru|dzen\.ru", re.IGNORECASE)


def extract_links(html: str) -> list[Link]:
    """Ссылки письма на сайт с их текстом; служебные (отписка, соцсети, почта) и метки utm отбрасываются."""
    out, seen = [], set()
    for href, inner in _A.findall(html or ""):
        url = unescape(href).strip().split("#")[0]
        base, _, query = url.partition("?")
        query = "&".join(p for p in query.split("&") if p and not p.lower().startswith("utm_"))
        url = base + ("?" + query if query else "")
        if not url.lower().startswith("http") or SERVICE_LINK.search(url):  # utm_source=Unisender уже убрана
            continue
        text = " ".join(unescape(_TAG.sub(" ", inner)).split())[:200]
        if (url, text) not in seen:
            seen.add((url, text))
            out.append(Link(url, text))
    return out


def parse_contacts(text: str, keep_emails: bool = False) -> list[Contact]:
    """CSV выгрузки базы → отпечатки. Сами адреса и телефоны дальше этой функции не уходят, кроме адреса при
    keep_emails (списки получателей включены)."""
    text = text.lstrip("﻿")
    sample = text[:2000]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","
    out = []
    for row in csv.DictReader(io.StringIO(text), delimiter=delimiter):
        row = {(k or "").strip().lower(): v for k, v in row.items()}
        email, phone = row.get("email"), row.get("phone")
        if not email and not phone:
            continue
        lists = tuple(_int(x) for x in str(row.get("email_list_ids") or "").replace(";", ",").split(",") if _int(x))
        found = privacy.emails(email)
        out.append(Contact(privacy.email_hash(email), privacy.phone_hash(phone), row.get("email_status") or None, lists,
                           found[0] if keep_emails and found else None))
    return out
