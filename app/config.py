from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # БД: PostgreSQL в Docker, SQLite для локальной разработки.
    database_url: str = f"sqlite:///{PROJECT_ROOT / 'data' / 'app.db'}"

    # Доступ к приложению: HTTP Basic. Пустой пароль — доступ без пароля (только для разработки).
    app_username: str = "velmorium"
    app_password: str = ""

    # Файл ключевых слов, который импортируется при первом запуске, если в БД ещё нет версий.
    keywords_file: Path = PROJECT_ROOT / "Velmorium_ключевые_слова_тендеры.xlsx"
    # Правило беглой гласной при сопоставлении масок (подушк* → подушек).
    mask_fleeting_vowels: bool = True

    # Расписание прогонов (используется планировщиком на этапе 5).
    run_interval_hours: int = 48
    run_start_time: str = "06:00"
    timezone: str = "Europe/Moscow"
    initial_history_days: int = 30
    search_overlap_days: int = 2

    # Вежливые лимиты для адаптеров.
    request_delay_min: float = 2.0
    request_delay_max: float = 5.0
    user_agent: str = "VelmoriumTenderMonitor/0.1"

    # Уведомления (опционально).
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_to: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
