"""RabbitSeo: позиции сайта по запросам проекта через гостевую ссылку (только просмотр).

Гостевая страница https://rabbit-seo.ru/s/<код> подгружает позиции с <ссылка>/positions/data?from=…&to=…
(не больше 366 дней за запрос). В ответе по каждому запросу — частотность и массив позиций, где индекс —
это «дней назад»: 0 — сегодня, 1 — вчера и т. д. (так их читает сама страница). checked — дни, когда был
съём; пустая позиция в такой день — сайт в выдаче не найден.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

import requests

from analytics.sources.http import send

MAX_DAYS = 366  # ограничение страницы на период одного запроса


class RabbitError(RuntimeError):
    pass


@dataclass(frozen=True)
class PositionRow:
    date: date
    query: str
    frequency: int
    position: int | None  # None — в этот день сайт по запросу не найден


class RabbitClient:
    def __init__(self, guest_url: str, session: requests.Session | None = None, max_retries: int = 4,
                 backoff: float = 2.0):
        if not guest_url:
            raise RabbitError("Не задан RABBIT_GUEST_URL (гостевая ссылка проекта RabbitSeo)")
        self.url = guest_url.rstrip("/")
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = "Mozilla/5.0 (analytics dashboard)"
        self.max_retries = max_retries
        self.backoff = backoff

    def _get(self, url: str, **kwargs) -> requests.Response:
        resp = send(self.session, "GET", url, max_retries=self.max_retries, backoff=self.backoff, **kwargs)
        if resp.status_code == 404:
            raise RabbitError("Гостевая ссылка RabbitSeo не открывается (404): её сменили или выключили гостевой доступ")
        if resp.status_code != 200:
            raise RabbitError(f"RabbitSeo: HTTP {resp.status_code}")
        return resp

    def project(self) -> str:
        """Сайт проекта — так его называет сама страница (имя CSV-файла)."""
        match = re.search(r'data-csv-name="([^"]+)"', self._get(self.url).text)
        return match.group(1) if match else "project"

    def positions(self, date_from: date, date_to: date) -> dict:
        return self._get(
            self.url + "/positions/data",
            params={"from": date_from.isoformat(), "to": date_to.isoformat()},
            headers={"Accept": "application/json"},
        ).json()


def parse_positions(data: dict) -> list[PositionRow]:
    today = date.fromisoformat(data["today"])
    checked = data.get("checked", [])
    out = []
    for row in data.get("rows", []):
        pos = row.get("pos", [])
        for back in checked:
            if back < len(pos):
                value = pos[back]
                out.append(PositionRow(today - timedelta(days=back), row["q"], int(row.get("freq") or 0),
                                       None if value is None else int(value)))
    return out
