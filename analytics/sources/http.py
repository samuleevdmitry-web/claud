"""Общее для API Яндекса: OAuth-заголовок и повторы запросов."""

from __future__ import annotations

import time
from collections.abc import Callable

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
    quota_wait: float = 0,
    give_up: Callable[[requests.Response], bool] | None = None,
    **kwargs,
) -> requests.Response:
    """Запрос с повторами при 429 и 5xx; возвращает последний ответ, каким бы он ни был.

    quota_wait — сколько минимум ждать после 429 (квота на частоту запросов снимается не сразу);
    если сервер прислал Retry-After, ждём столько, сколько он просит.
    give_up — ответы, которые повторять бессмысленно (например, исчерпана суточная квота).
    """
    attempt = 0
    while True:
        resp = session.request(method, url, timeout=120, **kwargs)
        if give_up and give_up(resp):
            return resp
        if (resp.status_code == 429 or resp.status_code >= 500) and attempt < max_retries:
            delay = backoff * 2**attempt
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After", "")
                delay = max(delay, quota_wait, float(retry_after) if retry_after.isdigit() else 0)
            time.sleep(delay)
            attempt += 1
            continue
        return resp
