from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.classifier import FIELD_LABELS, RELEVANCE_LABELS, Field, PositionText, TenderText, classify
from app.config import PROJECT_ROOT
from app.db import get_db
from app.keywords.loader import KEY_TYPE_LABELS, STRENGTH_LABELS, KeywordSet
from app.services.keywords import (
    KeywordFileRejected,
    activate_keyword_file,
    get_active_keyword_set,
    parse_keyword_file,
    reclassify_all,
)
from app.web.auth import require_auth
from app.web.templating import templates

router = APIRouter(prefix="/keywords", dependencies=[Depends(require_auth)])

UPLOAD_DIR = PROJECT_ROOT / "data" / "uploads"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def _rows(ks: KeywordSet) -> dict[str, list[dict]]:
    def issues_for(sheet: str, row: int) -> list[str]:
        return [i.message for i in ks.issues if i.sheet == sheet and i.row == row]

    return {
        "products": [
            {
                "row": p.row,
                "category": p.category,
                "phrase": p.phrase,
                "mask": p.mask.source,
                "type": KEY_TYPE_LABELS[p.type],
                "priority": p.priority,
                "notes": p.mask.notes,
                "issues": issues_for("Товарные ключи", p.row),
            }
            for p in ks.products
        ],
        "markers": [
            {
                "row": m.row,
                "group": m.group,
                "phrase": m.marker,
                "mask": m.mask.source,
                "priority": m.priority,
                "notes": m.mask.notes,
                "issues": issues_for("Маркеры заказчика", m.row),
            }
            for m in ks.markers
        ],
        "minus": [
            {
                "row": n.row,
                "group": n.group,
                "phrase": n.word,
                "mask": n.mask.source,
                "strength": STRENGTH_LABELS[n.strength],
                "notes": n.mask.notes,
                "issues": issues_for("Минус-слова", n.row),
            }
            for n in ks.minus_words
        ],
        "codes": [
            {
                "row": c.row,
                "code": c.code,
                "classifier": c.classifier,
                "name": c.name,
                "goods": c.goods,
                "priority": c.priority,
                "excluded": c.excluded,
            }
            for c in ks.codes
        ],
    }


@router.get("", response_class=HTMLResponse)
def keywords_page(request: Request, db: Session = Depends(get_db)):
    active = get_active_keyword_set(db)
    version, ks = active if active else (None, None)
    return templates.TemplateResponse(
        request,
        "keywords.html",
        {"version": version, "ks": ks, "rows": _rows(ks) if ks else None, "preview": None},
    )


@router.post("/preview", response_class=HTMLResponse)
async def preview_upload(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    active = get_active_keyword_set(db)
    version, ks_active = active if active else (None, None)
    if len(content) > MAX_UPLOAD_BYTES:
        error = "Файл больше 10 МБ"
        return templates.TemplateResponse(
            request,
            "keywords.html",
            {
                "version": version,
                "ks": ks_active,
                "rows": _rows(ks_active) if ks_active else None,
                "preview": None,
                "upload_error": error,
            },
            status_code=400,
        )
    ks = parse_keyword_file(content)
    token = hashlib.sha256(content).hexdigest()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    (UPLOAD_DIR / f"{token}.xlsx").write_bytes(content)
    return templates.TemplateResponse(
        request,
        "keywords.html",
        {
            "version": version,
            "ks": ks_active,
            "rows": _rows(ks),
            "preview": ks,
            "preview_token": token,
            "preview_filename": Path(file.filename or "keywords.xlsx").name,
        },
    )


@router.post("/activate")
def activate_upload(
    request: Request,
    token: str = Form(...),
    filename: str = Form(...),
    allow_errors: bool = Form(False),
    db: Session = Depends(get_db),
    user: str = Depends(require_auth),
):
    if not all(c in "0123456789abcdef" for c in token) or len(token) != 64:
        return RedirectResponse("/keywords", status_code=303)
    path = UPLOAD_DIR / f"{token}.xlsx"
    if not path.exists():
        return RedirectResponse("/keywords", status_code=303)
    try:
        activate_keyword_file(db, path.read_bytes(), filename, uploaded_by=user, allow_errors=allow_errors)
    except KeywordFileRejected as exc:
        return templates.TemplateResponse(
            request,
            "keywords.html",
            {
                "version": None,
                "ks": None,
                "rows": _rows(exc.keyword_set),
                "preview": exc.keyword_set,
                "preview_token": token,
                "preview_filename": filename,
                "upload_error": str(exc),
            },
            status_code=400,
        )
    path.unlink(missing_ok=True)
    return RedirectResponse("/keywords?activated=1", status_code=303)


@router.post("/reclassify", response_class=HTMLResponse)
def reclassify(request: Request, db: Session = Depends(get_db)):
    active = get_active_keyword_set(db)
    if not active:
        return HTMLResponse('<div class="flash flash-error">Нет активной версии ключевых слов</div>')
    version, ks = active
    stats = reclassify_all(db, version.id, ks)
    return HTMLResponse(
        f'<div class="flash">Пересчитано тендеров: {stats.total} (релевантных {stats.relevant}, '
        f"на проверку {stats.review}, отклонённых {stats.rejected}; изменился статус у {stats.changed})</div>"
    )


@router.post("/try", response_class=HTMLResponse)
def try_classify(
    request: Request,
    title: str = Form(""),
    customer: str = Form(""),
    delivery_place: str = Form(""),
    positions: str = Form(""),
    codes: str = Form(""),
    notice: str = Form(""),
    db: Session = Depends(get_db),
):
    active = get_active_keyword_set(db)
    if not active:
        return HTMLResponse('<div class="flash flash-error">Нет активной версии ключевых слов</div>')
    _, ks = active
    tender = TenderText(
        title=title,
        customer_name=customer,
        delivery_place=delivery_place,
        positions=[PositionText(line.strip()) for line in positions.splitlines() if line.strip()],
        codes=[c.strip() for c in codes.replace(",", " ").split() if c.strip()],
        notice_text=notice,
    )
    result = classify(ks, tender)
    matches = [m.to_dict() for m in result.matches]
    fields = [
        (Field.TITLE, None, tender.title),
        (Field.CUSTOMER, None, tender.customer_name),
        (Field.DELIVERY_PLACE, None, tender.delivery_place),
    ]
    fields += [(Field.POSITIONS, i, p.name) for i, p in enumerate(tender.positions)]
    fields += [(Field.NOTICE, None, tender.notice_text)]
    return templates.TemplateResponse(
        request,
        "_try_result.html",
        {
            "result": result,
            "matches": matches,
            "fields": [f for f in fields if f[2]],
            "relevance_labels": RELEVANCE_LABELS,
            "field_labels": FIELD_LABELS,
        },
    )
