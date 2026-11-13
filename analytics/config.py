"""Настройки из переменных окружения и файла .env (переменные окружения важнее)."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

DEFAULT_DATABASE_URL = "postgresql://analytics:analytics@localhost:5433/analytics"


@dataclass(frozen=True)
class Settings:
    yandex_token: str
    metrika_counters: tuple[int, ...]  # счётчики сайтов
    metrika_lead_goals: frozenset[int]  # ID целей уникальны во всей Метрике, поэтому список общий для всех сайтов
    metrika_attribution: str
    metrika_maps_counters: tuple[int, ...]
    webmaster_hosts: tuple[str, ...]
    wordstat_api_key: str
    wordstat_folder_id: str
    wordstat_keywords_path: str
    rabbit_guest_url: str
    database_url: str
    reports_dir: str = "reports"  # куда сборщик сохраняет ежемесячные отчёты
    imports_dir: str = "imports"  # куда кладутся выгрузки из Битрикс24 и 1С (Excel)
    semantics_url: str = ""  # ссылка на Google Таблицу семантики: скачивается раз в неделю
    site_search_param: str = "q"  # параметр адреса с запросом поиска по сайту (oldos.ru: /catalog/?q=…)
    not_found_title: str = "Страница не найдена"  # заголовок страницы 404 на сайте


def _list(name: str) -> tuple[str, ...]:
    """Список через запятую; порядок сохраняется (первый сайт — основной в дашборде)."""
    return tuple(part.strip() for part in os.environ.get(name, "").split(",") if part.strip())


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        yandex_token=os.environ.get("YANDEX_OAUTH_TOKEN", "").strip(),
        metrika_counters=tuple(int(v) for v in _list("METRIKA_COUNTERS")),
        metrika_lead_goals=frozenset(int(v) for v in _list("METRIKA_LEAD_GOALS")),
        metrika_attribution=os.environ.get("METRIKA_ATTRIBUTION", "").strip() or "lastsign",
        metrika_maps_counters=tuple(int(v) for v in _list("METRIKA_MAPS_COUNTERS")),
        webmaster_hosts=_list("WEBMASTER_HOSTS"),
        wordstat_api_key=os.environ.get("WORDSTAT_API_KEY", "").strip(),
        wordstat_folder_id=os.environ.get("WORDSTAT_FOLDER_ID", "").strip(),
        wordstat_keywords_path=os.environ.get("WORDSTAT_KEYWORDS", "").strip() or "wordstat.toml",
        rabbit_guest_url=os.environ.get("RABBIT_GUEST_URL", "").strip(),
        database_url=os.environ.get("DATABASE_URL", "").strip() or DEFAULT_DATABASE_URL,
        reports_dir=os.environ.get("REPORTS_DIR", "").strip() or "reports",
        imports_dir=os.environ.get("IMPORTS_DIR", "").strip() or "imports",
        semantics_url=os.environ.get("SEMANTICS_URL", "").strip(),
        site_search_param=os.environ.get("SITE_SEARCH_PARAM", "").strip() or "q",
        not_found_title=os.environ.get("NOT_FOUND_TITLE", "").strip() or "Страница не найдена",
    )
