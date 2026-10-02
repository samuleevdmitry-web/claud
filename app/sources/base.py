"""Общий интерфейс адаптеров площадок и вежливый HTTP-клиент."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

import httpx

from app.config import get_settings
from app.sources.tls import ssl_context

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- данные


@dataclass
class TenderStub:
    """Результат поиска: минимум, чтобы решить, нужны ли детали."""

    source_code: str
    external_id: str
    url: str
    title: str
    eis_number: str | None = None
    customer_name: str = ""
    published_at: datetime | None = None
    application_deadline: datetime | None = None
    status: str | None = None
    # Метка версии на площадке (дата обновления и т.п.): если не изменилась, детали не запрашиваем.
    revision: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Position:
    name: str
    qty: float | None = None
    unit: str | None = None
    code: str | None = None  # ОКПД2 или КТРУ
    price: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "qty": self.qty, "unit": self.unit, "code": self.code, "price": self.price}


@dataclass
class TenderDetails:
    source_code: str
    external_id: str
    url: str
    title: str
    eis_number: str | None = None
    customer_name: str = ""
    customer_inn: str | None = None
    region: str | None = None
    delivery_place: str = ""
    law: str = "коммерческая"  # 44-ФЗ / 223-ФЗ / коммерческая
    procedure_type: str | None = None
    nmck: Decimal | None = None
    currency: str = "RUB"
    published_at: datetime | None = None
    application_deadline: datetime | None = None
    status: str | None = None
    is_open: bool | None = None  # идёт ли приём заявок (None — неизвестно)
    okpd2_codes: list[str] = field(default_factory=list)
    positions: list[Position] = field(default_factory=list)
    notice_text: str = ""
    # Метка версии извещения на площадке (номер редакции, дата изменения).
    revision: str | None = None
    etp: str | None = None  # площадка проведения (для закупок из ЕИС)


@dataclass
class HealthStatus:
    ok: bool
    message: str = ""


# --------------------------------------------------------------------------- ошибки


class SourceError(Exception):
    """Сбой площадки: прогон по ней помечается как неуспешный."""

    status = "failed"


class SourceBlocked(SourceError):
    """Площадка заблокировала доступ (403, антибот, геоблок)."""

    status = "blocked"


class SourceCaptcha(SourceError):
    """Площадка показала капчу. Обходить её не будем."""

    status = "captcha"


class SourceNotConfigured(SourceError):
    """Не хватает настроек (токен, логин) — адаптер пропускается."""

    status = "skipped"


# --------------------------------------------------------------------------- интерфейс


@runtime_checkable
class SourceAdapter(Protocol):
    code: str
    title: str

    def search(self, query: str, published_from: date, published_to: date) -> AsyncIterator[TenderStub]: ...

    async def fetch_details(self, stub: TenderStub) -> TenderDetails: ...

    async def healthcheck(self) -> HealthStatus: ...


# --------------------------------------------------------------------------- HTTP


_CAPTCHA_MARKERS = ("showcaptcha", "smartcaptcha", "g-recaptcha", "captcha-container", "hcaptcha")
_ANTIBOT_MARKERS = ("защиты от ботов", "ddos-guard", "проверка браузера", "checking your browser")


class PoliteClient:
    """HTTP-клиент для одной площадки: один запрос за раз, пауза 2–5 с, повторы при 429/5xx."""

    def __init__(
        self,
        source_code: str,
        *,
        base_url: str = "",
        headers: dict[str, str] | None = None,
        delay_min: float | None = None,
        delay_max: float | None = None,
        max_retries: int = 4,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep=asyncio.sleep,
    ):
        settings = get_settings()
        self.source_code = source_code
        self.delay_min = settings.request_delay_min if delay_min is None else delay_min
        self.delay_max = settings.request_delay_max if delay_max is None else delay_max
        self.max_retries = max_retries
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self.requests_made = 0
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"User-Agent": settings.user_agent, **(headers or {})},
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
            verify=ssl_context(),
        )

    async def __aenter__(self) -> PoliteClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _pause(self) -> None:
        if self._last_request:
            wait = random.uniform(self.delay_min, self.delay_max) - (time.monotonic() - self._last_request)
            if wait > 0:
                await self._sleep(wait)

    async def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        async with self._lock:  # один параллельный запрос на площадку
            attempt = 0
            while True:
                await self._pause()
                self._last_request = time.monotonic()
                self.requests_made += 1
                try:
                    response = await self._client.request(method, url, **kwargs)
                except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError) as exc:
                    if attempt >= self.max_retries:
                        raise SourceBlocked(f"соединение обрывается: {exc!r}") from exc
                    await self._backoff(attempt, None)
                    attempt += 1
                    continue
                except httpx.TimeoutException as exc:
                    if attempt >= self.max_retries:
                        raise SourceError(f"таймаут: {exc!r}") from exc
                    await self._backoff(attempt, None)
                    attempt += 1
                    continue

                if response.status_code == 429 or response.status_code >= 500:
                    if attempt >= self.max_retries:
                        raise SourceError(f"HTTP {response.status_code} после {attempt + 1} попыток: {url}")
                    await self._backoff(attempt, response.headers.get("retry-after"))
                    attempt += 1
                    continue
                self._check_blocked(response)
                return response

    async def _backoff(self, attempt: int, retry_after: str | None) -> None:
        delay = min(2 ** (attempt + 1), 120) + random.uniform(0, 1)
        if retry_after:
            try:
                delay = max(delay, float(retry_after) + 1)
            except ValueError:
                pass
        log.info("%s: повтор через %.0f с", self.source_code, delay)
        await self._sleep(delay)

    def _check_blocked(self, response: httpx.Response) -> None:
        url = str(response.url).lower()
        if any(m in url for m in _CAPTCHA_MARKERS):
            raise SourceCaptcha(f"капча: {response.url}")
        ctype = response.headers.get("content-type", "")
        if "html" in ctype:
            head = response.text[:5000].lower()
            if any(m in head for m in _CAPTCHA_MARKERS):
                raise SourceCaptcha(f"капча на странице {response.url}")
            if any(m in head for m in _ANTIBOT_MARKERS):
                raise SourceBlocked(f"антибот-защита на странице {response.url}")
        if response.status_code in (401, 403):
            raise SourceBlocked(f"HTTP {response.status_code}: {response.url}")

    async def get(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def get_json(self, url: str, **kwargs) -> Any:
        response = await self.get(url, **kwargs)
        if response.status_code >= 400:
            raise SourceError(f"HTTP {response.status_code}: {response.text[:300]}")
        try:
            return response.json()
        except ValueError as exc:
            raise SourceError(f"ответ не JSON: {response.text[:200]!r}") from exc
