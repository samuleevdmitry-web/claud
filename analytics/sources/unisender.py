"""UniSender: рассылки, их статистика и база контактов.

API: https://api.unisender.com/ru/api/<метод>?format=json&api_key=… Ключ — в личном кабинете UniSender:
«Настройки аккаунта» → «Интеграции и API». База выгружается асинхронно: async/exportContacts → task_uuid →
async/getTaskResult, пока статус не «completed», → файл CSV по ссылке file_to_download.
Почта и телефон из выгрузки сразу превращаются в отпечатки (analytics.privacy) и в базу не попадают.
"""

from __future__ import annotations

import csv
import io
import time
from dataclasses import dataclass
from datetime import date, datetime

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
class Contact:
    email_hash: str | None
    phone_hash: str | None
    status: str | None
    list_ids: tuple[int, ...]


class UnisenderClient:
    def __init__(self, api_key: str, session: requests.Session | None = None, max_retries: int = 4,
                 backoff: float = 2.0, pause: float = 0.1):
        if not api_key:
            raise UnisenderError("Не задан UNISENDER_API_KEY (ключ API UniSender)")
        self.api_key = api_key
        self.session = session or requests.Session()
        self.max_retries, self.backoff, self.pause = max_retries, backoff, pause

    def call(self, method: str, **params):
        resp = send(self.session, "GET", API + method, max_retries=self.max_retries, backoff=self.backoff,
                    params={"format": "json", "api_key": self.api_key, **params})
        time.sleep(self.pause)
        if resp.status_code != 200:
            raise UnisenderError(f"UniSender {method}: HTTP {resp.status_code}")
        data = resp.json()
        if "error" in data:
            code = data.get("code") or ""
            hint = " — проверьте ключ API" if "invalid_api_key" in str(code) or "api_key" in str(data["error"]) else ""
            raise UnisenderError(f"UniSender {method}: {data['error']}{hint}")
        return data.get("result")

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

    def export_contacts(self, wait: float = 5.0, timeout: float = 900.0) -> list[Contact]:
        params = {f"field_names[{i}]": name for i, name in enumerate(EXPORT_FIELDS)}
        task = self.call("async/exportContacts", **params) or {}
        uuid = task.get("task_uuid")
        if not uuid:
            raise UnisenderError("UniSender не вернул task_uuid для выгрузки базы")
        started = time.monotonic()
        while True:
            result = self.call("async/getTaskResult", task_uuid=uuid) or {}
            if result.get("status") == "completed":
                return parse_contacts(self._download(result.get("file_to_download") or ""))
            if result.get("status") not in ("new", "processing", None) or time.monotonic() - started > timeout:
                raise UnisenderError(f"Выгрузка базы UniSender не готова: статус {result.get('status')}")
            time.sleep(wait)

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


def parse_contacts(text: str) -> list[Contact]:
    """CSV выгрузки базы → отпечатки. Сами адреса и телефоны дальше этой функции не уходят."""
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
        out.append(Contact(privacy.email_hash(email), privacy.phone_hash(phone), row.get("email_status") or None, lists))
    return out
