from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.web.highlight import highlight

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def _local_dt(value: datetime | None, fmt: str = "%d.%m.%Y %H:%M") -> str:
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo("UTC"))
    return value.astimezone(ZoneInfo(get_settings().timezone)).strftime(fmt)


def _header():
    """Счётчики новых тендеров, непрочитанные уведомления и тревоги по площадкам для шапки."""
    from app.db import get_session_factory
    from app.services.tenders import header_info

    with get_session_factory()() as session:
        return header_info(session)


def _money(value) -> str:
    if value is None:
        return "—"
    return f"{float(value):,.2f}".replace(",", " ").replace(".", ",") + " ₽"


templates.env.filters["dt"] = _local_dt
templates.env.filters["money"] = _money
templates.env.globals["header"] = _header
templates.env.globals["highlight"] = highlight
