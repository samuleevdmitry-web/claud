"""Настройки из переменных окружения и файла .env (переменные окружения важнее)."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

DEFAULT_DATABASE_URL = "postgresql://analytics:analytics@localhost:5432/analytics"


@dataclass(frozen=True)
class Settings:
    yandex_token: str
    metrika_counter_id: int | None
    metrika_lead_goals: frozenset[int]
    metrika_attribution: str
    metrika_maps_counters: tuple[int, ...]
    webmaster_host_id: str | None
    database_url: str


def _int_set(value: str) -> frozenset[int]:
    return frozenset(int(part) for part in value.replace(" ", "").split(",") if part)


def load_settings() -> Settings:
    load_dotenv()
    counter = os.environ.get("METRIKA_COUNTER_ID", "").strip()
    return Settings(
        yandex_token=os.environ.get("YANDEX_OAUTH_TOKEN", "").strip(),
        metrika_counter_id=int(counter) if counter else None,
        metrika_lead_goals=_int_set(os.environ.get("METRIKA_LEAD_GOALS", "")),
        metrika_attribution=os.environ.get("METRIKA_ATTRIBUTION", "").strip() or "lastsign",
        metrika_maps_counters=tuple(sorted(_int_set(os.environ.get("METRIKA_MAPS_COUNTERS", "")))),
        webmaster_host_id=os.environ.get("WEBMASTER_HOST_ID", "").strip() or None,
        database_url=os.environ.get("DATABASE_URL", "").strip() or DEFAULT_DATABASE_URL,
    )
