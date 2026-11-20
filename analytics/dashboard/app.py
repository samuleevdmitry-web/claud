"""Дашборд: разделы с меню слева, вкладки отчётов Метрики и аналитические сводки.

Запуск: uvicorn analytics.dashboard.app:app --port 8080
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from zoneinfo import ZoneInfo

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from analytics import db
from analytics.config import load_settings
from analytics.dashboard import guard, pages
from analytics.format import fmt_change, fmt_dec, fmt_duration, fmt_int, fmt_pct, month_name, month_short, month_with, plural

HERE = Path(__file__).parent
TZ = ZoneInfo("Europe/Moscow")

# Меню слева: адрес, название, значок (имя символа в templates/base.html).
NAV = [
    ("overview", "/", "Обзор", "home"),
    ("recs", "/recs", "Рекомендации", "check"),
    ("traffic", "/traffic", "Трафик", "chart"),
    ("leads", "/leads", "Заявки и цели", "target"),
    ("sales", "/sales", "Продажи", "cart"),
    ("email", "/email", "E-mail", "mail"),
    ("seo", "/seo", "Ассистент SEO", "spark"),
    ("search", "/search", "Поиск Яндекса", "search"),
    ("keywords", "/keywords", "Ключи", "key"),
    ("pf", "/pf", "ПФ и риски", "shield"),
    ("report", "/report", "Отчёт за месяц", "doc"),
    ("demand", "/demand", "Спрос", "trend"),
    ("maps", "/maps", "Карты", "pin"),
    ("status", "/status", "Данные", "db"),
]
# Разделы, где есть переключатель «Только люди / Весь трафик».
AUDIENCE_PAGES = {"traffic"}
# Группы меню: пункты подряд с одной группой показываются под общим заголовком.
NAV_GROUPS = {"seo": "SEO", "search": "SEO", "keywords": "SEO", "pf": "SEO"}

@asynccontextmanager
async def lifespan(_: FastAPI):
    """При запуске дашборд обновляет схему базы: после обновления кода таблицы и представления нужны сразу,
    не дожидаясь сборщика. Если базы ещё нет, страницы сами объяснят это."""
    try:
        with db.connect(load_settings().database_url) as conn:
            # сборщик может держать таблицы долгой загрузкой: дашборд не ждёт его дольше 15 секунд — схему
            # сборщик обновит сам, а страницы откроются сразу
            db.apply_schema(conn, lock_timeout="15s")
    except psycopg.errors.LockNotAvailable:
        guard.log.warning("схема не обновлена при запуске: таблицы заняты сборщиком, он обновит её сам")
    except psycopg.OperationalError:
        pass
    yield


PAGE_TIMEOUT_MS = 60_000  # один запрос страницы — не дольше минуты


app = FastAPI(title="Аналитика", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.add_extension(guard.GuardExtension)  # {% guard %}: блок с ошибкой не роняет страницу
def urlencode_pairs(pairs, **override) -> str:
    """Параметры адреса из пар (ключ, значение) с заменой части: для сортировки, листания и ссылок таблицы ключей."""
    params = dict(pairs) | override
    return urlencode({k: v.isoformat() if isinstance(v, date) else v for k, v in params.items() if v not in (None, "")})


templates.env.filters.update(
    urlencode_pairs=urlencode_pairs,
    int=fmt_int, dec=fmt_dec, pct=fmt_pct, duration=fmt_duration, month=month_name, month_short=month_short, month_with=month_with,
    change=fmt_change, local=lambda dt: dt.astimezone(TZ).strftime("%d.%m.%Y %H:%M") if dt else "—",
    plural=lambda n, *forms: plural(round(n), forms),
)


@app.get("/health")
def health() -> PlainTextResponse:
    return PlainTextResponse("ok")


def render(request: Request, page: str, build: Callable[..., dict], site: int | None, month: str | None,
           aud: str | None, **kwargs) -> HTMLResponse:
    """Общая обвязка раздела: фильтры, меню, пустая база, нет связи с базой."""
    settings = load_settings()
    context: dict = {"page": page, "nav": NAV, "nav_groups": NAV_GROUPS, "audiences": pages.AUDIENCES,
                     "audience_switch": page in AUDIENCE_PAGES, "problems": guard.start()}
    template = f"{page}.html"
    try:
        with db.connect(settings.database_url, PAGE_TIMEOUT_MS) as conn:
            # «Только люди / весь трафик» выбирается только в «Трафике»; остальные разделы — по умолчанию
            f = pages.resolve_filters(conn, settings, site, month, aud if page in AUDIENCE_PAGES else None, date.today())
            context["f"] = f
            if f is None and page != "status":
                context.update(pages.status_page(conn, settings, None), empty=True)
                template = "empty.html"
            else:
                context.update(build(conn, settings, f, **kwargs) or {})
    except psycopg.errors.QueryCanceled:
        guard.log.exception("раздел %s: запрос дольше минуты", page)
        context.update(error="Раздел не построен: запрос к базе выполнялся дольше минуты. Обычно так бывает, пока сборщик "
                             "загружает большие файлы, — обновите страницу через пару минут; если повторяется — сообщите.",
                       section_title=next((title for key, _, title, _ in NAV if key == page), None))
        template = "empty.html"
    except psycopg.OperationalError as exc:
        context.update(error=f"Нет подключения к базе: {exc}")
        template = "empty.html"
    except Exception as exc:  # noqa: BLE001 — раздел не построился целиком: показать почему, меню остаётся рабочим
        guard.log.exception("раздел %s не построен", page)
        context.update(error=f"Раздел не построен: {guard.describe(exc)}. Другие разделы меню работают; если повторяется — "
                             "пришлите этот текст.",
                       section_title=next((title for key, _, title, _ in NAV if key == page), None))
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


@app.get("/sales", response_class=HTMLResponse)
def sales(request: Request, site: int | None = None, month: str | None = None, aud: str | None = None):
    return render(request, "sales", pages.sales_page, site, month, aud)


@app.get("/keywords", response_class=HTMLResponse)
def keywords(request: Request, site: int | None = None, month: str | None = None, aud: str | None = None):
    return render(request, "keywords", pages.keywords_page, site, month, aud, params=dict(request.query_params))


@app.get("/keywords/detail", response_class=HTMLResponse)
def keyword(request: Request, site: int | None = None, month: str | None = None, aud: str | None = None):
    return render(request, "keyword", pages.keyword_detail, site, month, aud, params=dict(request.query_params))


@app.get("/keywords.csv")
def keywords_csv(request: Request, site: int | None = None) -> Response:
    settings = load_settings()
    with db.connect(settings.database_url, PAGE_TIMEOUT_MS) as conn:
        f = pages.resolve_filters(conn, settings, site, None, None, date.today())
        body = pages.keywords_csv(conn, settings, f, dict(request.query_params)) if f else "\ufeff"
    return Response(body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="keywords.csv"'})


@app.get("/email/contacts.csv")
def email_contacts_csv(site: int | None = None, month: str | None = None, kind: str = "active") -> Response:
    settings = load_settings()
    with db.connect(settings.database_url, PAGE_TIMEOUT_MS) as conn:
        f = pages.resolve_filters(conn, settings, site, month, None, date.today())
        body = pages.contacts_csv(conn, settings, f, kind) if f else "\ufeff"
    name = f"email-{kind}-{f.month:%Y-%m}.csv" if f else "email.csv"
    return Response(body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/email", response_class=HTMLResponse)
def email(request: Request, site: int | None = None, month: str | None = None, aud: str | None = None):
    return render(request, "email", pages.email_page, site, month, aud)


@app.get("/seo", response_class=HTMLResponse)
def seo_assistant(request: Request, site: int | None = None, month: str | None = None, aud: str | None = None):
    return render(request, "seo", pages.seo_page, site, month, aud, params=dict(request.query_params))


def same_origin(request: Request) -> bool:
    """Кнопки ассистента меняют данные: принимаем их только со страниц самого дашборда, не с чужого сайта,
    открытого в том же браузере."""
    if request.headers.get("sec-fetch-site") == "cross-site":
        return False
    origin = request.headers.get("origin")
    if origin and origin != "null":
        return urlsplit(origin).netloc == request.headers.get("host")
    return True


@app.post("/seo/action")
async def seo_action(request: Request) -> Response:
    if not same_origin(request):
        return PlainTextResponse("Запрос с другого сайта отклонён", status_code=403)
    form = parse_qs((await request.body()).decode("utf-8", "replace"), keep_blank_values=True)
    site = (form.get("site") or [""])[0]
    settings = load_settings()
    with db.connect(settings.database_url, PAGE_TIMEOUT_MS) as conn:
        f = pages.resolve_filters(conn, settings, int(site) if site.isdigit() else None, None, None, date.today())
        if f is None:
            return RedirectResponse("/seo", status_code=303)
        code, n, anchor = pages.seo_action(conn, settings, f, form)
    back = {k: (form.get(k) or [""])[0] for k in ("q", "lane", "src", "sort", "p") if (form.get(k) or [""])[0]}
    return RedirectResponse(f"/seo{f.query(msg=code, n=n or None, **back)}" + (f"#{anchor}" if anchor else ""),
                            status_code=303)


@app.get("/search", response_class=HTMLResponse)
def search(request: Request, site: int | None = None, month: str | None = None, aud: str | None = None):
    return render(request, "search", pages.search_page, site, month, aud)


@app.get("/pf", response_class=HTMLResponse)
def pf(request: Request, site: int | None = None, month: str | None = None, aud: str | None = None):
    return render(request, "pf", pages.pf_page, site, month, aud)


@app.get("/recs", response_class=HTMLResponse)
def recs(request: Request, site: int | None = None, month: str | None = None, aud: str | None = None):
    return render(request, "recs", pages.recommendations_page, site, month, aud)


@app.get("/report", response_class=HTMLResponse)
def report(request: Request, site: int | None = None, month: str | None = None, aud: str | None = None):
    return render(request, "report", pages.report_page, site, month, aud)


def report_file(conn: psycopg.Connection, settings, counter_id: int, month: date, today: date) -> tuple[str, str] | None:
    """Отчёт за месяц одним HTML-файлом со встроенными стилями: имя файла и содержимое. None — данных нет."""
    f = pages.resolve_filters(conn, settings, counter_id, month.strftime("%Y-%m"), None, today)
    if f is None or f.month != month or f.counter_id != counter_id:
        return None
    problems = guard.start()
    context = pages.report_page(conn, settings, f)
    css = (HERE / "static" / "dashboard.css").read_text(encoding="utf-8")
    html = templates.env.get_template("report_file.html").render(f=f, css=css, audiences=pages.AUDIENCES,
                                                                 problems=problems, **context)
    return f"{month:%Y-%m} {f.site.get('site') or counter_id}.html", html


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
