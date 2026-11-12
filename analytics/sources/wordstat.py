"""Вордстат через Yandex Cloud Search API: частотность запросов по месяцам и популярные формулировки."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date

import requests

from analytics.sources.http import send

API_URL = "https://searchapi.api.cloud.yandex.net/v2/wordstat"


class WordstatError(RuntimeError):
    pass


@dataclass(frozen=True)
class MonthCount:
    month: date
    count: int
    share: float  # доля от всех запросов к Яндексу в регионе


class WordstatClient:
    def __init__(
        self,
        api_key: str,
        folder_id: str,
        session: requests.Session | None = None,
        max_retries: int = 4,
        backoff: float = 2.0,
        pause: float = 0.2,
    ):
        if not folder_id:
            raise WordstatError("Не задан WORDSTAT_FOLDER_ID (ID каталога Yandex Cloud)")
        self.folder_id = folder_id
        self.session = session or requests.Session()
        # Без ключа заголовок не ставим: его может подставлять прокси (секрет облачного окружения).
        if api_key:
            self.session.headers["Authorization"] = f"Api-Key {api_key}"
        self.max_retries = max_retries
        self.backoff = backoff
        self.pause = pause  # пауза между запросами, чтобы не упираться в квоты

    def _post(self, method: str, body: dict) -> dict:
        resp = send(
            self.session, "POST", API_URL + method, json={**body, "folderId": self.folder_id},
            max_retries=self.max_retries, backoff=self.backoff,
        )
        time.sleep(self.pause)
        if resp.status_code != 200:
            hint = ""
            if resp.status_code in (401, 403):
                hint = ". Проверьте API-ключ (роль search-api.webSearch.user, область yc.search-api.execute)"
            raise WordstatError(f"{method} «{body.get('phrase')}»: HTTP {resp.status_code}: {_error_message(resp)}{hint}")
        return resp.json()

    def monthly(self, phrase: str, region_id: int, since: date) -> list[MonthCount]:
        data = self._post("/dynamics", {
            "phrase": phrase,
            "period": "PERIOD_MONTHLY",
            "fromDate": f"{since.replace(day=1).isoformat()}T00:00:00Z",
            "regions": [str(region_id)],
        })
        return parse_dynamics(data.get("results", []))

    def top_requests(self, phrase: str, region_id: int, limit: int = 50) -> list[tuple[str, int]]:
        """Формулировки с этими словами за последние 30 дней, по убыванию частотности."""
        data = self._post("/topRequests", {"phrase": phrase, "numPhrases": limit, "regions": [str(region_id)]})
        return [(r["phrase"], int(r.get("count") or 0)) for r in data.get("results", [])]


def _error_message(resp: requests.Response) -> str:
    try:
        return str(resp.json().get("message") or resp.text[:300])
    except ValueError:
        return resp.text[:300]


def parse_dynamics(results: list[dict]) -> list[MonthCount]:
    return [
        # нулевые значения API (protobuf) не присылает вовсе: нет поля — значит 0
        MonthCount(date.fromisoformat(r["date"][:10]).replace(day=1), int(r.get("count") or 0), float(r.get("share") or 0))
        for r in results
    ]
