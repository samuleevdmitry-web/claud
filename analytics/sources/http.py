"""Общее для API Яндекса: OAuth-заголовок и повторы запросов."""

from __future__ import annotations

import time

import requests


def yandex_session(token: str, session: requests.Session | None = None) -> requests.Session:
    session = session or requests.Session()
    # Без токена заголовок не ставим: его может подставлять прокси (секрет облачного окружения).
    if token:
        session.headers["Authorization"] = f"OAuth {token}"
    return session


def send(
    session: requests.Session,
    method: str,
    url: str,
    *,
    max_retries: int,
    backoff: float,
    **kwargs,
) -> requests.Response:
    """Запрос с повторами при 429 и 5xx; возвращает последний ответ, каким бы он ни был."""
    attempt = 0
    while True:
        resp = session.request(method, url, timeout=120, **kwargs)
        if (resp.status_code == 429 or resp.status_code >= 500) and attempt < max_retries:
            time.sleep(backoff * 2**attempt)
            attempt += 1
            continue
        return resp
