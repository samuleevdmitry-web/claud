from __future__ import annotations

import json
import secrets

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasicCredentials
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.keywords.queries import build_queries
from app.services.importer import ImportPayload, import_payload
from app.services.keywords import get_active_keyword_set
from app.sources.registry import BY_CODE, SOURCES
from app.web.auth import _security, require_auth
from app.web.templating import templates

router = APIRouter()

EXAMPLE = {
    "source": "bidzaar",
    "complete": True,
    "searched_queries": ["гостиничный текстиль", "полотенце махровое"],
    "tenders": [
        {
            "external_id": "123456",
            "url": "https://bidzaar.com/...",
            "title": "Поставка полотенец махровых для отеля",
            "customer_name": "ООО «Отель Пример»",
            "region": "Краснодарский край",
            "delivery_place": "г. Сочи",
            "law": "коммерческая",
            "nmck": 350000,
            "published_at": "2026-10-01",
            "application_deadline": "2026-10-10T12:00",
            "status": "Приём заявок",
            "positions": [{"name": "Полотенце махровое 50×90", "qty": 300, "unit": "шт"}],
            "notice_text": "",
        }
    ],
}


def _context(db: Session, **extra) -> dict:
    active = get_active_keyword_set(db)
    queries = build_queries(active[1]) if active else []
    return {
        "sources": SOURCES,
        "queries": queries,
        "example": json.dumps(EXAMPLE, ensure_ascii=False, indent=2),
        **extra,
    }


def _run_import(db: Session, raw: str) -> tuple[dict | None, str | None]:
    active = get_active_keyword_set(db)
    if not active:
        return None, "Нет активной версии ключевых слов"
    try:
        payload = ImportPayload.model_validate_json(raw)
    except ValidationError as exc:
        return None, "Данные не подходят под формат: " + "; ".join(
            f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:10]
        )
    if payload.source not in BY_CODE:
        return None, f"Неизвестная площадка «{payload.source}»"
    version, ks = active
    stat = import_payload(db, payload, ks, version.id)
    return {
        "source": BY_CODE[payload.source].title,
        "received": len(payload.tenders),
        "new": stat.new,
        "updated": stat.updated,
        "status": stat.status,
    }, None


@router.get("/import", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
def import_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "import.html", _context(db))


@router.post("/import", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
async def import_form(
    request: Request,
    data: str = Form(""),
    file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    raw = data.strip()
    if file is not None and file.filename:
        raw = (await file.read()).decode("utf-8-sig")
    if not raw:
        return templates.TemplateResponse(
            request, "import.html", _context(db, error="Пустые данные"), status_code=400
        )
    result, error = _run_import(db, raw)
    return templates.TemplateResponse(
        request,
        "import.html",
        _context(db, result=result, error=error, data=raw if error else ""),
        status_code=400 if error else 200,
    )


def _api_auth(
    x_import_token: str | None = Header(default=None),
    credentials: HTTPBasicCredentials | None = Depends(_security),
) -> None:
    token = get_settings().import_token
    if token and x_import_token and secrets.compare_digest(x_import_token, token):
        return
    require_auth(credentials)


@router.post("/api/import", dependencies=[Depends(_api_auth)])
async def import_api(request: Request, db: Session = Depends(get_db)):
    raw = (await request.body()).decode("utf-8-sig")
    result, error = _run_import(db, raw)
    if error:
        raise HTTPException(status_code=422, detail=error)
    return result


@router.get("/api/queries", dependencies=[Depends(_api_auth)])
def queries_api(db: Session = Depends(get_db)):
    active = get_active_keyword_set(db)
    return {"queries": [q.text for q in build_queries(active[1])] if active else []}
