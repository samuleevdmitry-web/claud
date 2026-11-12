"""Дашборд: разделы с меню слева, вкладки отчётов Метрики и аналитические сводки.

Запуск: uvicorn analytics.dashboard.app:app --port 8080
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from analytics import db
from analytics.config import load_settings
from analytics.dashboard import pages
from analytics.format import fmt_change, fmt_dec, fmt_duration, fmt_int, fmt_pct, month_name, month_short

HERE = Path(__file__).parent
TZ = ZoneInfo("Europe/Moscow")

# Меню слева: адрес, название, значок (имя символа в templates/base.html).
NAV = [
    ("overview", "/", "Обзор", "home"),
    ("traffic", "/traffic", "Трафик", "chart"),
    ("leads", "/leads", "Заявки и цели", "target"),
    ("search", "/search", "Поиск Яндекса", "search"),
    ("demand", "/demand", "Спрос", "trend"),
    ("maps", "/maps", "Карты", "pin"),
    ("status", "/status", "Данные", "db"),
]

app = FastAPI(title="Аналитика", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters.update(
    int=fmt_int, dec=fmt_dec, pct=fmt_pct, duration=fmt_duration, month=month_name, month_short=month_short,
    change=fmt_change, local=lambda dt: dt.astimezone(TZ).strftime("%d.%m.%Y %H:%M") if dt else "—",
)


@app.get("/health")
def health() -> PlainTextResponse:
    return PlainTextResponse("ok")


def render(request: Request, page: str, build: Callable[..., dict], site: int | None, month: str | None,
           aud: str | None, **kwargs) -> HTMLResponse:
    """Общая обвязка раздела: фильтры, меню, пустая база, нет связи с базой."""
    settings = load_settings()
    context: dict = {"page": page, "nav": NAV, "audiences": pages.AUDIENCES}
    template = f"{page}.html"
    try:
        with db.connect(settings.database_url) as conn:
            f = pages.resolve_filters(conn, settings, site, month, aud, date.today())
            context["f"] = f
            if f is None and page != "status":
                context.update(pages.status_page(conn, settings, None), empty=True)
                template = "empty.html"
            else:
                context.update(build(conn, settings, f, **kwargs) or {})
    except psycopg.OperationalError as exc:
        context.update(error=f"Нет подключения к базе: {exc}")
        template = "empty.html"
    return templates.TemplateResponse(request, template, context)


@app.get("/", response_class=HTMLResponse)
def overview(request: Request, site: int | None = None, month: str | None = None, aud: str | None = None):
    return render(request, "overview", pages.overview, site, month, aud)


@app.get("/traffic", response_class=HTMLResponse)
def traffic(request: Request, site: int | None = None, month: str | None = None, aud: str | None = None,
            tab: str = "summary"):
    return render(request, "traffic", pages.traffic, site, month, aud, tab=tab)


@app.get("/leads", response_class=HTMLResponse)
def leads(request: Request, site: int | None = None, month: str | None = None, aud: str | None = None):
    return render(request, "leads", pages.leads, site, month, aud)


@app.get("/search", response_class=HTMLResponse)
def search(request: Request, site: int | None = None, month: str | None = None, aud: str | None = None):
    return render(request, "search", pages.search_page, site, month, aud)


@app.get("/demand", response_class=HTMLResponse)
def demand(request: Request, site: int | None = None, month: str | None = None, aud: str | None = None):
    return render(request, "demand", _demand, site, month, aud)


def _demand(conn: psycopg.Connection, settings, f: pages.Filters) -> dict:
    data = pages.demand_page(conn, settings, f)
    return {"demand": data, "charts": data["charts"] if data else {}}


@app.get("/maps", response_class=HTMLResponse)
def maps(request: Request, site: int | None = None, month: str | None = None, aud: str | None = None):
    return render(request, "maps", pages.maps_page, site, month, aud)


@app.get("/status", response_class=HTMLResponse)
def status(request: Request, site: int | None = None, month: str | None = None, aud: str | None = None):
    return render(request, "status", pages.status_page, site, month, aud)
